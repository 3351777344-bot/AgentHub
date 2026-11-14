"""Serve the same-origin console from the installed Python package."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

_DIRECTORY = Path(__file__).resolve().parent
_PAGE_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
        "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


def attach_console(app: FastAPI) -> None:
    @app.get("/", include_in_schema=False)
    def console() -> FileResponse:
        return FileResponse(_DIRECTORY / "index.html", headers=_PAGE_HEADERS)

    app.mount(
        "/console-assets", StaticFiles(directory=_DIRECTORY / "assets"), name="console-assets"
    )
