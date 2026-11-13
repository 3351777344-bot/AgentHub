"""Built-in definitions and administrator-reviewed MCP registrations."""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from agenthub.core.config import Settings
from agenthub.core.errors import AppError
from agenthub.identity import service as identity
from agenthub.rag.schemas import AskRequest, SearchOutput
from agenthub.tools import mcp
from agenthub.tools.models import McpService, ToolRegistration
from agenthub.tools.schemas import AddInput, DiscoveredTool, IntegerTotal, ToolDescription
from agenthub.tools.schemas import ToolRegistration as RegistrationInput

BUILTINS = {
    ("add_integers", 1): ToolDescription(
        name="add_integers",
        version=1,
        source="builtin",
        description="Add two bounded integers.",
        required_role="member",
        effect="read_only",
        timeout_seconds=2,
        input_schema=AddInput.model_json_schema(),
        output_schema=IntegerTotal.model_json_schema(),
    ),
    ("knowledge_search", 1): ToolDescription(
        name="knowledge_search",
        version=1,
        source="builtin",
        description="Search the caller's private knowledge base for relevant passages.",
        required_role="member",
        effect="read_only",
        timeout_seconds=60,
        input_schema=AskRequest.model_json_schema(),
        output_schema=SearchOutput.model_json_schema(),
    ),
}
BUILTIN_NAMES = {name for name, _version in BUILTINS}


def get_service(session: Session, key: str, *, lock: bool = False) -> McpService:
    service = session.get(McpService, key, with_for_update=lock)
    if service is None:
        raise AppError(404, "mcp_service_not_found", "MCP service not found")
    return service


def endpoint_for(settings: Settings, service: McpService) -> mcp.Endpoint:
    if not service.enabled:
        raise AppError(403, "mcp_service_disabled", "MCP service is disabled")
    try:
        endpoint = mcp.configured_endpoint(settings, service.key)
    except AppError:
        raise AppError(
            409, "mcp_target_changed", "MCP deployment target changed or was removed"
        ) from None
    if endpoint.fingerprint != service.target_digest:
        raise AppError(409, "mcp_target_changed", "MCP deployment target changed or was removed")
    return endpoint


def create_service(
    session: Session, settings: Settings, key: str, principal: identity.Principal, request_id: str
) -> McpService:
    endpoint = mcp.configured_endpoint(settings, key)
    identity.lock_admin_operation(session, principal)
    service = McpService(
        key=key, transport=endpoint.transport, target_digest=endpoint.fingerprint, enabled=True
    )
    session.add(service)
    identity.audit(session, "mcp.service.created", request_id, principal.user.id)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise AppError(409, "mcp_service_exists", "MCP service already exists") from None
    return service


def update_service(
    session: Session,
    settings: Settings,
    key: str,
    enabled: bool,
    principal: identity.Principal,
    request_id: str,
) -> McpService:
    identity.lock_admin_operation(session, principal)
    service = get_service(session, key, lock=True)
    service.enabled = enabled
    if enabled:
        endpoint_for(settings, service)
    identity.audit(session, "mcp.service.updated", request_id, principal.user.id)
    session.commit()
    return service


def register_tool(
    session: Session,
    settings: Settings,
    data: RegistrationInput,
    discovered: DiscoveredTool,
    endpoint: mcp.Endpoint,
    principal: identity.Principal,
    request_id: str,
) -> ToolRegistration:
    if data.effect == "read_only" and (
        discovered.read_only_hint is False or discovered.destructive_hint is True
    ):
        raise AppError(403, "tool_effect_blocked", "MCP tool declares side effects")
    identity.lock_admin_operation(session, principal)
    service = get_service(session, data.service_key, lock=True)
    if endpoint_for(settings, service).fingerprint != endpoint.fingerprint:
        raise AppError(409, "mcp_target_changed", "MCP deployment target changed or was removed")
    row = ToolRegistration(
        **data.model_dump(),
        input_schema=discovered.input_schema,
        output_schema=discovered.output_schema,
        enabled=data.effect == "read_only",
    )
    session.add(row)
    identity.audit(session, "tools.registration.created", request_id, principal.user.id)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise AppError(409, "tool_version_exists", "Tool version already exists") from None
    return row


def update_tool(
    session: Session,
    name: str,
    version: int,
    enabled: bool,
    principal: identity.Principal,
    request_id: str,
) -> ToolRegistration:
    identity.lock_admin_operation(session, principal)
    row = session.get(ToolRegistration, (name, version), with_for_update=True)
    if row is None:
        raise AppError(404, "tool_not_found", "Tool version not found")
    if enabled and row.effect != "read_only":
        raise AppError(403, "tool_effect_blocked", "Tool side effects are blocked")
    row.enabled = enabled
    identity.audit(session, "tools.registration.updated", request_id, principal.user.id)
    session.commit()
    return row


def describe(row: ToolRegistration) -> ToolDescription:
    return ToolDescription.model_validate(row)


def update_agent_execution(
    session: Session,
    name: str,
    version: int,
    enabled: bool,
    principal: identity.Principal,
    request_id: str,
) -> ToolRegistration:
    identity.lock_admin_operation(session, principal)
    row = session.get(ToolRegistration, (name, version), with_for_update=True)
    if row is None:
        raise AppError(404, "tool_not_found", "Tool version not found")
    if row.effect == "read_only":
        raise AppError(409, "tool_effect_conflict", "Read-only tools use the standard tool setting")
    row.agent_enabled = enabled
    identity.audit(session, "tools.agent_execution.updated", request_id, principal.user.id)
    session.commit()
    return row


def catalog(session: Session, role: str, limit: int, offset: int) -> list[ToolDescription]:
    builtins = list(BUILTINS.values())
    page = builtins[offset : offset + limit]
    if len(page) == limit:
        return page
    query = (
        select(ToolRegistration)
        .join(McpService)
        .where(ToolRegistration.enabled, McpService.enabled, ToolRegistration.effect == "read_only")
        .order_by(ToolRegistration.name, ToolRegistration.version)
        .offset(max(0, offset - len(builtins)))
        .limit(limit - len(page))
    )
    if role != "admin":
        query = query.where(ToolRegistration.required_role == "member")
    return page + [describe(row) for row in session.scalars(query)]
