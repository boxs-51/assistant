from __future__ import annotations

import ast
from pathlib import Path


CONTRACT = Path(
    "docs/context_future/"
    "CTX_F5_3K_USER_WIDE_MEMORY_STORAGE_CLASSIFICATION_CONTRACT_CDF270A9.md"
)
MEMORY = Path("se/src/context/memory.py")
MEMORY_SQL = Path("se/src/infrastructure/storage/models/sql/memory.py")
MEMORY_REPOSITORY = Path("se/src/infrastructure/storage/repositories/memory.py")
MEMORY_ADMISSION = Path(
    "se/src/infrastructure/storage/services/memory_promotion_admission.py"
)
MIGRATION_29A = Path(
    "se/src/infrastructure/storage/migrations/sql/versions/"
    "29a_crt1_capability_invocation_target.py"
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _normalized(path: Path) -> str:
    return " ".join(_read(path).replace("`", "").split())


def _class(source: str, name: str) -> ast.ClassDef:
    for node in ast.parse(source).body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"class {name} not found")


def _annotated_names(cls: ast.ClassDef) -> set[str]:
    return {
        node.target.id
        for node in cls.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
    }


def test_ctx_f5_3k_freezes_exact_zero_production_claim() -> None:
    contract = _normalized(CONTRACT)

    for phrase in (
        "stage = CTX-F5-3K",
        "name = USER_WIDE Memory Storage Classification Contract",
        "development baseline = cdf270a926b154f40a0ae3f5db1d80cec4fd8d07",
        "baseline Architecture #2290 / 37620056189 = GREEN/GREEN",
        "class = CONTRACT + ARCHITECTURE EVIDENCE / ZERO PRODUCTION",
        "contract CLAIM = ACTIVE",
        "production PRE-CLAIM = HOLD / NOT RELEASED",
        "production CLAIM = NONE",
        "merge authority = NONE",
        "exact changed paths = 2 NEW / 2",
        "se/src/** delta = ZERO",
        "schema/migration delta = ZERO",
        "repository/model delta = ZERO",
        "runtime/API/router delta = ZERO",
    ):
        assert phrase in contract

    assert (
        "CTX_F5_3K_USER_WIDE_MEMORY_STORAGE_CLASSIFICATION_CONTRACT_CDF270A9.md"
        in contract
    )
    assert (
        "test_ctx_f5_3k_user_wide_memory_storage_classification_contract.py"
        in contract
    )


def test_ctx_f5_3k_proves_preimplementation_memory_shape_is_owner_only() -> None:
    domain = _class(_read(MEMORY), "MemoryRecord")
    row = _class(_read(MEMORY_SQL), "MemoryRecordRow")

    domain_fields = _annotated_names(domain)
    row_fields = _annotated_names(row)

    assert "owner_user_id" in domain_fields
    assert "owner_user_id" in row_fields
    assert "memory_scope" not in domain_fields
    assert "memory_scope" not in row_fields
    assert "agent_instance_id" not in domain_fields
    assert "agent_instance_id" not in row_fields


def test_ctx_f5_3k_freezes_legacy_null_not_user_wide_semantics() -> None:
    contract = _normalized(CONTRACT)

    for phrase in (
        "NULL / absent future memory_scope",
        "= LEGACY OWNER-ONLY / UNCLASSIFIED",
        "!= USER_WIDE",
        "!= AGENT_PRIVATE",
        "MUST NOT:",
        "assign a database default of USER_WIDE",
        "backfill existing rows to USER_WIDE",
        "rewrite, remint, relabel, or delete existing Memory rows",
        "no database default",
        "no automatic backfill",
    ):
        assert phrase in contract


def test_ctx_f5_3k_freezes_first_value_and_agent_private_hold() -> None:
    contract = _normalized(CONTRACT)

    for phrase in (
        "first post-3J production representation may implement exactly one explicit durable scope value",
        "USER_WIDE",
        "AGENT_PRIVATE production remains HARD HOLD",
        "AGENT_PRIVATE storage representation",
        "agent_instance_id",
        "remain CLOSED",
    ):
        assert phrase in contract


