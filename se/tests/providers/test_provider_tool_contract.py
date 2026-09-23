from __future__ import annotations

import re

import pytest

from se.src.domain.schemas.tool import GatewayToolDefinition
from se.src.provider.core.tool_contract import (
    ProviderToolContractError,
    build_provider_tool_name_map,
    lower_provider_tools,
    normalize_provider_tool_schema,
    provider_tool_name_rule,
)


def test_openai_dotted_logical_id_gets_reversible_safe_alias():
    names = build_provider_tool_name_map("openai", ["web.search"])

    alias = names.provider_name("web.search")
    assert alias != "web.search"
    assert re.fullmatch(r"[A-Za-z0-9_-]+", alias)
    assert len(alias) <= 64
    assert names.logical_name(alias) == "web.search"


def test_gemini_dotted_logical_id_gets_reversible_safe_alias():
    names = build_provider_tool_name_map("gemini", ["web.search"])

    alias = names.provider_name("web.search")
    assert alias != "web.search"
    assert re.fullmatch(r"[A-Za-z0-9_-]+", alias)
    assert len(alias) <= 128
    assert names.logical_name(alias) == "web.search"


@pytest.mark.parametrize("provider", ["openai", "gemini"])
def test_provider_safe_name_stays_unchanged(provider):
    names = build_provider_tool_name_map(provider, ["web_search-v2"])

    assert names.provider_name("web_search-v2") == "web_search-v2"
    assert names.logical_name("web_search-v2") == "web_search-v2"


def test_ollama_ptc1_keeps_logical_name_identity():
    names = build_provider_tool_name_map("ollama", ["web.search"])

    assert names.provider_name("web.search") == "web.search"
    assert names.logical_name("web.search") == "web.search"


def test_aliases_are_deterministic_and_order_independent():
    left = build_provider_tool_name_map("openai", ["web.search", "web.read"])
    right = build_provider_tool_name_map("openai", ["web.read", "web.search"])

    assert dict(left.logical_to_provider) == dict(right.logical_to_provider)


def test_long_openai_name_is_bounded_and_reversible():
    logical_name = "namespace." + ("very_long_component_" * 8)
    names = build_provider_tool_name_map("openai", [logical_name])

    alias = names.provider_name(logical_name)
    assert len(alias) <= provider_tool_name_rule("openai").max_length
    assert names.logical_name(alias) == logical_name


def test_duplicate_logical_names_fail_closed():
    with pytest.raises(ProviderToolContractError, match="duplicate logical tool name"):
        build_provider_tool_name_map("openai", ["web.search", "web.search"])


def test_generated_alias_collision_with_safe_name_fails_closed():
    first = build_provider_tool_name_map("openai", ["web.search"])
    generated_alias = first.provider_name("web.search")

    with pytest.raises(ProviderToolContractError, match="alias collision"):
        build_provider_tool_name_map("openai", [generated_alias, "web.search"])


def test_unknown_name_decode_and_encode_fail_closed():
    names = build_provider_tool_name_map("openai", ["web.search"])

    with pytest.raises(ProviderToolContractError, match="unknown logical tool name"):
        names.provider_name("missing.tool")
    with pytest.raises(ProviderToolContractError, match="unknown provider tool name"):
        names.logical_name("missing_tool")


def test_unsupported_provider_fails_closed():
    with pytest.raises(ProviderToolContractError, match="unsupported provider"):
        build_provider_tool_name_map("unknown-provider", ["web.search"])


def test_schema_normalization_is_deep_copy_and_preserves_constraints():
    source = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "enum": ["a", "b"],
                "minLength": 1,
            },
            "nested": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "minimum": 1}},
                "required": ["limit"],
                "additionalProperties": False,
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    normalized = normalize_provider_tool_schema("gemini", source)

    assert "$schema" not in normalized
    assert normalized["required"] == ["query"]
    assert normalized["additionalProperties"] is False
    assert normalized["properties"]["nested"]["required"] == ["limit"]
    assert normalized["properties"]["nested"]["additionalProperties"] is False
    assert source["$schema"].startswith("https://")

    normalized["properties"]["nested"]["properties"]["limit"]["minimum"] = 9
    assert source["properties"]["nested"]["properties"]["limit"]["minimum"] == 1


def test_lower_provider_tools_preserves_gateway_definition_and_logical_identity():
    tool = GatewayToolDefinition(
        name="web.search",
        description="Search the web",
        parameters={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {"q": {"type": "string"}},
            "required": ["q"],
            "additionalProperties": False,
        },
    )
    original = tool.model_dump(mode="python")

    contract = lower_provider_tools("openai", [tool])

    assert contract.provider == "openai"
    assert len(contract.tools) == 1
    lowered = contract.tools[0]
    assert lowered.logical_name == "web.search"
    assert lowered.provider_name != lowered.logical_name
    assert contract.names.logical_name(lowered.provider_name) == "web.search"
    assert lowered.parameters["required"] == ["q"]
    assert "$schema" not in lowered.parameters
    assert tool.model_dump(mode="python") == original
