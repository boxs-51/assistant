from copy import deepcopy

import pytest

from se.src.provider.core.tool_contract import ProviderToolContractError
from se.src.provider.gemini.converters.chats.request import (
    RequestChats as GeminiRequestChats,
)
from se.src.provider.ollama.converters.chat.request import (
    RequestChats as OllamaRequestChats,
)
from se.src.provider.openai.converters.chats.request import (
    RequestChats as OpenAIRequestChats,
)


def _nested_schema() -> dict:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "minLength": 1,
            },
            "filters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 50,
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["limit"],
                "additionalProperties": False,
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }


def _body(parameters: dict) -> dict:
    return {
        "model": "test-model",
        "messages": [{"role": "user", "content": "search"}],
        "tools": [
            {
                "name": "web.search",
                "description": "Search with nested filters",
                "parameters": parameters,
            }
        ],
    }


def test_cross_provider_nested_json_schema_semantics_are_preserved():
    body = _body(_nested_schema())
    original = deepcopy(body)
    expected = _nested_schema()
    expected.pop("$schema")

    openai = OpenAIRequestChats().adapt_chat_request(body)
    gemini = GeminiRequestChats().adapt_chat(body)
    ollama = OllamaRequestChats().adapt_chat_request(body)

    openai_schema = openai["tools"][0]["function"]["parameters"]
    gemini_decl = gemini["tools"][0]["function_declarations"][0]
    ollama_schema = ollama["tools"][0]["function"]["parameters"]

    assert openai_schema == expected
    assert gemini_decl["parametersJsonSchema"] == expected
    assert "parameters" not in gemini_decl
    assert ollama_schema == expected
    assert body == original


def test_legacy_uppercase_type_tokens_normalize_recursively_on_provider_copy():
    legacy_schema = {
        "type": "OBJECT",
        "properties": {
            "count": {"type": "INTEGER", "minimum": 1},
            "items": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "label": {"type": "STRING"},
                    },
                },
            },
        },
        "required": ["count"],
    }
    body = _body(legacy_schema)
    original = deepcopy(body)

    openai = OpenAIRequestChats().adapt_chat_request(body)
    gemini = GeminiRequestChats().adapt_chat(body)
    ollama = OllamaRequestChats().adapt_chat_request(body)

    schemas = [
        openai["tools"][0]["function"]["parameters"],
        gemini["tools"][0]["function_declarations"][0]["parametersJsonSchema"],
        ollama["tools"][0]["function"]["parameters"],
    ]
    for schema in schemas:
        assert schema["type"] == "object"
        assert schema["properties"]["count"]["type"] == "integer"
        items = schema["properties"]["items"]
        assert items["type"] == "array"
        assert items["items"]["type"] == "object"
        assert items["items"]["properties"]["label"]["type"] == "string"

    assert body == original


def test_non_object_parameter_roots_fail_closed_for_all_providers():
    body = _body(
        {
            "type": "array",
            "items": {"type": "string"},
        }
    )

    adapters = [
        ("openai", lambda: OpenAIRequestChats().adapt_chat_request(body)),
        ("gemini", lambda: GeminiRequestChats().adapt_chat(body)),
        ("ollama", lambda: OllamaRequestChats().adapt_chat_request(body)),
    ]
    for provider, lower in adapters:
        with pytest.raises(
            ProviderToolContractError,
            match=rf"{provider} .*root must be type 'object'",
        ):
            lower()


def test_missing_root_type_is_normalized_for_all_providers_without_source_mutation():
    body = _body(
        {
            "properties": {
                "query": {"type": "string"},
            },
            "required": ["query"],
            "additionalProperties": False,
        }
    )
    original = deepcopy(body)

    openai = OpenAIRequestChats().adapt_chat_request(body)
    gemini = GeminiRequestChats().adapt_chat(body)
    ollama = OllamaRequestChats().adapt_chat_request(body)

    schemas = [
        openai["tools"][0]["function"]["parameters"],
        gemini["tools"][0]["function_declarations"][0]["parametersJsonSchema"],
        ollama["tools"][0]["function"]["parameters"],
    ]
    for schema in schemas:
        assert schema["type"] == "object"
        assert schema["properties"]["query"]["type"] == "string"
        assert schema["additionalProperties"] is False

    assert body == original


def test_empty_parameter_schema_is_bounded_to_empty_object_for_all_providers():
    body = _body({})
    original = deepcopy(body)

    openai = OpenAIRequestChats().adapt_chat_request(body)
    gemini = GeminiRequestChats().adapt_chat(body)
    ollama = OllamaRequestChats().adapt_chat_request(body)

    schemas = [
        openai["tools"][0]["function"]["parameters"],
        gemini["tools"][0]["function_declarations"][0]["parametersJsonSchema"],
        ollama["tools"][0]["function"]["parameters"],
    ]
    assert schemas == [
        {"type": "object", "properties": {}},
        {"type": "object", "properties": {}},
        {"type": "object", "properties": {}},
    ]
    assert body == original
