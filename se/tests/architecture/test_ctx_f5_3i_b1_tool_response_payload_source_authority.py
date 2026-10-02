from __future__ import annotations

import ast
import inspect
from pathlib import Path

from se.src.context.memory_promotion import SourcePromotionAuthorityPort
from se.src.infrastructure.storage.services.tool_response_payload_source_authority import (
    DurableToolResponsePayloadSourceAuthority,
)


SERVICE = Path(
    "se/src/infrastructure/storage/services/"
    "tool_response_payload_source_authority.py"
)
CONTRACT = Path(
    "docs/context_future/"
    "CTX_F5_3I_B1_TOOL_RESPONSE_PAYLOAD_SOURCE_AUTHORITY.md"
)


def _semantic_contract() -> str:
    text = " ".join(CONTRACT.read_text(encoding="utf-8").split())
    return text.replace(chr(96), "").replace("*", "")


def _service_tree() -> ast.Module:
    return ast.parse(SERVICE.read_text(encoding="utf-8"))


def _class_node(tree: ast.Module, name: str) -> ast.ClassDef:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"class {name!r} not found")


def _function_node(
    class_node: ast.ClassDef,
    name: str,
) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in class_node.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"method {name!r} not found")


def _call_name(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _call_names(node: ast.AST) -> list[str]:
    return [
        name
        for item in ast.walk(node)
        if isinstance(item, ast.Call)
        if (name := _call_name(item)) is not None
    ]


def test_b1_protocol_signature_and_exact_service_class():
    assert (
        SourcePromotionAuthorityPort
        in DurableToolResponsePayloadSourceAuthority.__mro__
    )
    signature = inspect.signature(
        DurableToolResponsePayloadSourceAuthority.reprove_for_memory_promotion
    )
    assert tuple(signature.parameters) == (
        "self",
        "source_ref",
        "owner_user_id",
    )
    assert (
        signature.parameters["source_ref"].kind
        is inspect.Parameter.KEYWORD_ONLY
    )
    assert (
        signature.parameters["owner_user_id"].kind
        is inspect.Parameter.KEYWORD_ONLY
    )


def test_b1_owns_one_read_only_session_and_no_write_or_lock_path():
    source = SERVICE.read_text(encoding="utf-8")
    tree = ast.parse(source)

    assert source.count(
        "async with self._session_factory() as session:"
    ) == 1
    assert source.count("await session.get(") == 4
    assert source.count("await session.execute(") == 1
    assert "select(AgentToolCallRecord).where(" in source

    forbidden_source = (
        "await session.commit(",
        "await session.flush(",
        "await session.rollback(",
        "session.add(",
        "session.delete(",
        ".with_for_update(",
        "SqlAlchemyUnitOfWork",
        "DatabaseDriver",
        "CapabilityInvocationRepository",
        "AgentRepository",
        "SessionRepository",
        "asyncio.sleep",
        "while True",
    )
    for fragment in forbidden_source:
        assert fragment not in source

    sqlalchemy_imports = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "sqlalchemy"
        ):
            sqlalchemy_imports.update(alias.name for alias in node.names)

    assert sqlalchemy_imports == {"select"}


def test_b1_exact_success_lineage_and_delegated_payload_reconstruction_are_present():
    source = SERVICE.read_text(encoding="utf-8")
    tree = _service_tree()
    authority = _class_node(
        tree,
        "DurableToolResponsePayloadSourceAuthority",
    )
    reprove = _function_node(
        authority,
        "reprove_for_memory_promotion",
    )
    build_material = _function_node(authority, "_build_material")

    required = (
        "result.commit_state != COMMITTED_RESULT_STATE",
        "result.success is not True",
        "result.error_code is not None",
        "result.error_message is not None",
        "result.retryable is not False",
        "session_owner",
        "requested_owner",
        "source_ref.owner_user_id",
        "invocation.execution_id",
        "invocation.session_id",
        "invocation.owner_user_id",
        "invocation.tool_call_id",
        "invocation.capability_id",
        "tool_call.execution_id",
        "tool_call.iteration_id",
        "tool_call.tool_call_id",
        "tool_call.invocation_id",
        "tool_call.capability_id",
        "payload_schema_version=TOOL_RESPONSE_PAYLOAD_SCHEMA_VERSION",
        "payload_id != source_ref.authority_id",
    )
    for phrase in required:
        assert phrase in source

    awaited_material_reads = [
        item
        for item in ast.walk(reprove)
        if isinstance(item, ast.Await)
        and isinstance(item.value, ast.Call)
        and isinstance(item.value.func, ast.Attribute)
        and isinstance(item.value.func.value, ast.Name)
        and item.value.func.value.id == "self"
        and item.value.func.attr == "read_trusted_promotion_material"
    ]
    assert len(awaited_material_reads) == 1

    returned_source_proofs = [
        item
        for item in ast.walk(reprove)
        if isinstance(item, ast.Return)
        and isinstance(item.value, ast.Attribute)
        and isinstance(item.value.value, ast.Name)
        and item.value.value.id == "material"
        and item.value.attr == "source_proof"
    ]
    assert len(returned_source_proofs) == 1

    build_calls = _call_names(build_material)
    assert build_calls.count("create_tool_response_payload") == 1
    assert "canonical_payload_bytes" not in build_calls
    assert "tool_response_payload_id" not in build_calls

    payload_imports = {
        alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module == "se.src.context.tool_response_payload"
        for alias in node.names
    }
    assert "create_tool_response_payload" in payload_imports
    assert "tool_response_payload_id" not in payload_imports


