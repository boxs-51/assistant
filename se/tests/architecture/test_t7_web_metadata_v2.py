from __future__ import annotations

import sys
import textwrap

import pytest

from tools.v1._shared.contracts import tool_result_schema
from se.src.application.policy.authorization import AuthorizationService
from se.src.domain.schemas.identity import Identity
from se.src.runtimes.agent.tool_execution.validator import (
    JsonSchemaToolArgumentValidator,
)
from se.src.runtimes.capability.catalog import CapabilityCatalog
from se.src.runtimes.capability.contracts.context import CapabilityExecutionContext
from se.src.runtimes.capability.contracts.definition import CapabilityDefinition
from se.src.runtimes.capability.drivers.python_driver import PythonCapabilityDriver
from se.src.runtimes.capability.local_tool_loader import register_local_tools
from se.src.runtimes.capability.policy import CapabilityRoutingPolicy
from se.src.runtimes.capability.registry import CapabilityRegistry
from se.src.runtimes.capability.runtime import CapabilityRuntime
from se.src.tool.registry import ToolRegistry


def _runtime_and_tools():
    runtime = CapabilityRuntime(
        registry=CapabilityRegistry(),
        authorization=AuthorizationService(),
        catalog=CapabilityCatalog(),
        routing_policy=CapabilityRoutingPolicy(),
    )
    return runtime, ToolRegistry(runtime.registry)


def _write_module(tmp_path, name: str, source: str):
    path = tmp_path / f"{name}.py"
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    return path


def _v2_source(
    *,
    third_binding: str = '"gamma"',
    third_id: str = '"logical.gamma"',
    third_effects: str = '"effects": ["READ"],',
) -> str:
    return f"""
    TOOL_METADATA = {{
        "metadata_version": 2,
        "name": "physical_bundle",
        "version": "9.0.0",
        "capabilities": [
            {{
                "id": "logical.alpha",
                "version": "1.0",
                "description": "alpha",
                "effects": ["READ"],
                "binding": {{"action": "alpha"}},
                "parameters": {{
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {{"value": {{"type": "string"}}}},
                    "required": ["value"],
                }},
            }},
            {{
                "id": "logical.beta",
                "version": "1.0",
                "description": "beta",
                "effects": ["READ"],
                "binding": {{"action": "beta"}},
                "parameters": {{
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {{"value": {{"type": "string"}}}},
                    "required": ["value"],
                }},
            }},
            {{
                "id": {third_id},
                "version": "1.0",
                "description": "gamma",
                {third_effects}
                "binding": {{"action": {third_binding}}},
                "parameters": {{
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {{"value": {{"type": "string"}}}},
                    "required": ["value"],
                }},
            }},
        ],
    }}

    def run(action, **kwargs):
        return {{"physical_action": action, "arguments": kwargs}}
    """


def _canonical_v2_source(*, third_effects: str = '"effects": ["READ"],') -> str:
    return f"""
    from tools.v1._shared.contracts import tool_result_schema

    TOOL_METADATA = {{
        "manifest_version": "2.0",
        "name": "physical_bundle",
        "version": "9.0.0",
        "description": "canonical bundle",
        "expose_root": False,
        "exports": [
            {{
                "id": "logical.alpha",
                "version": "3.0",
                "name": "logical.alpha",
                "description": "alpha",
                "bind": {{"action": "shared", "mode": "alpha"}},
                "input_schema": {{
                    "type": "object",
                    "properties": {{"value": {{"type": "string"}}}},
                    "required": ["value"],
                    "additionalProperties": False,
                }},
                "output_schema": tool_result_schema({{}}),
                "kind": "TOOL",
                "execution_mode": "ONE_SHOT",
                "idempotency": "UNKNOWN",
                "effects": ["READ"],
                "base_risk": "LOW",
                "required_scopes": ["scope.b", "scope.a"],
                "required_permissions": ["perm.b", "perm.a"],
                "danger_patterns": ["secret.b", "secret.a"],
            }},
            {{
                "id": "logical.beta",
                "version": "3.0",
                "name": "logical.beta",
                "description": "beta",
                "bind": {{"action": "shared", "mode": "beta"}},
                "input_schema": {{
                    "type": "object",
                    "properties": {{"value": {{"type": "string"}}}},
                    "required": ["value"],
                    "additionalProperties": False,
                }},
                "output_schema": tool_result_schema({{}}),
                "kind": "TOOL",
                "execution_mode": "ONE_SHOT",
                "idempotency": "UNKNOWN",
                "effects": ["READ"],
                "base_risk": "LOW",
                "required_scopes": [],
                "required_permissions": [],
                "danger_patterns": [],
            }},
            {{
                "id": "logical.gamma",
                "version": "3.0",
                "name": "logical.gamma",
                "description": "gamma",
                "bind": {{"action": "gamma"}},
                "input_schema": {{
                    "type": "object",
                    "properties": {{"value": {{"type": "string"}}}},
                    "required": ["value"],
                    "additionalProperties": False,
                }},
                "output_schema": tool_result_schema({{}}),
                "kind": "TOOL",
                "execution_mode": "ONE_SHOT",
                "idempotency": "UNKNOWN",
                {third_effects}
                "base_risk": "LOW",
                "required_scopes": [],
                "required_permissions": [],
                "danger_patterns": [],
            }},
        ],
    }}

    def run(action, mode=None, **kwargs):
        return {{
            "physical_action": action,
            "mode": mode,
            "arguments": kwargs,
        }}
    """


