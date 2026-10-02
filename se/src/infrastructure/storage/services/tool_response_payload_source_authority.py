from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, AsyncContextManager, Callable

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from se.src.context.memory_promotion import (
    MemoryPromotionProofScope,
    SourcePromotionAuthorityPort,
    SourcePromotionProof,
)
from se.src.context.source_identity import (
    ContextSourceKind,
    ContextSourceRef,
    create_context_source_ref,
    validate_context_source_ref_integrity,
)
from se.src.context.tool_response_payload import (
    COMMITTED_RESULT_STATE,
    TOOL_RESPONSE_PAYLOAD_SCHEMA_VERSION,
    ToolResponsePayload,
    canonical_payload_bytes,
    create_tool_response_payload,
)
from se.src.infrastructure.storage.models.sql.agent.execution import (
    AgentExecutionRecord,
)
from se.src.infrastructure.storage.models.sql.agent.tool_call import (
    AgentToolCallRecord,
)
from se.src.infrastructure.storage.models.sql.agent.tool_result import (
    AgentToolResultRecord,
)
from se.src.infrastructure.storage.models.sql.capability.invocation import (
    CapabilityInvocationRecord,
)
from se.src.infrastructure.storage.models.sql.chat_data.session import Session


SessionContextFactory = Callable[[], AsyncContextManager[AsyncSession]]

_AUTHORITY_STATE_DOMAIN = "ctx-trp-source-state-v1"
_PROOF_RECEIPT_DOMAIN = "ctx-trp-proof-receipt-v1"


class ToolResponsePayloadSourceAuthorityError(RuntimeError):
    """Base trusted-source re-proof failure for TOOL_RESPONSE_PAYLOAD."""


class ToolResponsePayloadSourceRejectedError(
    ToolResponsePayloadSourceAuthorityError
):
    """Durable evidence or caller claim is not eligible for a trusted proof."""


class ToolResponsePayloadSourceUnavailableError(
    ToolResponsePayloadSourceAuthorityError
):
    """Durable source evidence could not be read safely."""


@dataclass(frozen=True, slots=True)
class TrustedToolResponsePromotionMaterial:
    """Detached trusted promotion material from one durable tool-result read."""

    source_proof: SourcePromotionProof
    _payload: ToolResponsePayload

    @property
    def content_snapshot(self) -> Any:
        """Return a fresh canonical JSON snapshot detached from trusted state."""
        return self._payload.model_dump(mode="json")["content"]

    @property
    def content_digest(self) -> str:
        """Return the digest owned by the internally frozen canonical payload."""
        return self._payload.content_digest


def _canonical_string(name: str, value: object) -> str:
    if not isinstance(value, str):
        raise ToolResponsePayloadSourceRejectedError(
            f"{name} must be a string"
        )
    normalized = value.strip()
    if not normalized or normalized != value:
        raise ToolResponsePayloadSourceRejectedError(
            f"{name} must be a canonical non-empty string"
        )
    return normalized


def _domain_hash(domain: str, material: object) -> str:
    return hashlib.sha256(
        domain.encode("utf-8")
        + b"\x00"
        + canonical_payload_bytes(material)
    ).hexdigest()


