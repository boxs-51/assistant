from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BranchDiscardResult:
    task_id: str
    branch_id: str
    branch_revision: int
    task_budget_revision: int


@dataclass(frozen=True, slots=True)
class TaskAdoptionResult:
    task_id: str
    task_revision: int
    selected_branch_id: str
    selected_execution_id: str
    task_budget_revision: int
    superseded_branch_ids: tuple[str, ...]
    rejected_resume_claim_ids: tuple[str, ...]


__all__ = ["BranchDiscardResult", "TaskAdoptionResult"]
