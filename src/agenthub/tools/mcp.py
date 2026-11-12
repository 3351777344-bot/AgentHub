"""MCP discovery and one-shot calls over pinned Streamable HTTP transports."""

import asyncio
import ipaddress
import re
import ssl
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx2
from jsonschema.exceptions import SchemaError
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, Tool
from pydantic import SecretStr, ValidationError

from agenthub.core.config import Settings
from agenthub.core.errors import AppError
from agenthub.tools.contracts import RESULT_BYTES, bounded_json, check_schema, digest
from agenthub.tools.schemas import DiscoveredTool

PROTOCOL_VERSION = "2026-07-28"
LOCAL_KEY = "local"
DISCOVERY_TIMEOUT = 10
WIRE_BYTES = 512 * 1024


@dataclass(frozen=True)
class Endpoint:
    transport: str
    url: httpx2.URL
    address: str
    token: SecretStr = field(default_factory=lambda: SecretStr(""), repr=False)

    @property
    def fingerprint(self) -> str:
        auth = "bearer" if self.token.get_secret_value() else "none"
        return digest([self.transport, str(self.url), self.address, auth], 8192)


def configured_endpoint(settings: Settings, key: str) -> Endpoint:
    if key == LOCAL_KEY and settings.mcp_local_socket is not None:
        return Endpoint(
            "unix_http", httpx2.URL("http://localhost/mcp"), str(settings.mcp_local_socket)
        )
    target = settings.mcp_remote_targets.get(key) if key != LOCAL_KEY else None
    if target is None:
        raise AppError(404, "mcp_target_not_configured", "MCP deployment target is not configured")
    try:
        url = httpx2.URL(target.url)
        if (
            url.scheme != "https"
            or not re.fullmatch(r"[a-z0-9.-]+", url.host)
            or not re.fullmatch(r"/[A-Za-z0-9._~/-]+", url.path)
            or url.userinfo
            or url.query
            or url.fragment
            or url.raw_path != url.path.encode("ascii")
            or any(character in target.url for character in "?#@\\")
        ):
            raise ValueError
        ip = target.ip
        if (
            not isinstance(ip, ipaddress.IPv4Address)
            or ip.is_multicast
            or not (ip.is_global or (settings.environment == "test" and ip.is_loopback))
        ):
            raise ValueError
        credential = SecretStr("")
        if target.auth == "bearer":
            credential = settings.mcp_remote_tokens.get(key, credential)
            token = credential.get_secret_value()
            if len(token) > 4096 or not re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", token):
                raise ValueError
    except (ValueError, httpx2.InvalidURL):
        raise AppError(422, "mcp_target_invalid", "Invalid MCP deployment target") from None
    return Endpoint("https", url, str(ip), credential)


class BoundedStream(httpx2.AsyncByteStream):
    def __init__(self, stream: httpx2.AsyncByteStream) -> None:
        self.stream = stream

    async def __aiter__(self) -> AsyncIterator[bytes]:
        size = 0
        async for chunk in self.stream:
            size += len(chunk)
            if size > WIRE_BYTES:
                raise httpx2.RemoteProtocolError("MCP response exceeds the byte limit")
            yield chunk

    async def aclose(self) -> None:
        await self.stream.aclose()


class PinnedTransport(httpx2.AsyncHTTPTransport):
    def __init__(self, endpoint: Endpoint, settings: Settings) -> None:
        self.endpoint = endpoint
        if endpoint.transport == "unix_http":
            super().__init__(uds=endpoint.address, trust_env=False)
        else:
            verify = ssl.create_default_context(cafile=settings.mcp_remote_ca_file)
            super().__init__(verify=verify, trust_env=False, retries=0)

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        endpoint = self.endpoint
        if request.url != endpoint.url:
            return httpx2.Response(502, content=b"")
        if endpoint.transport == "https":
            # TCP goes directly to the approved IP. Preserve the original Host, TLS SNI
            # and certificate identity. The client owns a pool for this one endpoint only.
            request.url = endpoint.url.copy_with(host=endpoint.address)
            request.extensions["sni_hostname"] = endpoint.url.host
        try:
            response = await super().handle_async_request(request)
        except httpx2.TransportError:
            # Give the SDK an HTTP failure it can correlate with the outstanding request.
            return httpx2.Response(502, content=b"")
        finally:
            request.url = endpoint.url
        # The SDK follows some redirects on its own. Block them at the wire boundary.
        length = response.headers.get("content-length", "")
        if (
            300 <= response.status_code < 400
            or response.headers.get("content-encoding", "identity").lower() != "identity"
            or len(length) > 20
            or (length.isascii() and length.isdecimal() and int(length) > WIRE_BYTES)
        ):
            await response.aclose()
            return httpx2.Response(502, content=b"")
        assert isinstance(response.stream, httpx2.AsyncByteStream)
        response.stream = BoundedStream(response.stream)
        return response