class DurableToolResponsePayloadSourceAuthority(
    SourcePromotionAuthorityPort
):
    """Read-only durable source authority for one TOOL_RESPONSE_PAYLOAD."""

    def __init__(self, session_factory: SessionContextFactory) -> None:
        self._session_factory = session_factory

    async def reprove_for_memory_promotion(
        self,
        *,
        source_ref: ContextSourceRef,
        owner_user_id: str,
    ) -> SourcePromotionProof:
        """Re-prove one immutable successful tool result from durable evidence."""
        material = await self.read_trusted_promotion_material(
            source_ref=source_ref,
            owner_user_id=owner_user_id,
        )
        return material.source_proof

    async def read_trusted_promotion_material(
        self,
        *,
        source_ref: ContextSourceRef,
        owner_user_id: str,
    ) -> TrustedToolResponsePromotionMaterial:
        """Read one immutable promotion material set from durable evidence."""
        try:
            validate_context_source_ref_integrity(source_ref)
        except ValueError as exc:
            raise ToolResponsePayloadSourceRejectedError(
                "source_ref failed ContextSourceRef integrity"
            ) from exc

        requested_owner = _canonical_string(
            "owner_user_id",
            owner_user_id,
        )
        if source_ref.source_kind is not ContextSourceKind.TOOL_RESPONSE_PAYLOAD:
            raise ToolResponsePayloadSourceRejectedError(
                "source_ref must be TOOL_RESPONSE_PAYLOAD"
            )
        if source_ref.authority_version is not None:
            raise ToolResponsePayloadSourceRejectedError(
                "TOOL_RESPONSE_PAYLOAD must not use authority_version"
            )
        if source_ref.owner_user_id != requested_owner:
            raise ToolResponsePayloadSourceRejectedError(
                "source_ref owner does not match requested owner"
            )
        source_session_id = _canonical_string(
            "source_ref.session_id",
            source_ref.session_id,
        )
        if source_ref.task_id is not None or source_ref.branch_id is not None:
            raise ToolResponsePayloadSourceRejectedError(
                "TOOL_RESPONSE_PAYLOAD source must not claim task/branch identity"
            )
        if source_ref.source_state != COMMITTED_RESULT_STATE:
            raise ToolResponsePayloadSourceRejectedError(
                "source_ref must claim COMMITTED source state"
            )

        source_result_id = _canonical_string(
            "source_ref.metadata.source_result_id",
            source_ref.metadata.get("source_result_id"),
        )

        try:
            async with self._session_factory() as session:
                result = await session.get(
                    AgentToolResultRecord,
                    source_result_id,
                )
                if result is None:
                    raise ToolResponsePayloadSourceRejectedError(
                        "durable tool result was not found"
                    )

                execution = await session.get(
                    AgentExecutionRecord,
                    result.execution_id,
                )
                if execution is None:
                    raise ToolResponsePayloadSourceRejectedError(
                        "durable agent execution was not found"
                    )

                canonical_session = await session.get(
                    Session,
                    execution.session_id,
                )
                if canonical_session is None:
                    raise ToolResponsePayloadSourceRejectedError(
                        "canonical chat Session was not found"
                    )

                invocation = await session.get(
                    CapabilityInvocationRecord,
                    result.invocation_id,
                )
                if invocation is None:
                    raise ToolResponsePayloadSourceRejectedError(
                        "durable capability invocation was not found"
                    )

                tool_call_query = await session.execute(
                    select(AgentToolCallRecord).where(
                        AgentToolCallRecord.execution_id
                        == result.execution_id,
                        AgentToolCallRecord.tool_call_id
                        == result.tool_call_id,
                    )
                )
                tool_call = tool_call_query.scalar_one_or_none()
                if tool_call is None:
                    raise ToolResponsePayloadSourceRejectedError(
                        "durable agent tool call was not found"
                    )

                try:
                    return self._build_material(
                        source_ref=source_ref,
                        requested_owner=requested_owner,
                        source_session_id=source_session_id,
                        source_result_id=source_result_id,
                        result=result,
                        execution=execution,
                        canonical_session=canonical_session,
                        invocation=invocation,
                        tool_call=tool_call,
                    )
                except ValueError as exc:
                    raise ToolResponsePayloadSourceRejectedError(
                        "durable source evidence failed canonical reconstruction"
                    ) from exc
        except SQLAlchemyError as exc:
            raise ToolResponsePayloadSourceUnavailableError(
                "durable TOOL_RESPONSE_PAYLOAD evidence is unavailable"
            ) from exc

    @staticmethod
    def _build_material(
        *,
        source_ref: ContextSourceRef,
        requested_owner: str,
        source_session_id: str,
        source_result_id: str,
        result: AgentToolResultRecord,
        execution: AgentExecutionRecord,
        canonical_session: Session,
        invocation: CapabilityInvocationRecord,
        tool_call: AgentToolCallRecord,
    ) -> TrustedToolResponsePromotionMaterial:
        result_id = _canonical_string("result.id", result.id)
        result_execution_id = _canonical_string(
            "result.execution_id",
            result.execution_id,
        )
        result_iteration_id = _canonical_string(
            "result.iteration_id",
            result.iteration_id,
        )
        result_invocation_id = _canonical_string(
            "result.invocation_id",
            result.invocation_id,
        )
        result_tool_call_id = _canonical_string(
            "result.tool_call_id",
            result.tool_call_id,
        )
        result_capability_id = _canonical_string(
            "result.capability_id",
            result.capability_id,
        )

        if result_id != source_result_id:
            raise ToolResponsePayloadSourceRejectedError(
                "durable result identity does not match caller lookup hint"
            )
        if (
            result.commit_state != COMMITTED_RESULT_STATE
            or result.success is not True
            or result.error_code is not None
            or result.error_message is not None
            or result.retryable is not False
        ):
            raise ToolResponsePayloadSourceRejectedError(
                "durable tool result is not an exact successful COMMITTED source"
            )

        execution_id = _canonical_string("execution.id", execution.id)
        execution_session_id = _canonical_string(
            "execution.session_id",
            execution.session_id,
        )
        session_id = _canonical_string(
            "Session.id",
            canonical_session.id,
        )
        session_owner = _canonical_string(
            "Session.user_id",
            canonical_session.user_id,
        )

        if execution_id != result_execution_id:
            raise ToolResponsePayloadSourceRejectedError(
                "result/execution identity mismatch"
            )
        if not (
            session_id
            == execution_session_id
            == source_session_id
        ):
            raise ToolResponsePayloadSourceRejectedError(
                "canonical Session/execution/source_ref session mismatch"
            )
        if not (
            session_owner
            == requested_owner
            == source_ref.owner_user_id
        ):
            raise ToolResponsePayloadSourceRejectedError(
                "canonical Session owner mismatch"
            )

        invocation_id = _canonical_string(
            "invocation.invocation_id",
            invocation.invocation_id,
        )
        invocation_execution_id = _canonical_string(
            "invocation.execution_id",
            invocation.execution_id,
        )
        invocation_session_id = _canonical_string(
            "invocation.session_id",
            invocation.session_id,
        )
        invocation_owner = _canonical_string(
            "invocation.owner_user_id",
            invocation.owner_user_id,
        )
        invocation_tool_call_id = _canonical_string(
            "invocation.tool_call_id",
            invocation.tool_call_id,
        )
        invocation_capability_id = _canonical_string(
            "invocation.capability_id",
            invocation.capability_id,
        )

        if (
            invocation_id != result_invocation_id
            or invocation_execution_id != result_execution_id
            or invocation_session_id != execution_session_id
            or invocation_owner != session_owner
            or invocation_tool_call_id != result_tool_call_id
            or invocation_capability_id != result_capability_id
        ):
            raise ToolResponsePayloadSourceRejectedError(
                "durable capability invocation lineage mismatch"
            )

        tool_call_execution_id = _canonical_string(
            "tool_call.execution_id",
            tool_call.execution_id,
        )
        tool_call_iteration_id = _canonical_string(
            "tool_call.iteration_id",
            tool_call.iteration_id,
        )
        tool_call_id = _canonical_string(
            "tool_call.tool_call_id",
            tool_call.tool_call_id,
        )
        tool_call_invocation_id = _canonical_string(
            "tool_call.invocation_id",
            tool_call.invocation_id,
        )
        tool_call_capability_id = _canonical_string(
            "tool_call.capability_id",
            tool_call.capability_id,
        )

        if (
            tool_call_execution_id != result_execution_id
            or tool_call_iteration_id != result_iteration_id
            or tool_call_id != result_tool_call_id
            or tool_call_invocation_id != result_invocation_id
            or tool_call_capability_id != result_capability_id
        ):
            raise ToolResponsePayloadSourceRejectedError(
                "durable agent tool-call lineage mismatch"
            )

        # CTX-F1 identity/canonicalization stays delegated to the existing
        # ToolResponsePayload primitive. Its implementation applies
        # canonical_payload_bytes(result.output) and tool_response_payload_id(...).
        payload = create_tool_response_payload(
            source_result_id=result_id,
            invocation_id=result_invocation_id,
            execution_id=result_execution_id,
            tool_call_id=result_tool_call_id,
            logical_capability_id=result_capability_id,
            content=result.output,
            source_commit_state=COMMITTED_RESULT_STATE,
            owner_user_id=session_owner,
            session_id=execution_session_id,
            payload_schema_version=TOOL_RESPONSE_PAYLOAD_SCHEMA_VERSION,
        )
        content_digest = payload.content_digest
        payload_id = payload.payload_id
        if payload_id != source_ref.authority_id:
            raise ToolResponsePayloadSourceRejectedError(
                "reconstructed payload identity does not match source_ref authority"
            )

        reconstructed_ref = create_context_source_ref(
            source_kind=ContextSourceKind.TOOL_RESPONSE_PAYLOAD,
            authority_id=payload_id,
            authority_version=None,
            owner_user_id=session_owner,
            session_id=execution_session_id,
            task_id=None,
            branch_id=None,
            source_created_at=None,
            source_state=COMMITTED_RESULT_STATE,
            metadata={"source_result_id": result_id},
        )
        validate_context_source_ref_integrity(reconstructed_ref)
        if reconstructed_ref.context_source_id != source_ref.context_source_id:
            raise ToolResponsePayloadSourceRejectedError(
                "reconstructed canonical source identity does not match caller claim"
            )

        authority_state_token = _domain_hash(
            _AUTHORITY_STATE_DOMAIN,
            {
                "result_id": result_id,
                "execution_id": result_execution_id,
                "iteration_id": result_iteration_id,
                "invocation_id": result_invocation_id,
                "tool_call_id": result_tool_call_id,
                "capability_id": result_capability_id,
                "commit_state": COMMITTED_RESULT_STATE,
                "success": True,
                "error_code": None,
                "error_message": None,
                "retryable": False,
                "payload_schema_version": TOOL_RESPONSE_PAYLOAD_SCHEMA_VERSION,
                "content_digest": content_digest,
                "payload_id": payload_id,
                "owner_user_id": session_owner,
                "session_id": execution_session_id,
            },
        )
        proof_receipt_id = _domain_hash(
            _PROOF_RECEIPT_DOMAIN,
            {
                "context_source_id": reconstructed_ref.context_source_id,
                "authority_id": payload_id,
                "source_result_id": result_id,
                "owner_user_id": session_owner,
                "scope": MemoryPromotionProofScope.MEMORY_PROMOTION.value,
                "authority_state_token": authority_state_token,
            },
        )

        source_proof = SourcePromotionProof(
            source_ref_snapshot=reconstructed_ref,
            proof_receipt_id=proof_receipt_id,
            authority_state_token=authority_state_token,
            scope=MemoryPromotionProofScope.MEMORY_PROMOTION,
        )
        return TrustedToolResponsePromotionMaterial(
            source_proof=source_proof,
            _payload=payload,
        )
