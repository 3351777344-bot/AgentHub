"""Real SDK server launched by the HTTPS integration tests."""

import asyncio
import os
from pathlib import Path
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse, Response

from agenthub.tools.schemas import IntegerTotal


def create_app() -> Starlette:
    variant = os.environ["P6_FIXTURE_VARIANT"]
    marker = Path(os.environ["P6_FIXTURE_MARKER"])
    token = os.environ["P6_FIXTURE_TOKEN"]
    host = os.environ["P6_FIXTURE_HOST"]
    server = MCPServer("AgentHub HTTPS acceptance")
    upper = 20 if variant == "drift" else 10

    def record(value: str) -> None:
        with marker.open("a", encoding="ascii") as file:
            file.write(value + "\n")

    @server.tool()
    async def remote_add(
        left: Annotated[int, Field(ge=0, le=upper)],
        right: Annotated[int, Field(ge=0, le=10)],
    ) -> IntegerTotal:
        await asyncio.to_thread(record, "call")
        if left == 7:
            await asyncio.sleep(4)
        if left == 8:
            raise ValueError("private upstream error marker")
        return IntegerTotal(total=left + right)

    @server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True))
    def remote_write() -> IntegerTotal:
        record("unexpected write")
        return IntegerTotal(total=0)

    app = server.streamable_http_app(
        json_response=True,
        stateless_http=True,
        transport_security=TransportSecuritySettings(allowed_hosts=[host]),
    )

    async def check(request: Request, call_next) -> Response:
        if request.headers.get("host") != host:
            return PlainTextResponse("Wrong Host", status_code=400)
        if request.headers.get("authorization") != f"Bearer {token}":
            return PlainTextResponse("Unauthorized", status_code=401)
        if request.url.path == "/redirected":
            await asyncio.to_thread(record, "redirect")
            return PlainTextResponse("Redirect followed", status_code=500)
        if variant == "redirect":
            return RedirectResponse("/redirected", status_code=307)
        return await call_next(request)

    app.add_middleware(BaseHTTPMiddleware, dispatch=check)
    return app
