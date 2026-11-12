"""Bounded JSON and offline validation of reviewed MCP contracts."""

import hashlib
import json
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry

ARGUMENT_BYTES = 64 * 1024
RESULT_BYTES = 256 * 1024
SCHEMA_BYTES = 16 * 1024


def bounded_json(value: Any, limit: int) -> bytes:
    pending = [(value, 0)]
    nodes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if depth > 24 or nodes > 16_000:
            raise ValueError("JSON nesting or item limit exceeded")
        if isinstance(item, dict):
            pending.extend((key, depth + 1) for key in item)
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
        elif isinstance(item, str) and (len(item) > limit or "\x00" in item):
            raise ValueError("Invalid JSON string")
    payload = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True).encode("utf-8")
    if len(payload) > limit:
        raise ValueError("JSON byte limit exceeded")
    return payload


def digest(value: Any, limit: int) -> str:
    return hashlib.sha256(bounded_json(value, limit)).hexdigest()


def check_schema(schema: dict[str, Any]) -> None:
    if schema.get("type") != "object":
        raise ValueError("Tool schemas must describe JSON objects")
    bounded_json(schema, SCHEMA_BYTES)
    Draft202012Validator.check_schema(schema)

    # Walk schema nodes, keeping user property names separate from schema keywords.
    pending = [schema]
    while pending:
        node = pending.pop()
        if {
            "$ref",
            "$dynamicRef",
            "$recursiveRef",
            "$id",
            "$vocabulary",
            "pattern",
            "patternProperties",
            "x-mcp-header",
        }.intersection(node):
            raise ValueError("Unsupported tool schema keyword")
        if (
            node.get("$schema", Draft202012Validator.META_SCHEMA["$id"])
            != (Draft202012Validator.META_SCHEMA["$id"])
        ):
            raise ValueError("Unsupported tool schema dialect")
        for key in ("properties", "$defs", "dependentSchemas"):
            pending.extend(child for child in node.get(key, {}).values() if isinstance(child, dict))
        for key in ("allOf", "anyOf", "oneOf", "prefixItems"):
            pending.extend(child for child in node.get(key, []) if isinstance(child, dict))
        for key in (
            "additionalProperties",
            "unevaluatedProperties",
            "propertyNames",
            "contains",
            "items",
            "unevaluatedItems",
            "not",
            "if",
            "then",
            "else",
            "contentSchema",
        ):
            child = node.get(key)
            if isinstance(child, dict):
                pending.append(child)


def valid(schema: dict[str, Any], value: dict[str, Any]) -> bool:
    return Draft202012Validator(schema, registry=Registry()).is_valid(value)