@pytest.mark.asyncio
async def test_canonical_metadata_v2_server_projection_and_generic_bind(tmp_path):
    _write_module(tmp_path, "canonical_bundle", _canonical_v2_source())
    runtime, tools = _runtime_and_tools()

    result = register_local_tools(runtime, tools, tmp_path)

    assert result == {
        "logical.alpha": "registered",
        "logical.beta": "registered",
        "logical.gamma": "registered",
    }
    assert runtime.registry.get_driver("physical_bundle") is None
    assert tools.get("physical_bundle") is None

    context = CapabilityExecutionContext.create(identity=None)
    alpha = runtime.registry.get_driver("logical.alpha")
    beta = runtime.registry.get_driver("logical.beta")
    assert alpha is not None
    assert beta is not None

    assert await alpha.execute(context, {"value": "x"}) == {
        "physical_action": "shared",
        "mode": "alpha",
        "arguments": {"value": "x"},
    }
    assert await beta.execute(context, {"value": "y"}) == {
        "physical_action": "shared",
        "mode": "beta",
        "arguments": {"value": "y"},
    }

    definition = runtime.registry.get_definition("logical.alpha")
    assert definition.version == "3.0"
    assert definition.source == "LOCAL"
    assert definition.execution_kind == "PYTHON"
    assert definition.kind.value == "TOOL"
    assert definition.execution_mode.value == "ONE_SHOT"
    assert definition.idempotency.value == "UNKNOWN"
    assert {item.value for item in definition.effects} == {"READ"}
    assert definition.required_scopes == ["scope.a", "scope.b"]
    assert set(definition.output_schema["required"]) >= {
        "ok",
        "tool",
        "action",
        "data",
        "error",
        "meta",
    }
    assert definition.metadata == {
        "base_risk": "LOW",
        "required_permissions": ["perm.a", "perm.b"],
        "danger_patterns": ["secret.a", "secret.b"],
    }

    implementation = runtime.catalog.get_implementation(
        "server:logical.alpha"
    )
    assert implementation.metadata["manifest_version"] == "2.0"
    assert implementation.metadata["physical_tool"] == "physical_bundle"
    assert implementation.metadata["physical_version"] == "9.0.0"
    assert implementation.metadata["bind"] == {
        "action": "shared",
        "mode": "alpha",
    }


def _canonical_alpha_definition() -> CapabilityDefinition:
    return CapabilityDefinition(
        id="logical.alpha",
        version="3.0",
        name="logical.alpha",
        description="alpha",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        output_schema=tool_result_schema({}),
        source="LOCAL",
        execution_kind="PYTHON",
        kind="TOOL",
        execution_mode="ONE_SHOT",
        idempotency="UNKNOWN",
        effects={"READ"},
        require_auth=False,
        required_scopes=["scope.a", "scope.b"],
        metadata={
            "base_risk": "LOW",
            "required_permissions": ["perm.a", "perm.b"],
            "danger_patterns": ["secret.a", "secret.b"],
        },
    )


