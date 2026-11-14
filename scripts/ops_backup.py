"""Capture and restore a stopped Compose application's database and document volume."""

import argparse
import hashlib
import json
import os
import re
import subprocess
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

WRITERS = {"api", "worker", "agent-worker", "evaluation-worker", "migrate"}
DATABASE = "agenthub"
DUMP = "database.dump"
DOCUMENTS = "documents.tar"
MANIFEST = "manifest.json"


class Stack:
    def __init__(self, args: argparse.Namespace) -> None:
        self.project = args.project
        self.command = ["docker", "compose", "--project-name", args.project]
        for name in args.file or ["compose.yaml"]:
            self.command.extend(("--file", name))
        if args.env_file:
            self.command.extend(("--env-file", args.env_file))

    def run(
        self,
        *command: str,
        source: BinaryIO | None = None,
        target: BinaryIO | None = None,
    ) -> str:
        result = subprocess.run(
            [*self.command, *command],
            stdin=source,
            stdout=target if target is not None else subprocess.PIPE,
            check=True,
        )
        return result.stdout.decode().strip() if result.stdout is not None else ""

    def postgres(
        self, *args: str, source: BinaryIO | None = None, target: BinaryIO | None = None
    ) -> str:
        return self.run("exec", "-T", "postgres", *args, source=source, target=target)

    def query(self, sql: str) -> str:
        return self.postgres(
            "psql", "-XAt", "-v", "ON_ERROR_STOP=1", "-U", DATABASE, "-d", DATABASE, "-c", sql
        )

    def files(
        self, *command: str, source: BinaryIO | None = None, target: BinaryIO | None = None
    ) -> str:
        return self.run(
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "--entrypoint",
            command[0],
            "api",
            *command[1:],
            source=source,
            target=target,
        )

    def ensure_quiet(self) -> None:
        running = set(self.run("ps", "--services", "--status", "running").splitlines())
        if running & WRITERS or "postgres" not in running:
            raise ValueError("Stop all application writers and keep PostgreSQL running")
        if (
            self.query(
                "select count(*) from pg_stat_activity where datname=current_database() "
                "and pid <> pg_backend_pid() and backend_type='client backend'"
            )
            != "0"
        ):
            raise ValueError("Close other clients connected to the database")


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def source_hashes(path: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    with tarfile.open(path, "r:") as archive:
        for item in archive:
            if item.name == "." and item.isdir():
                continue
            name = item.name.removeprefix("./")
            match = re.fullmatch(r"([0-9a-f]{32})\.(?:raw|part)", name)
            if match is None or not item.isfile() or name in files:
                raise ValueError("Unexpected document archive member")
            stream = archive.extractfile(item)
            assert stream is not None
            with stream:
                checksum = hashlib.sha256()
                while block := stream.read(1024 * 1024):
                    checksum.update(block)
                files[name] = checksum.hexdigest()
    return files


def verify_sources(stack: Stack, path: Path) -> None:
    files = source_hashes(path)
    for row in stack.query(
        "select replace(id::text, '-', '') || '|' || checksum "
        "from documents where deleted_at is null"
    ).splitlines():
        identifier, expected = row.split("|")
        if files.get(identifier + ".raw") != expected:
            raise ValueError("Document volume differs from PostgreSQL metadata")


def backup(stack: Stack, output: Path) -> None:
    stack.ensure_quiet()
    if output.exists():
        raise ValueError("Backup destination already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent, prefix=".agenthub-backup-") as staging:
        partial = Path(staging) / "backup"
        partial.mkdir(mode=0o700)
        revision = stack.query("select version_num from alembic_version")
        with (partial / DUMP).open("xb") as target:
            stack.postgres("pg_dump", "-Fc", "-U", DATABASE, DATABASE, target=target)
        with (partial / DOCUMENTS).open("xb") as target:
            stack.files("tar", "-C", "/app/data/documents", "-cf", "-", ".", target=target)
        verify_sources(stack, partial / DOCUMENTS)
        stack.ensure_quiet()
        manifest = {
            "format": 1,
            "created_utc": datetime.now(UTC).isoformat(),
            "source_project": stack.project,
            "schema_revision": revision,
            "sha256": {name: digest(partial / name) for name in (DUMP, DOCUMENTS)},
        }
        (partial / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        if output.exists():
            raise ValueError("Backup destination already exists")
        partial.rename(output)


def restore(stack: Stack, source: Path) -> None:
    manifest = json.loads((source / MANIFEST).read_text(encoding="utf-8"))
    if manifest.get("format") != 1 or manifest.get("source_project") == stack.project:
        raise ValueError("Use a distinct destination Compose project")
    for name in (DUMP, DOCUMENTS):
        if digest(source / name) != manifest["sha256"][name]:
            raise ValueError(f"Backup hash mismatch for {name}")
    source_hashes(source / DOCUMENTS)
    stack.ensure_quiet()
    if (
        stack.query(
            "select count(*) from pg_class where relnamespace='public'::regnamespace "
            "and relkind in ('r', 'p', 'v', 'm', 'S', 'f')"
        )
        != "0"
    ):
        raise ValueError("Restore requires an empty destination database")
    if stack.files("find", "/app/data/documents", "-mindepth", "1", "-print", "-quit"):
        raise ValueError("Restore requires an empty document volume")
    with (source / DUMP).open("rb") as stream:
        stack.postgres(
            "pg_restore",
            "--single-transaction",
            "--no-owner",
            "--no-acl",
            "-U",
            DATABASE,
            "-d",
            DATABASE,
            source=stream,
        )
    with (source / DOCUMENTS).open("rb") as stream:
        stack.files(
            "tar",
            "--no-same-owner",
            "--no-same-permissions",
            "-C",
            "/app/data/documents",
            "-xf",
            "-",
            source=stream,
        )
    if stack.query("select version_num from alembic_version") != manifest["schema_revision"]:
        raise ValueError("Restored schema revision does not match the backup")
    with tempfile.TemporaryDirectory(prefix="agenthub-restore-check-") as check:
        path = Path(check) / DOCUMENTS
        with path.open("xb") as target:
            stack.files("tar", "-C", "/app/data/documents", "-cf", "-", ".", target=target)
        if source_hashes(path) != source_hashes(source / DOCUMENTS):
            raise ValueError("Restored document bytes differ from archive")
        verify_sources(stack, path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["backup", "restore"])
    parser.add_argument("directory", type=Path)
    parser.add_argument("--project", required=True)
    parser.add_argument("--file", action="append")
    parser.add_argument("--env-file")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", args.project):
        parser.error("Invalid Compose project name")
    os.umask(0o077)
    try:
        action = backup if args.action == "backup" else restore
        action(Stack(args), args.directory.resolve())
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"{args.action} failed: {error}\n")


if __name__ == "__main__":
    main()
