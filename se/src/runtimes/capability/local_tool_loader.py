"""Discover executable, server-owned tools from the repository tools/v1."""
from __future__ import annotations

import importlib.util
from copy import deepcopy
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from tools.v1._shared.metadata import validate_tool_manifest_v2

from .contracts.definition import (
    CapabilityDefinition,
    CapabilityEffect,
)
from .contracts.implementation import (
    CapabilityExecutionLocation,
    CapabilityImplementation,
    CapabilityImplementationState,
    CapabilityOwnerType,
)
from .drivers.python_driver import PythonCapabilityDriver


class _MetadataV2Error(ValueError):
    """Expected fail-closed Metadata V2 validation/preflight error."""


class _MetadataV2CommitError(RuntimeError):
    """Unexpected Metadata V2 registration failure; bootstrap must surface it."""


@dataclass(frozen=True)
class _LocalCapabilityPlan:
    definition: CapabilityDefinition
    driver: PythonCapabilityDriver
    implementation_metadata: dict[str, Any] = field(default_factory=dict)


def _bound_handler(
    handler: Callable[..., Any],
    bind: dict[str, Any],
) -> Callable[..., Any]:
    """Bind one canonical logical export to immutable physical arguments."""
    bound_values = deepcopy(bind)

    def bound_handler(**arguments: Any) -> Any:
        collisions = set(arguments).intersection(bound_values)
        if collisions:
            names = ", ".join(sorted(collisions))
            raise TypeError(
                "logical capability cannot override immutable bound fields: "
                f"{names}"
            )
        invocation_bind = deepcopy(bound_values)
        return handler(**invocation_bind, **arguments)

    bound_handler.__name__ = (
        f"{getattr(handler, '__name__', 'run')}__bound"
    )
    return bound_handler


def _build_canonical_v2_plans(
    metadata: dict[str, Any],
    handler: Callable[..., Any],
) -> list[_LocalCapabilityPlan]:
    """Build logical plans from the frozen T1 Metadata V2 manifest."""
    try:
        manifest = validate_tool_manifest_v2(metadata)
    except Exception as exc:
        raise _MetadataV2Error(str(exc)) from exc

    if manifest["expose_root"]:
        raise _MetadataV2Error(
            "canonical Metadata V2 expose_root=true is not supported by T8"
        )

    physical_name = manifest["name"]
    physical_version = manifest["version"]
    plans: list[_LocalCapabilityPlan] = []

    for export in manifest["exports"]:
        capability_id = export["id"]
        definition = CapabilityDefinition(
            id=capability_id,
            version=export["version"],
            name=export["name"],
            description=export["description"],
            input_schema=export["input_schema"],
            output_schema=export["output_schema"],
            source="LOCAL",
            execution_kind="PYTHON",
            kind=export["kind"],
            execution_mode=export["execution_mode"],
            idempotency=export["idempotency"],
            effects={
                CapabilityEffect(item)
                for item in export["effects"]
            },
            require_auth=False,
            required_scopes=sorted(export["required_scopes"]),
            metadata={
                "base_risk": export["base_risk"],
                "required_permissions": sorted(
                    export["required_permissions"]
                ),
                "danger_patterns": sorted(export["danger_patterns"]),
            },
        )
        bind = deepcopy(export["bind"])
        plans.append(
            _LocalCapabilityPlan(
                definition=definition,
                driver=PythonCapabilityDriver(
                    definition,
                    _bound_handler(handler, bind),
                ),
                implementation_metadata={
                    "manifest_version": manifest["manifest_version"],
                    "physical_tool": physical_name,
                    "physical_version": physical_version,
                    "bind": deepcopy(bind),
                },
            )
        )

    return plans


