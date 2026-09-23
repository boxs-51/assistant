import importlib.util
import inspect
import logging
import sys
import types
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Set, Union

from tools.v1._shared.metadata import validate_tool_manifest_v2


logger = logging.getLogger(__name__)

_RUNTIME_CONTEXT_BIND_KEYS = frozenset(
    {
        "invocation_id",
        "connection_id",
        "session_id",
        "cancel_event",
    }
)
_EXCLUDED_PACKAGE_DIRS = frozenset({"_shared", "test", "live"})


class LocalToolManager:
    """Discover, load and filter local Python tools."""

    def __init__(
        self,
        tools_dir: Union[str, Path],
        internal_required_tools: Set[str],
    ):
        self.tools_dir = Path(tools_dir).resolve()
        self.internal_required_tools = internal_required_tools

    def _setup_import_environment(self) -> None:
        """Expose tools_dir and the compatibility local_tools package."""
        tools_str = str(self.tools_dir)

        if tools_str not in sys.path:
            sys.path.insert(0, tools_str)

        if "local_tools" not in sys.modules:
            pkg_mod = types.ModuleType("local_tools")
            pkg_mod.__path__ = [tools_str]
            pkg_mod.__file__ = str(self.tools_dir / "__init__.py")
            sys.modules["local_tools"] = pkg_mod
        else:
            pkg_mod = sys.modules["local_tools"]
            package_path = list(getattr(pkg_mod, "__path__", []))
            package_path = [
                item for item in package_path
                if str(Path(item).resolve()) != tools_str
            ]
            pkg_mod.__path__ = [tools_str, *package_path]

    @staticmethod
    def _enabled_v2_capabilities(
        tools_config: Dict[str, Any],
    ) -> frozenset[str]:
        raw = tools_config.get("enabled_v2_capabilities", [])
        if raw is None:
            raw = []
        if not isinstance(raw, list):
            raise ValueError(
                "tools_config.enabled_v2_capabilities must be a list"
            )
        if any(
            not isinstance(item, str) or not item.strip()
            for item in raw
        ):
            raise ValueError(
                "tools_config.enabled_v2_capabilities must contain "
                "non-empty capability IDs"
            )
        if "*" in raw:
            raise ValueError(
                "tools_config.enabled_v2_capabilities does not support '*'"
            )
        if len(set(raw)) != len(raw):
            raise ValueError(
                "tools_config.enabled_v2_capabilities contains duplicates"
            )
        return frozenset(raw)

    def _entrypoint_candidates(self) -> list[tuple[Path, bool]]:
        files = [
            path
            for path in self.tools_dir.glob("*.py")
            if path.name != "__init__.py"
        ]
        packages = [
            path / "__init__.py"
            for path in self.tools_dir.iterdir()
            if (
                path.is_dir()
                and not path.name.startswith("_")
                and path.name not in _EXCLUDED_PACKAGE_DIRS
                and (path / "__init__.py").is_file()
            )
        ]
        return sorted(
            [(path, False) for path in files]
            + [(path, True) for path in packages],
            key=lambda item: str(item[0]),
        )

    def _load_entrypoint(
        self,
        fpath: Path,
        *,
        is_package: bool,
    ):
        module_leaf = fpath.parent.name if is_package else fpath.stem
        mname = f"local_tools.{module_leaf}"

        cached_module = sys.modules.get(mname)
        if (
            cached_module is not None
            and hasattr(cached_module, "TOOL_METADATA")
        ):
            cached_file = getattr(cached_module, "__file__", None)
            if (
                cached_file is not None
                and Path(cached_file).resolve() == fpath.resolve()
            ):
                return cached_module

        prior_namespace = {
            name: module
            for name, module in sys.modules.items()
            if name == mname or name.startswith(f"{mname}.")
        }
        prior_alias = (
            sys.modules.get(fpath.stem)
            if not is_package and fpath.stem in sys.modules
            else None
        )
        had_prior_alias = (
            not is_package and fpath.stem in sys.modules
        )

        for module_name in list(sys.modules):
            if (
                module_name == mname
                or module_name.startswith(f"{mname}.")
            ):
                sys.modules.pop(module_name, None)

        try:
            spec = importlib.util.spec_from_file_location(
                mname,
                fpath,
                submodule_search_locations=(
                    [str(fpath.parent)]
                    if is_package
                    else [str(self.tools_dir)]
                ),
            )
            if not spec or not spec.loader:
                logger.warning("Không thể tạo module spec cho: %s", fpath)
                return None

            mod = importlib.util.module_from_spec(spec)
            if not is_package:
                mod.__package__ = "local_tools"

            sys.modules[mname] = mod
            if not is_package:
                sys.modules[fpath.stem] = mod

            spec.loader.exec_module(mod)
            return mod
        except Exception as exc:
            for module_name in list(sys.modules):
                if (
                    module_name == mname
                    or module_name.startswith(f"{mname}.")
                ):
                    sys.modules.pop(module_name, None)
            sys.modules.update(prior_namespace)

            if not is_package:
                if had_prior_alias:
                    sys.modules[fpath.stem] = prior_alias
                else:
                    sys.modules.pop(fpath.stem, None)

            logger.error(
                "❌ Lỗi khi nạp tool/module từ '%s': %s",
                fpath,
                exc,
                exc_info=True,
            )
            return None

    @staticmethod
    def _bound_handler(handler, bind: Dict[str, Any]):
        bound_values = deepcopy(dict(bind))

        reserved = set(bound_values).intersection(
            _RUNTIME_CONTEXT_BIND_KEYS
        )
        if reserved:
            raise ValueError(
                "Metadata V2 bind uses reserved client runtime context "
                f"keys: {', '.join(sorted(reserved))}"
            )

        effective_signature = None
        try:
            signature = inspect.signature(handler)
        except (TypeError, ValueError):
            signature = None

        if signature is not None:
            parameters = signature.parameters
            accepts_kwargs = any(
                parameter.kind == inspect.Parameter.VAR_KEYWORD
                for parameter in parameters.values()
            )
            for key in bound_values:
                parameter = parameters.get(key)
                if parameter is None and not accepts_kwargs:
                    raise ValueError(
                        f"Metadata V2 bind key {key!r} is not accepted by "
                        "the physical callable"
                    )
                if (
                    parameter is not None
                    and parameter.kind == inspect.Parameter.POSITIONAL_ONLY
                ):
                    raise ValueError(
                        f"Metadata V2 bind key {key!r} targets a "
                        "positional-only physical parameter"
                    )

            effective_signature = signature.replace(
                parameters=[
                    parameter
                    for parameter in parameters.values()
                    if parameter.name not in bound_values
                ]
            )

        def bound_handler(**arguments):
            collisions = set(arguments).intersection(bound_values)
            if collisions:
                raise TypeError(
                    "logical capability cannot override immutable bound "
                    "fields: "
                    + ", ".join(sorted(collisions))
                )
            invocation_bind = deepcopy(bound_values)
            return handler(**invocation_bind, **arguments)

        bound_handler.__name__ = (
            f"{getattr(handler, '__name__', 'run')}__bound"
        )
        bound_handler.__doc__ = getattr(handler, "__doc__", None)
        bound_handler.__module__ = getattr(
            handler,
            "__module__",
            bound_handler.__module__,
        )
        if effective_signature is not None:
            bound_handler.__signature__ = effective_signature
        return bound_handler

    def _canonical_v2_entries(
        self,
        *,
        metadata: Dict[str, Any],
        handler,
        enabled: frozenset[str],
        fpath: Path,
    ) -> Dict[str, Dict[str, Any]]:
        manifest = validate_tool_manifest_v2(metadata)
        if manifest["expose_root"]:
            raise ValueError(
                "canonical Metadata V2 expose_root=true is not supported "
                "by the T8 client loader"
            )

        selected = [
            export
            for export in manifest["exports"]
            if export["id"] in enabled
        ]

        entries: Dict[str, Dict[str, Any]] = {}
        for export in selected:
            capability_id = export["id"]
            bind = deepcopy(export["bind"])
            func = self._bound_handler(handler, bind)

            entries[capability_id] = {
                "metadata": {
                    "name": export["name"],
                    "version": export["version"],
                    "description": export["description"],
                    "parameters": deepcopy(export["input_schema"]),
                    "input_schema": deepcopy(export["input_schema"]),
                    "output_schema": deepcopy(export["output_schema"]),
                    "kind": export["kind"],
                    "execution_mode": export["execution_mode"],
                    "idempotency": export["idempotency"],
                    "effects": sorted(export["effects"]),
                    "require_auth": False,
                    "required_scopes": sorted(
                        export["required_scopes"]
                    ),
                    "base_risk": export["base_risk"],
                    "required_permissions": sorted(
                        export["required_permissions"]
                    ),
                    "danger_patterns": sorted(
                        export["danger_patterns"]
                    ),
                    "physical_tool": manifest["name"],
                    "physical_version": manifest["version"],
                    "bind": deepcopy(bind),
                    "manifest_version": manifest[
                        "manifest_version"
                    ],
                },
                "func": func,
                "is_internal": False,
                "file_path": str(fpath),
            }

        return entries

    def load_tools(
        self,
        tools_config: Dict[str, Any],
    ) -> Dict[str, Dict[str, Any]]:
        loaded_tools: Dict[str, Dict[str, Any]] = {}
        # Tombstones make cross-generation/package collision handling
        # monotonic within one discovery pass. Once a logical ID participates
        # in a canonical V2 collision, a later entry cannot resurrect it just
        # because the conflicting package was already removed.
        disabled_capability_ids: set[str] = set()
        allowed = tools_config.get("allowed_local_tools", ["*"])
        blocked = set(
            tools_config.get("blocked_local_tools", [])
        )
        enabled_v2 = self._enabled_v2_capabilities(tools_config)

        if not self.tools_dir.exists() or not self.tools_dir.is_dir():
            logger.error(
                "Thư mục tools không tồn tại: %s",
                self.tools_dir,
            )
            return loaded_tools

        self._setup_import_environment()

        for fpath, is_package in self._entrypoint_candidates():
            mod = self._load_entrypoint(
                fpath,
                is_package=is_package,
            )
            if mod is None:
                continue

            if not (
                hasattr(mod, "TOOL_METADATA")
                and hasattr(mod, "run")
            ):
                logger.debug(
                    "ℹ️ [Helper Module Loaded]: %s",
                    fpath,
                )
                continue

            meta = getattr(mod, "TOOL_METADATA")
            handler = getattr(mod, "run")
            if (
                not isinstance(meta, dict)
                or "name" not in meta
                or not callable(handler)
            ):
                logger.warning(
                    "⚠️ [Tool Skipped]: %s có TOOL_METADATA/run "
                    "không hợp lệ.",
                    fpath,
                )
                continue

            manifest_version = meta.get("manifest_version")
            if manifest_version is not None:
                if manifest_version != "2.0":
                    logger.error(
                        "❌ [Tool Skipped]: %s có manifest_version "
                        "không được hỗ trợ: %r",
                        fpath,
                        manifest_version,
                    )
                    continue
                try:
                    package_entries = self._canonical_v2_entries(
                        metadata=meta,
                        handler=handler,
                        enabled=enabled_v2,
                        fpath=fpath,
                    )
                except Exception as exc:
                    logger.error(
                        "❌ [Metadata V2 Skipped]: %s: %s",
                        fpath,
                        exc,
                        exc_info=True,
                    )
                    continue

                selected_ids = set(package_entries)
                collisions = selected_ids.intersection(loaded_tools)
                collisions.update(
                    selected_ids.intersection(disabled_capability_ids)
                )
                if collisions:
                    # The whole current selected V2 package becomes disabled.
                    disabled = set(selected_ids)
                    collided_v2_origins = {
                        entry.get("file_path")
                        for capability_id in collisions
                        for entry in [loaded_tools.get(capability_id)]
                        if (
                            isinstance(entry, dict)
                            and isinstance(entry.get("metadata"), dict)
                            and entry["metadata"].get("manifest_version")
                            == "2.0"
                            and isinstance(entry.get("file_path"), str)
                        )
                    }
                    if collided_v2_origins:
                        disabled.update(
                            capability_id
                            for capability_id, entry in loaded_tools.items()
                            if (
                                isinstance(entry, dict)
                                and isinstance(entry.get("metadata"), dict)
                                and entry["metadata"].get("manifest_version")
                                == "2.0"
                                and entry.get("file_path")
                                in collided_v2_origins
                            )
                        )
                    logger.error(
                        "❌ [Metadata V2 Skipped]: %s trùng logical "
                        "capability IDs: %s; disabling collided group: %s",
                        fpath,
                        ", ".join(sorted(collisions)),
                        ", ".join(sorted(disabled)),
                    )
                    # Package-level fail-closed semantics are monotonic for
                    # the whole load pass. Existing collided groups are
                    # removed and every selected ID in the conflict component
                    # is tombstoned so later packages cannot resurrect it.
                    for capability_id in disabled:
                        loaded_tools.pop(capability_id, None)
                    disabled_capability_ids.update(disabled)
                    continue

                loaded_tools.update(package_entries)
                continue

            t_name = meta["name"]
            is_internal = t_name in self.internal_required_tools

            if t_name in disabled_capability_ids:
                logger.error(
                    "❌ [Tool Skipped]: %s uses disabled collided "
                    "capability ID %s",
                    fpath,
                    t_name,
                )
                continue

            if not is_internal:
                if t_name in blocked:
                    logger.info("🚫 [Tool Blocked]: %s", t_name)
                    continue
                if "*" not in allowed and t_name not in allowed:
                    continue

            existing_entry = loaded_tools.get(t_name)
            if (
                existing_entry is not None
                and isinstance(existing_entry, dict)
                and (
                    existing_entry.get("metadata", {}).get(
                        "manifest_version"
                    )
                    == "2.0"
                )
            ):
                source_path = existing_entry.get("file_path")
                logger.error(
                    "❌ [Tool Skipped]: %s conflicts with canonical "
                    "Metadata V2 capability %s; disabling selected exports "
                    "from package %s",
                    fpath,
                    t_name,
                    source_path,
                )
                conflicted_package_ids = [
                    capability_id
                    for capability_id, entry in loaded_tools.items()
                    if (
                        isinstance(entry, dict)
                        and entry.get("file_path") == source_path
                        and (
                            entry.get("metadata", {}).get(
                                "manifest_version"
                            )
                            == "2.0"
                        )
                    )
                ]
                for capability_id in conflicted_package_ids:
                    loaded_tools.pop(capability_id, None)
                disabled_capability_ids.update(conflicted_package_ids)
                disabled_capability_ids.add(t_name)
                continue

            loaded_tools[t_name] = {
                "metadata": meta,
                "func": handler,
                "is_internal": is_internal,
                "file_path": str(fpath),
            }

        logger.info(
            "✅ [Local Tools Loaded]: %s tools từ %s",
            len(loaded_tools),
            self.tools_dir,
        )
        return loaded_tools
