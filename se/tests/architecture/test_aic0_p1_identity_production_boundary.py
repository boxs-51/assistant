"""AIC P1A is nonwired, with no API/AE/CTX/CAS/UBQ authority transfer."""
from pathlib import Path
import ast
import importlib

ROOT = Path(__file__).resolve().parents[3]
NEW = [
    "se/src/domain/schemas/agent_instance.py",
    "se/src/infrastructure/storage/models/sql/agent/agent_instance.py",
    "se/src/infrastructure/storage/repositories/agent_instance.py",
    "se/src/application/agent_instance.py",
    "se/src/infrastructure/storage/migrations/sql/versions/31a_aic0_agent_instance_identity.py",
]


def test_aic_modules_parse_and_exist():
    for rel in NEW:
        file = ROOT / rel
        assert file.is_file(), rel
        ast.parse(file.read_text(encoding="utf-8"), filename=str(file))


def test_aic_has_no_route_or_execution_activation():
    facade = (ROOT / "se/src/application/agent_instance.py").read_text(encoding="utf-8")
    for prohibited in ("APIRouter", "FastAPI(", "AgentExecution(", "TaskBudget(", "create_task(", "run_agent("):
        assert prohibited not in facade
    repo = (ROOT / "se/src/infrastructure/storage/repositories/agent_instance.py").read_text(encoding="utf-8")
    assert "session.commit(" not in repo
    assert "AsyncSession(" not in repo
    assert "FOR SHARE" in repo


def test_ubq_history_triggers_not_modified_and_aic_owner_guard():
    migration = (ROOT / NEW[-1]).read_text(encoding="utf-8")
    assert "AIC_LIVE_USER_DELETE_DENIED" in migration
    assert "BEFORE DELETE ON users" in migration
    assert "AIC_ID_NONREUSE" in migration
    assert "DROP TRIGGER IF EXISTS trg_ubq" not in migration
    assert "AIC_DOWNGRADE_DENIED_NONREUSABLE_IDS" in migration


def test_uow_still_exposes_original_repositories():
    text = (ROOT / "se/src/infrastructure/storage/core/unit_of_work.py").read_text(encoding="utf-8")
    assert "self.agent_instances = AgentInstanceRepository(self.session)" in text
    for old in ("self.users = UserRepository(self.session)",
                "self.agents = AgentRepository(self.session)",
                "self.user_budgets = UserBudgetRepository(self.session)"):
        assert old in text
