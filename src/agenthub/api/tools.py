"""Tool catalog, administrator registration and caller-scoped execution audit."""

from typing import Annotated

from fastapi import APIRouter, Query, Request
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from agenthub.core.errors import AppError, ErrorResponse
from agenthub.identity.dependencies import AdminPrincipal, CurrentPrincipal, DatabaseSession
from agenthub.tools import executor, mcp, registry
from agenthub.tools.models import McpService, ToolCall
from agenthub.tools.models import ToolRegistration as ToolRow
from agenthub.tools.schemas import (
    CallAudit,
    CallInput,
    CallOutput,
    DiscoveredTool,
    EnabledUpdate,
    RegisteredTool,
    ServiceCreate,
    ServiceKey,
    ServiceOutput,
    ToolDescription,
    ToolName,
    ToolRegistration,
    Version,
)

router = APIRouter(
    prefix="/api/v1",
    tags=["tools"],
    responses={code: {"model": ErrorResponse} for code in (401, 403, 404, 409, 422, 502, 503, 504)},
)
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=100000)]


@router.patch("/tool-registrations/{name}/{version}/agent-execution", response_model=RegisteredTool)
def update_agent_execution(
    name: ToolName,
    version: Version,
    data: EnabledUpdate,
    request: Request,
    session: DatabaseSession,
    principal: AdminPrincipal,
) -> ToolRow:
    return registry.update_agent_execution(
        session, name, version, data.enabled, principal, request.state.request_id
    )


@router.get("/tools", response_model=list[ToolDescription])
def list_tools(
    session: DatabaseSession,
    principal: CurrentPrincipal,
    limit: Limit = 20,
    offset: Offset = 0,
    for_agent: bool = False,
) -> list[ToolDescription]:
    return registry.catalog(session, principal.user.role_name, limit, offset, for_agent=for_agent)


@router.post("/tools/{name}/{version}/calls", response_model=CallOutput)
async def call_tool(
    name: ToolName,
    version: Version,
    data: CallInput,
    request: Request,
    session: DatabaseSession,
    principal: CurrentPrincipal,
) -> CallOutput:
    actor_id = principal.user.id
    await run_in_threadpool(session.rollback)
    return await executor.execute(
        request.app.state.session_factory,
        request.app.state.settings,
        request.app.state.embedding_provider,
        actor_id,
        request.state.request_id,
        name,
        version,
        data.arguments,
    )


@router.get("/tool-calls", response_model=list[CallAudit])
def list_calls(
    session: DatabaseSession,
    principal: CurrentPrincipal,
    request_id: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    limit: Limit = 20,
    offset: Offset = 0,
) -> list[ToolCall]:
    query = select(ToolCall).where(ToolCall.actor_id == principal.user.id)
    if request_id is not None:
        query = query.where(ToolCall.request_id == request_id)
    return list(
        session.scalars(
            query.order_by(ToolCall.created_at.desc(), ToolCall.id).limit(limit).offset(offset)
        )
    )


@router.get("/mcp/services", response_model=list[ServiceOutput])
def list_services(session: DatabaseSession, principal: AdminPrincipal) -> list[McpService]:
    return list(session.scalars(select(McpService).order_by(McpService.key)))


@router.post("/mcp/services", response_model=ServiceOutput, status_code=201)
def create_service(
    data: ServiceCreate,
    request: Request,
    session: DatabaseSession,
    principal: AdminPrincipal,
) -> McpService:
    return registry.create_service(
        session, request.app.state.settings, data.key, principal, request.state.request_id
    )


@router.patch("/mcp/services/{key}", response_model=ServiceOutput)
def update_service(
    key: ServiceKey,
    data: EnabledUpdate,
    request: Request,
    session: DatabaseSession,
    principal: AdminPrincipal,
) -> McpService:
    return registry.update_service(
        session, request.app.state.settings, key, data.enabled, principal, request.state.request_id
    )


@router.get("/mcp/services/{key}/tools", response_model=list[DiscoveredTool])
async def discover_tools(
    key: ServiceKey,
    request: Request,
    session: DatabaseSession,
    principal: AdminPrincipal,
) -> list[DiscoveredTool]:
    settings = request.app.state.settings

    def load_endpoint() -> mcp.Endpoint:
        try:
            return registry.endpoint_for(settings, registry.get_service(session, key))
        finally:
            session.rollback()

    endpoint = await run_in_threadpool(load_endpoint)
    return await mcp.discover(settings, endpoint)


@router.get("/tool-registrations", response_model=list[RegisteredTool])
def list_registrations(
    session: DatabaseSession,
    principal: AdminPrincipal,
    limit: Limit = 20,
    offset: Offset = 0,
) -> list[ToolRow]:
    return list(
        session.scalars(
            select(ToolRow).order_by(ToolRow.name, ToolRow.version).limit(limit).offset(offset)
        )
    )


@router.post("/tool-registrations", response_model=RegisteredTool, status_code=201)
async def register_tool(
    data: ToolRegistration,
    request: Request,
    session: DatabaseSession,
    principal: AdminPrincipal,
) -> ToolRow:
    if data.name in registry.BUILTIN_NAMES:
        raise AppError(409, "tool_name_reserved", "Built-in tool names are reserved")
    settings = request.app.state.settings

    def load_endpoint() -> mcp.Endpoint:
        try:
            return registry.endpoint_for(settings, registry.get_service(session, data.service_key))
        finally:
            session.rollback()

    endpoint = await run_in_threadpool(load_endpoint)
    catalog = await mcp.discover(settings, endpoint)
    discovered = next((item for item in catalog if item.name == data.remote_name), None)
    if discovered is None or discovered.definition_digest != data.definition_digest:
        raise AppError(
            409, "mcp_definition_changed", "MCP tool definition changed; review it again"
        )
    return await run_in_threadpool(
        registry.register_tool,
        session,
        settings,
        data,
        discovered,
        endpoint,
        principal,
        request.state.request_id,
    )


@router.patch("/tool-registrations/{name}/{version}", response_model=RegisteredTool)
def update_tool(
    name: ToolName,
    version: Version,
    data: EnabledUpdate,
    request: Request,
    session: DatabaseSession,
    principal: AdminPrincipal,
) -> ToolRow:
    return registry.update_tool(
        session, name, version, data.enabled, principal, request.state.request_id
    )
