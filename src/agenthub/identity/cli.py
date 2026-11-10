"""Run: uv run python -m agenthub.identity.cli init-admin --username YOUR_NAME."""

import argparse
import getpass
import sys

from pydantic import SecretStr, ValidationError
from sqlalchemy.exc import SQLAlchemyError

from agenthub.core.config import Settings
from agenthub.core.errors import AppError
from agenthub.db.session import create_database_engine, create_session_factory
from agenthub.identity.schemas import CreateUserInput
from agenthub.identity.service import bootstrap_admin


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize the first AgentHub administrator")
    parser.add_argument("command", choices=["init-admin"])
    parser.add_argument("--username", required=True)
    parser.add_argument(
        "--password-stdin", action="store_true", help="Read one password line from stdin"
    )
    args = parser.parse_args()
    if args.password_stdin:
        password = sys.stdin.readline().rstrip("\r\n")
    else:
        password = getpass.getpass("Password (12-128 characters): ")
        if password != getpass.getpass("Confirm password: "):
            print("Passwords do not match.", file=sys.stderr)
            return 1
    try:
        data = CreateUserInput(username=args.username, password=SecretStr(password), role="admin")
        engine = create_database_engine(Settings())
        try:
            with create_session_factory(engine)() as session:
                bootstrap_admin(session, data)
        finally:
            engine.dispose()
    except ValidationError:
        print(
            "Invalid configuration, username or password (12-128 nonblank characters).",
            file=sys.stderr,
        )
        return 1
    except AppError as exc:
        print(exc.message, file=sys.stderr)
        return 1
    except SQLAlchemyError:
        print(
            "Database operation failed. Check connectivity and run Alembic upgrade head.",
            file=sys.stderr,
        )
        return 1
    print("Administrator initialized. Sign in through /api/v1/auth/login.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
