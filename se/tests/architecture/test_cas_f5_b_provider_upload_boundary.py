from __future__ import annotations

import ast
import inspect
import textwrap
from pathlib import Path

from se.src.provider.gemini.api.files import GeminiFiles
from se.src.runtimes.workflow.runtime import WorkflowRuntime


def test_f5b_generic_file_handler_remains_backward_compatible():
    source = Path("se/src/provider/handlers/file_handler.py").read_text(
        encoding="utf-8"
    )

    assert "provider.files.upload_file(" in source
    assert "provider.files.upload_file_outcome(" not in source


def test_f5b_boundary_has_no_cas_or_runtime_orchestration_caller():
    offenders: list[str] = []
    root = Path("se/src")

    for path in root.rglob("*.py"):
        normalized = path.as_posix()
        if normalized.startswith("se/src/provider/"):
            continue
        source = path.read_text(encoding="utf-8")
        if "upload_file_outcome(" in source:
            offenders.append(normalized)

    assert offenders == []


def test_f5b_gemini_boundary_is_single_attempt_and_has_no_binding_authority():
    source = textwrap.dedent(
        inspect.getsource(GeminiFiles.upload_file_outcome)
    )

    assert source.count("_upload_file_once(") == 1
    assert "FileProviderBinding" not in source
    assert "delete_file(" not in source
    assert "retry(" not in source
    assert "fallback(" not in source


def test_f5b_provider_implementation_does_not_import_cas_binding_or_storage():
    for relative_path in (
        "se/src/provider/gemini/api/files.py",
        "se/src/provider/mock/provider.py",
    ):
        source = Path(relative_path).read_text(encoding="utf-8")
        assert "FileProviderBinding" not in source
        assert "AssetRepository" not in source
        assert "ObjectStorage" not in source


def test_f5b_workflow_guard_remains_exact_positive_fail_closed_boundary():
    source = textwrap.dedent(
        inspect.getsource(WorkflowRuntime._handle_context_built)
    )
    tree = ast.parse(source)

    asset_guards = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and "contains_canonical_asset_content"
        in (ast.get_source_segment(source, node.test) or "")
    ]
    assert len(asset_guards) == 1

    guard = asset_guards[0]
    assert isinstance(guard.test, ast.Call)
    assert isinstance(guard.test.func, ast.Name)
    assert guard.test.func.id == "contains_canonical_asset_content"

    guard_source = ast.get_source_segment(source, guard) or ""
    assert "ASSET_HYDRATION_REQUIRED" in guard_source
    assert '"failure_domain": "MESSAGE_ASSET"' in guard_source
    assert '"retryable": False' in guard_source
    assert guard.body
    assert isinstance(guard.body[-1], ast.Return)