def test_b1_reconstructs_sanitized_snapshot_instead_of_reusing_caller_ref():
    source = SERVICE.read_text(encoding="utf-8")

    assert "reconstructed_ref = create_context_source_ref(" in source
    assert "source_kind=ContextSourceKind.TOOL_RESPONSE_PAYLOAD" in source
    assert "authority_id=payload_id" in source
    assert "authority_version=None" in source
    assert "owner_user_id=session_owner" in source
    assert "session_id=execution_session_id" in source
    assert "task_id=None" in source
    assert "branch_id=None" in source
    assert "source_created_at=None" in source
    assert "source_state=COMMITTED_RESULT_STATE" in source
    assert 'metadata={"source_result_id": result_id}' in source
    assert "validate_context_source_ref_integrity(reconstructed_ref)" in source
    assert (
        "reconstructed_ref.context_source_id != source_ref.context_source_id"
        in source
    )
    assert "source_ref_snapshot=reconstructed_ref" in source
    assert "source_ref_snapshot=source_ref" not in source


def test_b1_domains_and_deterministic_material_are_frozen():
    source = SERVICE.read_text(encoding="utf-8")

    assert (
        '_AUTHORITY_STATE_DOMAIN = "ctx-trp-source-state-v1"'
        in source
    )
    assert (
        '_PROOF_RECEIPT_DOMAIN = "ctx-trp-proof-receipt-v1"'
        in source
    )
    for phrase in (
        '"result_id": result_id',
        '"execution_id": result_execution_id',
        '"iteration_id": result_iteration_id',
        '"invocation_id": result_invocation_id',
        '"tool_call_id": result_tool_call_id',
        '"capability_id": result_capability_id',
        '"commit_state": COMMITTED_RESULT_STATE',
        '"success": True',
        '"error_code": None',
        '"error_message": None',
        '"retryable": False',
        '"content_digest": content_digest',
        '"payload_id": payload_id',
        '"owner_user_id": session_owner',
        '"session_id": execution_session_id',
        '"context_source_id": reconstructed_ref.context_source_id',
        '"scope": MemoryPromotionProofScope.MEMORY_PROMOTION.value',
        '"authority_state_token": authority_state_token',
    ):
        assert phrase in source


def test_b1_error_surface_maps_storage_errors_without_swallowing_cancellation():
    source = SERVICE.read_text(encoding="utf-8")

    assert "class ToolResponsePayloadSourceRejectedError(" in source
    assert "class ToolResponsePayloadSourceUnavailableError(" in source
    assert "except SQLAlchemyError as exc:" in source
    assert "except asyncio.CancelledError" not in source
    assert "asyncio" not in {
        alias.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Import)
        for alias in node.names
    }


def test_b1_contract_freezes_exact_five_file_scope_and_closed_boundaries():
    contract = _semantic_contract()

    required = (
        "class = PRODUCTION / TRUSTED SOURCE AUTHORITY SERVICE ONLY",
        "claim base = 16e93e72d005f952c0f405522b37ef3e6fba7522",
        "The complete candidate is therefore exactly five files",
        "Schema delta: ZERO",
        "Migration delta: ZERO",
        "Runtime/container/API wiring delta: ZERO",
        "Caller source_ref is lookup/claim material only",
        "source_created_at = None",
        'metadata = exactly {"source_result_id": result.id}',
        "ctx-trp-source-state-v1",
        "ctx-trp-proof-receipt-v1",
        "asyncio.CancelledError propagates unchanged",
        "CAS / UBQ / R6 / R7 / R11 / R12 / Issue #156 authority transfer = NONE",
        "reservation/Memory admission = CLOSED",
        "retrieval/ContextBuilder/model visibility = CLOSED",
        "Production merge is not authorized",
    )
    for phrase in required:
        assert phrase in contract
