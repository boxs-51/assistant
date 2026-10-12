from .execution import AgentExecutionRecord
from .iteration import AgentIterationRecord
from .tool_call import AgentToolCallRecord
from .tool_result import AgentToolResultRecord
from .session import AgentSessionRecord, AgentSessionMemberRecord
from .message import AgentMessageRecord
from .task import AgentTaskRecord
from .task_branch import AgentTaskBranchContextRecord, AgentTaskBranchRecord
from .task_budget import (
    TaskBudgetIncarnationAllocatorRecord,
    TaskBudgetRecord,
    TaskBudgetReservationRecord,
)
from .checkpoint import (
    AgentCheckpointPendingInvocationRecord,
    AgentExecutionCheckpointRecord,
)
from .resume_claim import AgentResumeClaimRecord
from .fork_admission import AgentTaskForkAdmissionRecord
from .retry_admission import AgentTaskRetryAdmissionRecord
from .aggregate_admission import AgentTaskAggregateAdmissionRecord
from .transcript_representation import (
    AgentTranscriptChunkRecord,
    AgentTranscriptPayloadNodeRecord,
    AgentTranscriptRepresentationRecord,
)

__all__ = [
    "AgentInstanceLiveRecord",
    "AgentInstanceIdReservationRecord",
    "AgentExecutionRecord",
    "AgentIterationRecord",
    "AgentToolCallRecord",
    "AgentToolResultRecord",
    "AgentSessionRecord",
    "AgentSessionMemberRecord",
    "AgentMessageRecord",
    "AgentTaskRecord",
    "AgentTaskBranchRecord",
    "AgentTaskBranchContextRecord",
    "TaskBudgetIncarnationAllocatorRecord",
    "TaskBudgetRecord",
    "TaskBudgetReservationRecord",
    "AgentExecutionCheckpointRecord",
    "AgentCheckpointPendingInvocationRecord",
    "AgentResumeClaimRecord",
    "AgentTaskForkAdmissionRecord",
    "AgentTaskRetryAdmissionRecord",
    "AgentTaskAggregateAdmissionRecord",
    "AgentTranscriptChunkRecord",
    "AgentTranscriptPayloadNodeRecord",
    "AgentTranscriptRepresentationRecord",
]


# Only materialize the AIC ORM when the dedicated AIC repository imports it.
# Importing it unconditionally here introduces a users FK into unrelated
# AE/UBQ Base.metadata.create_all fixtures before their user model graph is
# registered, breaking the historical R11 isolated test topology.
# from se.src.infrastructure.storage.models.sql.agent import AgentInstanceLiveRecord
# still works through this lazy symbol export.
def __getattr__(name: str):
    if name in {"AgentInstanceLiveRecord", "AgentInstanceIdReservationRecord"}:
        from . import agent_instance as _agent_instance
        return getattr(_agent_instance, name)
    raise AttributeError(name)