def test_canonical_server_reuses_equivalent_existing_definition(tmp_path):
    _write_module(tmp_path, "canonical_bundle", _canonical_v2_source())
    runtime, tools = _runtime_and_tools()
    existing = _canonical_alpha_definition()
    runtime.catalog.register_definition(existing)

    result = register_local_tools(runtime, tools, tmp_path)

    assert result["logical.alpha"] == "registered"
    assert runtime.catalog.get_definition("logical.alpha") == existing
    assert runtime.catalog.get_implementation(
        "server:logical.alpha"
    ).state.value == "ENABLED"


def test_canonical_server_reuses_reordered_set_like_policy_fields(tmp_path):
    _write_module(tmp_path, "canonical_bundle", _canonical_v2_source())
    runtime, tools = _runtime_and_tools()
    existing = _canonical_alpha_definition().model_copy(
        update={
            "required_scopes": ["scope.b", "scope.a"],
            "metadata": {
                "base_risk": "LOW",
                "required_permissions": ["perm.b", "perm.a"],
                "danger_patterns": ["secret.b", "secret.a"],
            },
        }
    )
    runtime.catalog.register_definition(existing)

    result = register_local_tools(runtime, tools, tmp_path)

    assert result["logical.alpha"] == "registered"
    assert runtime.catalog.get_definition("logical.alpha") == existing
    assert runtime.catalog.contains_implementation("server:logical.alpha")


def test_canonical_server_rejects_divergent_existing_definition_before_mutation(
    tmp_path,
):
    _write_module(tmp_path, "canonical_bundle", _canonical_v2_source())
    runtime, tools = _runtime_and_tools()
    divergent = _canonical_alpha_definition().model_copy(
        update={"description": "divergent"}
    )
    runtime.catalog.register_definition(divergent)

    result = register_local_tools(runtime, tools, tmp_path)

    assert set(result) == {"canonical_bundle"}
    assert result["canonical_bundle"].startswith("skipped:")
    assert "different contract" in result["canonical_bundle"]
    assert runtime.registry.get("logical.alpha") is None
    assert runtime.registry.get("logical.beta") is None
    assert runtime.registry.get("logical.gamma") is None
    assert tools.get("logical.alpha") is None
    assert not runtime.catalog.contains_implementation(
        "server:logical.alpha"
    )


@pytest.mark.asyncio
async def test_canonical_metadata_v2_bound_keys_cannot_be_overridden(tmp_path):
    _write_module(tmp_path, "canonical_bundle", _canonical_v2_source())
    runtime, tools = _runtime_and_tools()
    register_local_tools(runtime, tools, tmp_path)

    driver = runtime.registry.get_driver("logical.alpha")
    assert driver is not None
    with pytest.raises(
        TypeError,
        match="cannot override immutable bound fields: mode",
    ):
        await driver.execute(
            CapabilityExecutionContext.create(identity=None),
            {"value": "x", "mode": "caller"},
        )


@pytest.mark.asyncio
async def test_canonical_nested_bind_is_deeply_immutable_across_calls(tmp_path):
    source = """
    from tools.v1._shared.contracts import tool_result_schema

    TOOL_METADATA = {
        "manifest_version": "2.0",
        "name": "nested_bundle",
        "version": "1.0.0",
        "description": "nested bind bundle",
        "expose_root": False,
        "exports": [
            {
                "id": "logical.nested",
                "version": "1.0",
                "name": "logical.nested",
                "description": "nested",
                "bind": {
                    "action": "nested",
                    "options": {"tags": ["original"]},
                },
                "input_schema": {
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
                "output_schema": tool_result_schema({}),
                "kind": "TOOL",
                "execution_mode": "ONE_SHOT",
                "idempotency": "UNKNOWN",
                "effects": ["READ"],
                "base_risk": "LOW",
                "required_scopes": [],
                "required_permissions": [],
                "danger_patterns": [],
            }
        ],
    }

    def run(action, options, value):
        options["tags"].append("mutated")
        return {
            "action": action,
            "tags": list(options["tags"]),
            "value": value,
        }
    """
    _write_module(tmp_path, "nested_bundle", source)
    runtime, tools = _runtime_and_tools()
    register_local_tools(runtime, tools, tmp_path)

    driver = runtime.registry.get_driver("logical.nested")
    assert driver is not None
    context = CapabilityExecutionContext.create(identity=None)

    first = await driver.execute(context, {"value": "first"})
    second = await driver.execute(context, {"value": "second"})

    assert first["tags"] == ["original", "mutated"]
    assert second["tags"] == ["original", "mutated"]
    implementation = runtime.catalog.get_implementation(
        "server:logical.nested"
    )
    assert implementation.metadata["bind"] == {
        "action": "nested",
        "options": {"tags": ["original"]},
    }