def _definition_from_v1(metadata: dict[str, Any]) -> CapabilityDefinition:
    name = str(metadata["name"])
    return CapabilityDefinition(
        id=name,
        name=name,
        description=str(metadata.get("description", name)),
        input_schema=metadata.get("parameters") or {"type": "object"},
        source="LOCAL",
        execution_kind="PYTHON",
        require_auth=bool(metadata.get("require_auth", False)),
        required_scopes=list(metadata.get("required_scopes", [])),
        effects={
            CapabilityEffect(item)
            for item in metadata.get("effects", [])
        },
        metadata={
            "base_risk": metadata.get("base_risk", "HIGH"),
            "required_permissions": list(
                metadata.get("required_permissions", [])
            ),
        },
    )


def _canonical_definition_contract(
    definition: CapabilityDefinition,
) -> dict[str, Any]:
    """Return deterministic logical semantics for Metadata V2 convergence."""
    value = definition.model_dump(mode="json")
    value["effects"] = sorted(value.get("effects", []))
    value["required_scopes"] = sorted(value.get("required_scopes", []))

    metadata = dict(value.get("metadata") or {})
    for field_name in ("required_permissions", "danger_patterns"):
        if field_name in metadata:
            metadata[field_name] = sorted(metadata[field_name])
    value["metadata"] = metadata
    return value


def _preflight_v2_registration(
    runtime: Any,
    tool_registry: Any,
    plans: list[_LocalCapabilityPlan],
) -> None:
    for plan in plans:
        capability_id = plan.definition.capability_id
        implementation_id = f"server:{capability_id}"

        if runtime.registry.get(capability_id) is not None:
            raise _MetadataV2Error(
                f"capability already registered: {capability_id}"
            )
        if tool_registry.get(capability_id) is not None:
            raise _MetadataV2Error(
                f"tool definition already registered: {capability_id}"
            )
        if runtime.driver_registry.get(implementation_id) is not None:
            raise _MetadataV2Error(
                f"implementation driver already bound: {implementation_id}"
            )
        if runtime.catalog is not None:
            if runtime.catalog.contains_definition(capability_id):
                existing = runtime.catalog.get_definition(capability_id)
                if (
                    _canonical_definition_contract(existing)
                    != _canonical_definition_contract(plan.definition)
                ):
                    raise _MetadataV2Error(
                        "catalog definition already registered with a "
                        f"different contract: {capability_id}"
                    )
            if runtime.catalog.contains_implementation(implementation_id):
                raise _MetadataV2Error(
                    "catalog implementation already registered: "
                    f"{implementation_id}"
                )


def _register_one(
    runtime: Any,
    tool_registry: Any,
    plan: _LocalCapabilityPlan,
) -> None:
    from ...domain.schemas.tool import GatewayToolDefinition

    definition = plan.definition
    runtime.register_capability(plan.driver)
    tool_registry.register(
        GatewayToolDefinition(
            name=definition.name,
            description=definition.description,
            parameters=definition.input_schema,
        )
    )

    if runtime.catalog is None:
        return

    if runtime.catalog.contains_definition(definition.capability_id):
        catalog_definition = runtime.catalog.get_definition(
            definition.capability_id
        )
        if (
            _canonical_definition_contract(catalog_definition)
            != _canonical_definition_contract(definition)
        ):
            raise _MetadataV2CommitError(
                "catalog definition diverged after successful preflight: "
                f"{definition.capability_id}"
            )
    else:
        catalog_definition = runtime.catalog.register_definition(definition)

    implementation_id = f"server:{definition.capability_id}"
    if not runtime.catalog.contains_implementation(implementation_id):
        implementation = CapabilityImplementation.from_definition(
            catalog_definition,
            implementation_id=implementation_id,
            location=CapabilityExecutionLocation.SERVER,
            driver_kind="PYTHON",
            owner_type=CapabilityOwnerType.SYSTEM,
            metadata={
                "kind": "TOOL",
                **plan.implementation_metadata,
            },
        )
        runtime.catalog.register_implementation(implementation)
        runtime.catalog.transition_implementation(
            implementation_id,
            CapabilityImplementationState.ENABLED,
        )
    runtime.driver_registry.bind(
        implementation_id,
        runtime.registry.get_driver(definition.capability_id),
        replace=True,
    )


