"""Discover executable, server-owned tools from the repository's ``tools/v1``."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from .contracts.definition import CapabilityDefinition, CapabilityEffect
from .contracts.implementation import CapabilityExecutionLocation, CapabilityImplementation, CapabilityImplementationState, CapabilityOwnerType
from .drivers.python_driver import PythonCapabilityDriver


def register_local_tools(runtime: Any, tool_registry: Any, tools_dir: Path) -> dict[str, str]:
    """Register modules exposing ``TOOL_METADATA`` and a callable ``run``.

    A broken optional desktop dependency is reported and skipped rather than
    making gateway startup fail.
    """
    results: dict[str, str] = {}
    if not tools_dir.is_dir():
        return {"_directory": f"not found: {tools_dir}"}
    for path in sorted(tools_dir.glob("*.py")):
        if path.name.startswith("__"):
            continue
        try:
            spec = importlib.util.spec_from_file_location(f"gateway_local_tools.{path.stem}", path)
            if spec is None or spec.loader is None:
                raise RuntimeError("unable to create module spec")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            metadata, handler = getattr(module, "TOOL_METADATA", None), getattr(module, "run", None)
            if not isinstance(metadata, dict) or not metadata.get("name") or not callable(handler):
                continue
            definition = CapabilityDefinition(
                id=str(metadata["name"]), name=str(metadata["name"]),
                description=str(metadata.get("description", metadata["name"])),
                input_schema=metadata.get("parameters") or {"type": "object"},
                source="LOCAL", execution_kind="PYTHON",
                effects={CapabilityEffect(item) for item in metadata.get("effects", [])},
                metadata={"module_path": str(path), "base_risk": metadata.get("base_risk", "HIGH")},
            )
            runtime.register_capability(PythonCapabilityDriver(definition, handler))
            from ...domain.schemas.tool import GatewayToolDefinition
            tool_registry.register(GatewayToolDefinition(name=definition.name, description=definition.description, parameters=definition.input_schema))
            if runtime.catalog is not None:
                runtime.catalog.register_definition(definition)
                implementation_id = f"server:{definition.capability_id}"
                if not runtime.catalog.contains_implementation(implementation_id):
                    implementation = CapabilityImplementation.from_definition(definition, implementation_id=implementation_id, location=CapabilityExecutionLocation.SERVER, driver_kind="PYTHON", owner_type=CapabilityOwnerType.SYSTEM, metadata={"module_path": str(path), "kind": "TOOL"})
                    runtime.catalog.register_implementation(implementation)
                    runtime.catalog.transition_implementation(implementation_id, CapabilityImplementationState.ENABLED)
                runtime.driver_registry.bind(
                    implementation_id,
                    runtime.registry.get_driver(definition.capability_id),
                    replace=True,
                )
            results[definition.capability_id] = "registered"
        except Exception as exc:
            results[path.stem] = f"skipped: {exc}"
    return results