def test_canonical_metadata_v2_invalid_export_has_zero_registration(tmp_path):
    _write_module(
        tmp_path,
        "canonical_broken",
        _canonical_v2_source(third_effects=""),
    )
    runtime, tools = _runtime_and_tools()

    result = register_local_tools(runtime, tools, tmp_path)

    assert set(result) == {"canonical_broken"}
    assert result["canonical_broken"].startswith("skipped:")
    for capability_id in (
        "logical.alpha",
        "logical.beta",
        "logical.gamma",
    ):
        assert runtime.registry.get(capability_id) is None
        assert tools.get(capability_id) is None
        assert not runtime.catalog.contains_definition(capability_id)
        assert not runtime.catalog.contains_implementation(
            f"server:{capability_id}"
        )
        assert runtime.driver_registry.get(
            f"server:{capability_id}"
        ) is None


def test_canonical_metadata_v2_expose_root_true_fails_closed(tmp_path):
    source = _canonical_v2_source().replace(
        '"expose_root": False',
        '"expose_root": True',
        1,
    )
    _write_module(tmp_path, "canonical_root", source)
    runtime, tools = _runtime_and_tools()

    result = register_local_tools(runtime, tools, tmp_path)

    assert set(result) == {"canonical_root"}
    assert result["canonical_root"].startswith("skipped:")
    assert "expose_root=true is not supported" in result["canonical_root"]
    assert runtime.registry.get("physical_bundle") is None
    assert runtime.registry.get("logical.alpha") is None


def test_transitional_t7_metadata_v2_dialect_is_rejected(tmp_path):
    _write_module(tmp_path, "transitional", _v2_source())
    runtime, tools = _runtime_and_tools()

    result = register_local_tools(runtime, tools, tmp_path)

    assert set(result) == {"transitional"}
    assert result["transitional"].startswith("skipped:")
    assert "transitional metadata_version dialect is unsupported" in result[
        "transitional"
    ]
    for capability_id in (
        "logical.alpha",
        "logical.beta",
        "logical.gamma",
    ):
        assert runtime.registry.get(capability_id) is None
        assert tools.get(capability_id) is None
        assert not runtime.catalog.contains_definition(capability_id)
        assert not runtime.catalog.contains_implementation(
            f"server:{capability_id}"
        )


def test_canonical_logical_schema_rejects_bound_fields(tmp_path):
    _write_module(tmp_path, "canonical_bundle", _canonical_v2_source())
    runtime, tools = _runtime_and_tools()
    register_local_tools(runtime, tools, tmp_path)

    definition = runtime.registry.get_definition("logical.alpha")
    for field_name in ("action", "mode"):
        validation = JsonSchemaToolArgumentValidator().validate(
            definition,
            {"value": "x", field_name: "evil"},
        )
        assert validation.valid is False
        assert validation.error_code == "CAPABILITY_INVALID_ARGUMENT"


def test_metadata_v1_registration_remains_compatible(tmp_path):
    _write_module(
        tmp_path,
        "legacy",
        """
        TOOL_METADATA = {
            "name": "legacy.tool",
            "description": "legacy",
            "effects": ["READ"],
            "parameters": {
                "type": "object",
                "properties": {
                    "value": {"type": "string"},
                },
            },
        }

        def run(**kwargs):
            return kwargs
        """,
    )
    runtime, tools = _runtime_and_tools()

    result = register_local_tools(runtime, tools, tmp_path)

    assert result == {"legacy.tool": "registered"}
    assert runtime.registry.get_driver("legacy.tool") is not None
    assert tools.get("legacy.tool") is not None
    assert runtime.catalog.get_implementation(
        "server:legacy.tool"
    ).state.value == "ENABLED"