def register_local_tools(
    runtime: Any,
    tool_registry: Any,
    tools_dir: Path,
) -> dict[str, str]:
    """Register local tool modules.

    Metadata V1 remains one-module to one-capability.

    Canonical Metadata V2 (manifest_version='2.0') preflights the complete
    bundle and registers only its logical exports. The physical package name
    remains implementation metadata and is not registered as an executable
    capability. The transitional T7 metadata_version=2 dialect is rejected.

    Broken optional dependencies and expected metadata validation failures are
    reported and skipped. An unexpected V2 commit failure is surfaced because
    silently continuing after partial registration would violate the bootstrap
    contract.
    """
    results: dict[str, str] = {}
    if not tools_dir.is_dir():
        return {"_directory": f"not found: {tools_dir}"}

    candidates = list(tools_dir.glob("*.py")) + [
        path / "__init__.py"
        for path in tools_dir.iterdir()
        if path.is_dir() and (path / "__init__.py").is_file()
    ]

    for path in sorted(candidates, key=lambda item: str(item)):
        if path.name.startswith("__") and path.parent == tools_dir:
            continue

        module_name = (
            path.parent.name if path.name == "__init__.py" else path.stem
        )
        import_name = f"gateway_local_tool_{module_name}"

        prior_namespace = {
            name: module
            for name, module in sys.modules.items()
            if name == import_name or name.startswith(f"{import_name}.")
        }
        for loaded_name in list(sys.modules):
            if (
                loaded_name == import_name
                or loaded_name.startswith(f"{import_name}.")
            ):
                sys.modules.pop(loaded_name, None)

        try:
            spec = importlib.util.spec_from_file_location(
                import_name,
                path,
                submodule_search_locations=[str(path.parent)]
                if path.name == "__init__.py"
                else None,
            )
            if spec is None or spec.loader is None:
                raise RuntimeError("unable to create module spec")

            module = importlib.util.module_from_spec(spec)
            sys.modules[import_name] = module
            spec.loader.exec_module(module)

            metadata = getattr(module, "TOOL_METADATA", None)
            handler = getattr(module, "run", None)
            if (
                not isinstance(metadata, dict)
                or not metadata.get("name")
                or not callable(handler)
            ):
                continue

            manifest_version = metadata.get("manifest_version")
            if manifest_version is not None:
                if manifest_version != "2.0":
                    raise ValueError(
                        "manifest_version must equal '2.0'"
                    )
                plans = _build_canonical_v2_plans(metadata, handler)
                _preflight_v2_registration(runtime, tool_registry, plans)
                registered: list[str] = []
                try:
                    for plan in plans:
                        _register_one(runtime, tool_registry, plan)
                        registered.append(plan.definition.capability_id)
                except Exception as exc:
                    raise _MetadataV2CommitError(
                        "Metadata V2 commit failed after successful preflight"
                    ) from exc
                for capability_id in registered:
                    results[capability_id] = "registered"
                continue

            metadata_version = metadata.get("metadata_version", 1)
            if type(metadata_version) is not int:
                raise ValueError(
                    "metadata_version must be an integer"
                )
            if metadata_version != 1:
                raise ValueError(
                    "transitional metadata_version dialect is unsupported; "
                    "use manifest_version='2.0'"
                )

            definition = _definition_from_v1(metadata)
            _register_one(
                runtime,
                tool_registry,
                _LocalCapabilityPlan(
                    definition=definition,
                    driver=PythonCapabilityDriver(definition, handler),
                ),
            )
            results[definition.capability_id] = "registered"

        except _MetadataV2CommitError:
            for loaded_name in list(sys.modules):
                if (
                    loaded_name == import_name
                    or loaded_name.startswith(f"{import_name}.")
                ):
                    sys.modules.pop(loaded_name, None)
            sys.modules.update(prior_namespace)
            raise
        except Exception as exc:
            for loaded_name in list(sys.modules):
                if (
                    loaded_name == import_name
                    or loaded_name.startswith(f"{import_name}.")
                ):
                    sys.modules.pop(loaded_name, None)
            sys.modules.update(prior_namespace)
            results[module_name] = f"skipped: {exc}"

    return results
