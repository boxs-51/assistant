"""S02 additive RED-first Alembic graph contract; no existing oracle is modified.

Frozen pre-fix alpha: 03905175640b0e893f0d30f65019a2eb17c14d89
S01 observed two heads 31b and 31t. The one-head expectation below MUST
remain red until the actual Alembic graph has been repaired safely.
"""
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


ROOT = Path(__file__).resolve().parents[3]
BASE = "30a_ctx_f5_user_wide_memory_scope"
AIC_31A = "31a_aic0_agent_instance_identity"
AIC_31B = "31b_aic0_pg_guard_forward"
TERMINAL_31T = "31t_direct_terminal_permission"
MERGE = "32a_alpha_31b_31t_merge"


def _scripts() -> ScriptDirectory:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option(
        "script_location",
        str(ROOT / "se/src/infrastructure/storage/migrations/sql"),
    )
    return ScriptDirectory.from_config(config)


def test_s02_original_lineage_remains_immutable() -> None:
    """Original installed migration IDs may not be reparented or removed."""
    scripts = _scripts()
    assert scripts.get_revision(AIC_31A).down_revision == BASE
    assert scripts.get_revision(AIC_31B).down_revision == AIC_31A
    assert scripts.get_revision(TERMINAL_31T).down_revision == BASE


def test_s02_new_merge_reconciles_single_head_without_reparenting() -> None:
    """RED on original alpha: two existing heads must become one new merge."""
    scripts = _scripts()
    heads = scripts.get_heads()
    assert len(heads) == 1, f"Two-head bug persists: {heads!r}"
    merge = scripts.get_revision(MERGE)
    assert merge is not None, "Required explicit new merge revision absent"
    assert set(merge.down_revision) == {AIC_31B, TERMINAL_31T}
    # Future stages may build on MERGE without rewriting protected ancestors.
    assert scripts.get_revision(AIC_31B).down_revision == AIC_31A
    assert scripts.get_revision(TERMINAL_31T).down_revision == BASE