def test_metadata_version_bool_is_not_treated_as_v1(tmp_path):
    _write_module(
        tmp_path,
        "bool_version",
        """
        TOOL_METADATA = {
            "metadata_version": True,
            "name": "bool.tool",
            "description": "invalid version type",
            "parameters": {"type": "object"},
        }

        def run(**kwargs):
            return kwargs
        """,
    )
    runtime, tools = _runtime_and_tools()

    result = register_local_tools(runtime, tools, tmp_path)

    assert set(result) == {"bool_version"}
    assert result["bool_version"].startswith("skipped:")
    assert runtime.registry.get("bool.tool") is None
    assert tools.get("bool.tool") is None


@pytest.mark.asyncio
async def test_capability_runtime_keeps_logical_invocation_identity(tmp_path):
    _write_module(tmp_path, "bundle", _canonical_v2_source())
    runtime, tools = _runtime_and_tools()
    register_local_tools(runtime, tools, tmp_path)

    invocation_id = "inv-t8-logical-beta"
    result = await runtime.execute_capability(
        capability_id="logical.beta",
        arguments={"value": "through-runtime"},
        identity=Identity(
            user_id="t7-user",
            auth_type="jwt",
            scopes={"scope.a", "scope.b"},
        ),
        invocation_id=invocation_id,
    )

    assert result.output == {
        "physical_action": "shared",
        "mode": "beta",
        "arguments": {"value": "through-runtime"},
    }
    stored = await runtime.invocation_lifecycle.store.get(invocation_id)
    assert stored is not None
    assert stored.capability_id == "logical.beta"
    assert stored.arguments == {"value": "through-runtime"}


def _write_canonical_package(
    root,
    *,
    marker: str,
    fail: bool = False,
    package_name: str = "samepkg",
):
    package = root / package_name
    package.mkdir()
    (package / "helper.py").write_text(
        f"VALUE = {marker!r}\n",
        encoding="utf-8",
    )
    failure = 'raise RuntimeError("boom")\n' if fail else ""
    (package / "__init__.py").write_text(
        textwrap.dedent(
            f"""
            from .helper import VALUE
            from tools.v1._shared.contracts import tool_result_schema
            {failure}
            TOOL_METADATA = {{
                "manifest_version": "2.0",
                "name": "{package_name}",
                "version": "1.0.0",
                "description": "cache isolation package",
                "expose_root": False,
                "exports": [
                    {{
                        "id": "logical.cache",
                        "version": "1.0",
                        "name": "logical.cache",
                        "description": "cache isolation",
                        "bind": {{}},
                        "input_schema": {{
                            "type": "object",
                            "properties": {{}},
                            "required": [],
                            "additionalProperties": False,
                        }},
                        "output_schema": tool_result_schema({{}}),
                        "kind": "TOOL",
                        "execution_mode": "ONE_SHOT",
                        "idempotency": "UNKNOWN",
                        "effects": ["READ"],
                        "base_risk": "LOW",
                        "required_scopes": [],
                        "required_permissions": [],
                        "danger_patterns": [],
                    }}
                ],
            }}

            def run():
                return {{"value": VALUE}}
            """
        ),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_server_package_cache_is_scoped_to_current_tools_root(tmp_path):
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    _write_canonical_package(first_root, marker="first")
    _write_canonical_package(second_root, marker="second")

    first_runtime, first_tools = _runtime_and_tools()
    second_runtime, second_tools = _runtime_and_tools()

    assert register_local_tools(
        first_runtime,
        first_tools,
        first_root,
    )["logical.cache"] == "registered"
    assert register_local_tools(
        second_runtime,
        second_tools,
        second_root,
    )["logical.cache"] == "registered"

    context = CapabilityExecutionContext.create(identity=None)
    first_driver = first_runtime.registry.get_driver("logical.cache")
    second_driver = second_runtime.registry.get_driver("logical.cache")
    assert first_driver is not None
    assert second_driver is not None
    assert await first_driver.execute(context, {}) == {"value": "first"}
    assert await second_driver.execute(context, {}) == {"value": "second"}


def test_failed_server_package_import_cleans_synthetic_submodules(tmp_path):
    _write_canonical_package(
        tmp_path,
        marker="failed",
        fail=True,
        package_name="failedpkg",
    )
    runtime, tools = _runtime_and_tools()

    result = register_local_tools(runtime, tools, tmp_path)

    assert result["failedpkg"].startswith("skipped:")
    assert "gateway_local_tool_failedpkg" not in sys.modules
    assert not any(
        name.startswith("gateway_local_tool_failedpkg.")
        for name in sys.modules
    )