def translated_failure(error: Exception) -> AppError:
    if isinstance(error, BaseExceptionGroup):
        if len(error.exceptions) == 1 and isinstance(error.exceptions[0], Exception):
            return translated_failure(error.exceptions[0])
    if isinstance(error, AppError):
        return error
    if isinstance(error, TimeoutError | httpx2.TimeoutException):
        return AppError(504, "tool_timeout", "Tool call timed out")
    return AppError(
        502, "mcp_unavailable", "MCP service returned an invalid response or is unavailable"
    )


@asynccontextmanager
async def connected(
    settings: Settings, endpoint: Endpoint, timeout_seconds: int
) -> AsyncIterator[Client]:
    token = endpoint.token.get_secret_value()
    headers = {"Accept-Encoding": "identity"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with (
            asyncio.timeout(timeout_seconds),
            httpx2.AsyncClient(
                transport=PinnedTransport(endpoint, settings),
                headers=headers,
                timeout=None,
                trust_env=False,
            ) as http,
            Client(
                streamable_http_client(
                    str(endpoint.url),
                    http_client=http,
                    terminate_on_close=False,
                    max_sse_event_size=WIRE_BYTES,
                ),
                mode=PROTOCOL_VERSION,
                cache=None,
            ) as client,
        ):
            yield client
    except Exception as error:
        failure = translated_failure(error)
        raise failure from None


async def tools_on(client: Client) -> list[Tool]:
    found: dict[str, Tool] = {}
    cursor = None
    for _ in range(10):
        page = await client.list_tools(cursor=cursor)
        for tool in page.tools:
            if tool.name in found or len(found) >= 100:
                raise AppError(502, "mcp_invalid_catalog", "MCP tool catalog exceeded its limits")
            found[tool.name] = tool
        cursor = page.next_cursor
        if cursor is None:
            return list(found.values())
    raise AppError(502, "mcp_invalid_catalog", "MCP tool catalog exceeded its limits")


def describe(tool: Tool) -> DiscoveredTool:
    try:
        if tool.output_schema is None:
            raise ValueError("Structured output schema is required")
        check_schema(tool.input_schema)
        check_schema(tool.output_schema)
        fingerprint = digest(tool.model_dump(mode="json", exclude_none=True), RESULT_BYTES)
        return DiscoveredTool(
            name=tool.name,
            remote_description=tool.description,
            read_only_hint=tool.annotations.read_only_hint if tool.annotations else None,
            destructive_hint=tool.annotations.destructive_hint if tool.annotations else None,
            input_schema=tool.input_schema,
            output_schema=tool.output_schema,
            definition_digest=fingerprint,
        )
    except (ValueError, SchemaError, RecursionError):
        raise AppError(422, "mcp_schema_unsupported", "MCP tool schema is unsupported") from None


async def discover(settings: Settings, endpoint: Endpoint) -> list[DiscoveredTool]:
    async with connected(settings, endpoint, DISCOVERY_TIMEOUT) as client:
        result = []
        for tool in await tools_on(client):
            try:
                result.append(describe(tool))
            except AppError:
                # Other tools on a shared server can use unsupported contracts.
                continue
        return result


async def call(
    settings: Settings,
    endpoint: Endpoint,
    remote_name: str,
    fingerprint: str,
    arguments: dict[str, Any],
    timeout_seconds: int,
) -> dict[str, Any]:
    async with connected(settings, endpoint, timeout_seconds) as client:
        remote_tool = next(
            (tool for tool in await tools_on(client) if tool.name == remote_name), None
        )
        try:
            current = describe(remote_tool).definition_digest if remote_tool is not None else None
        except AppError:
            current = None
        if current != fingerprint:
            raise AppError(
                409, "mcp_definition_changed", "MCP tool definition changed; review it again"
            )

        try:
            # The low-level session sends once. The high-level SDK call can re-list and retry.
            result = await client.session.call_tool(remote_name, arguments)
        except (RuntimeError, ValidationError):
            raise AppError(
                502, "tool_invalid_result", "MCP returned an invalid tool result"
            ) from None
        if not isinstance(result, CallToolResult):
            raise AppError(502, "tool_invalid_result", "MCP returned an invalid tool result")
        if result.is_error:
            raise AppError(502, "mcp_tool_failed", "MCP tool reported an execution failure")
        if not isinstance(result.structured_content, dict):
            raise AppError(502, "tool_invalid_result", "MCP returned an invalid tool result")
        try:
            bounded_json(result.structured_content, RESULT_BYTES)
        except ValueError:
            raise AppError(
                502, "tool_invalid_result", "MCP returned an invalid tool result"
            ) from None
        return result.structured_content
