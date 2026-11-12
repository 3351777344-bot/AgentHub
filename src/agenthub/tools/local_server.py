"""Bundled MCP service for the networkless Compose container."""

from typing import Annotated

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field
from starlette.applications import Starlette

from agenthub.tools.schemas import IntegerTotal


def create_app() -> Starlette:
    server = MCPServer("AgentHub local tools")

    @server.tool()
    def multiply_integers(
        left: Annotated[int, Field(ge=-1_000_000, le=1_000_000)],
        right: Annotated[int, Field(ge=-1_000_000, le=1_000_000)],
    ) -> IntegerTotal:
        """Multiply two bounded integers."""
        return IntegerTotal(total=left * right)

    return server.streamable_http_app(
        json_response=True,
        stateless_http=True,
        transport_security=TransportSecuritySettings(allowed_hosts=["localhost"]),
    )