def test_ctx_f5_3k_freezes_current_migration_parent_without_mutating_it() -> None:
    contract = _normalized(CONTRACT)
    migration = _read(MIGRATION_29A)

    assert 'revision: str = "29a_crt1_capability_invocation_target"' in migration
    assert (
        'down_revision: Union[str, None] = "28a_tbo1_task_policy_representation"'
        in migration
    )
    assert "29a_crt1_capability_invocation_target" in contract
    assert "single linear child" in contract
    assert "fresh PRE-CLAIM must re-resolve the exact migration parent" in contract


def test_ctx_f5_3k_freezes_identity_stability_and_replay_conflict() -> None:
    contract = _normalized(CONTRACT)
    memory = _read(MEMORY)
    tree = ast.parse(memory)

    identity_domain = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "MEMORY_IDENTITY_DOMAIN"
            for target in node.targets
        )
    )
    assert isinstance(identity_domain.value, ast.Constant)
    assert identity_domain.value.value == "ctx-memory-v1"

    memory_id_function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "memory_id"
    )
    material_assignment = next(
        node
        for node in ast.walk(memory_id_function)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "material"
            for target in node.targets
        )
    )
    assert isinstance(material_assignment.value, ast.Dict)
    material_keys = [
        key.value
        for key in material_assignment.value.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    ]
    assert material_keys == [
        "owner_user_id",
        "promotion_authority_id",
        "source_context_source_id",
        "content_digest",
        "memory_schema_version",
    ]

    material_values = dict(zip(material_keys, material_assignment.value.values))
    source_context_value = material_values["source_context_source_id"]
    assert isinstance(source_context_value, ast.Attribute)
    assert source_context_value.attr == "context_source_id"
    assert isinstance(source_context_value.value, ast.Name)
    assert source_context_value.value.id == "source_ref"

    memory_id_source = ast.get_source_segment(memory, memory_id_function)
    assert memory_id_source is not None
    assert 'MEMORY_IDENTITY_DOMAIN.encode("utf-8")' in memory_id_source
    assert 'exclude={"created_at"}' in memory

    for phrase in (
        "Legacy Memory identities MUST remain stable",
        "MUST NOT remint or rewrite existing memory_id values",
        "existing v1 Memory identity material remains the compatibility baseline",
        "explicitly stored classification is immutable record material",
        "participate in replay/conflict comparison",
        "fail closed rather than converge silently",
    ):
        assert phrase in contract


def test_ctx_f5_3k_preserves_promotion_admission_and_p3_boundary() -> None:
    contract = _normalized(CONTRACT)
    admission = _read(MEMORY_ADMISSION)

    assert "create_memory_record(" in admission
    assert "memory_scope" not in admission

    for phrase in (
        "Memory scope classification does not mint source authority",
        "The first representation slice MUST NOT change:",
        "source-proof authority",
        "promotion intent authority",
        "reservation issue/recovery",
        "atomic Memory admission",
        "current P3 caller behavior",
        "Caller-provided scope alone cannot authorize Memory creation or promotion",
        "no automatic USER_WIDE promotion is released",
    ):
        assert phrase in contract


def test_ctx_f5_3k_keeps_read_visibility_and_external_authority_closed() -> None:
    contract = _normalized(CONTRACT)

    for phrase in (
        "persisted/global Memory listing",
        "Memory search",
        "Memory retrieval API",
        "ContextBuilder injection",
        "Working Set composition",
        "ContextSnapshot persistence/use",
        "automatic model visibility",
        "USER_WIDE storage classification does not mean every Agent",
        "Issue #31/R11 retains transcript/checkpoint read-liveness",
        "CAS #74 retains ASSET/FileAsset/FileBlob/ObjectStorage/provider lifecycle",
        "AIC/APR own Agent identity/profile/runtime authority",
        "CTX-F5-3K transfers none of those authorities",
    ):
        assert phrase in contract


def test_ctx_f5_3k_keeps_repository_and_production_unmodified() -> None:
    contract = _normalized(CONTRACT)
    repository = _read(MEMORY_REPOSITORY)

    assert "class DurableMemoryRecordRepository" in repository
    assert "memory_scope" not in repository

    for phrase in (
        "does not release:",
        "any se/src/** implementation",
        "Alembic migration",
        "MemoryRecord field changes",
        "MemoryRecordRow field changes",
        "repository changes",
        "promotion/admission changes",
        "USER_WIDE producer/caller",
        "AGENT_PRIVATE implementation",
        "Memory read/search/access APIs",
    ):
        assert phrase in contract
