import pytest
from pydantic import SecretStr

from agenthub.core.config import McpRemoteTarget
from agenthub.core.errors import AppError
from agenthub.tools.contracts import check_schema, valid
from agenthub.tools.mcp import configured_endpoint


@pytest.mark.parametrize(
    ("url", "ip"),
    [
        ("http://mcp.example/mcp", "8.8.8.8"),
        ("https://user@mcp.example/mcp", "8.8.8.8"),
        ("https://mcp.example/mcp?q=token", "8.8.8.8"),
        ("https://mcp.example/mcp#x", "8.8.8.8"),
        ("https://mcp.example/mcp", "127.0.0.1"),
        ("https://mcp.example/mcp", "10.0.0.2"),
        ("https://mcp.example/mcp", "169.254.169.254"),
        ("https://mcp.example/mcp", "224.0.0.1"),
        ("https://mcp.example/mcp", "::ffff:127.0.0.1"),
    ],
)
def test_unsafe_target_fails_before_connecting(settings, url, ip):
    settings = settings.model_copy(
        update={
            "environment": "local",
            "mcp_remote_targets": {
                "target": McpRemoteTarget(url=url, ip=ip),
            },
        }
    )
    with pytest.raises(AppError) as error:
        configured_endpoint(settings, "target")
    assert error.value.code == "mcp_target_invalid"


def test_pin_binds_endpoint_and_ip_but_allows_rotation(settings):
    target = McpRemoteTarget(url="https://mcp.example/mcp", ip="8.8.8.8", auth="bearer")

    def pin(value, token="first"):
        return configured_endpoint(
            settings.model_copy(
                update={
                    "mcp_remote_targets": {"target": value},
                    "mcp_remote_tokens": {"target": SecretStr(token)},
                }
            ),
            "target",
        ).fingerprint

    assert pin(target) == pin(target, "second")
    assert pin(target) != pin(target.model_copy(update={"url": "https://mcp.example/other"}))
    assert pin(target) != pin(target.model_copy(update={"auth": "none"}))
    with pytest.raises(AppError) as error:
        pin(target, "")
    assert error.value.code == "mcp_target_invalid"


@pytest.mark.parametrize("keyword", ["$ref", "$dynamicRef", "pattern", "x-mcp-header"])
def test_schema_rejects_remote_references_and_unsafe_keywords(keyword):
    with pytest.raises(ValueError):
        check_schema(
            {"type": "object", "properties": {"name": {keyword: "https://external/schema"}}}
        )


def test_property_names_and_offline_contract_validation():
    schema = {
        "type": "object",
        "properties": {"pattern": {"type": "integer", "maximum": 7}},
        "required": ["pattern"],
        "additionalProperties": False,
    }
    check_schema(schema)
    assert valid(schema, {"pattern": 7})
    assert not valid(schema, {"pattern": 8})
    assert not valid(schema, {"pattern": 7, "extra": True})


def test_tools_body_limit_is_enforced_before_parsing(client):
    response = client.post("/api/v1/tools/add_integers/1/calls", content=b"x" * (140 * 1024))
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "tool_request_too_large"
