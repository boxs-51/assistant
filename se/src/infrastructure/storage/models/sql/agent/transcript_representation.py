from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ....transcript_representation import HARD_MAX_DELTA_DEPTH


class AgentTranscriptChunkRecord(Base):
    __tablename__ = "agent_transcript_chunks"
    __table_args__ = (
        CheckConstraint(
            "message_count >= 0",
            name="ck_agent_transcript_chunks_message_count_nonnegative",
        ),
        CheckConstraint(
            "canonical_bytes >= 0",
            name="ck_agent_transcript_chunks_canonical_bytes_nonnegative",
        ),
    )

    chunk_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    payload: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    message_count: Mapped[int] = mapped_column(Integer, nullable=False)
    canonical_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AgentTranscriptPayloadNodeRecord(Base):
    __tablename__ = "agent_transcript_payload_nodes"
    __table_args__ = (
        CheckConstraint(
            "logical_message_count >= 0",
            name="ck_agent_transcript_payload_nodes_count_nonnegative",
        ),
        CheckConstraint(
            "parent_payload_root_ref IS NULL "
            "OR parent_payload_root_ref != payload_root_ref",
            name="ck_agent_transcript_payload_nodes_not_self_parent",
        ),
    )

    payload_root_ref: Mapped[str] = mapped_column(String(64), primary_key=True)
    parent_payload_root_ref: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey(
            "agent_transcript_payload_nodes.payload_root_ref",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    chunk_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("agent_transcript_chunks.chunk_id", ondelete="RESTRICT"),
        nullable=False,
    )
    logical_message_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AgentTranscriptRepresentationRecord(Base):
    __tablename__ = "agent_transcript_representations"
    __table_args__ = (
        CheckConstraint(
            "transcript_version >= 0",
            name="ck_agent_transcript_representations_version_nonnegative",
        ),
        CheckConstraint(
            "kind IN ('FULL', 'DELTA')",
            name="ck_agent_transcript_representations_kind",
        ),
        CheckConstraint(
            "delta_depth >= 0 AND delta_depth <= "
            + str(HARD_MAX_DELTA_DEPTH),
            name="ck_agent_transcript_representations_depth",
        ),
        CheckConstraint(
            "logical_message_count >= 0",
            name="ck_agent_transcript_representations_count_nonnegative",
        ),
        CheckConstraint(
            "("
            "kind = 'FULL' "
            "AND transcript_version = 0 "
            "AND parent_transcript_ref IS NULL "
            "AND parent_transcript_version IS NULL "
            "AND delta_depth = 0"
            ") OR ("
            "kind = 'DELTA' "
            "AND transcript_version > 0 "
            "AND parent_transcript_ref IS NOT NULL "
            "AND parent_transcript_version IS NOT NULL "
            "AND delta_depth > 0"
            ")",
            name="ck_agent_transcript_representations_shape",
        ),
        CheckConstraint(
            "parent_transcript_ref IS NULL "
            "OR parent_transcript_ref != transcript_ref "
            "OR parent_transcript_version != transcript_version",
            name="ck_agent_transcript_representations_not_self_parent",
        ),
        ForeignKeyConstraint(
            ["parent_transcript_ref", "parent_transcript_version"],
            [
                "agent_transcript_representations.transcript_ref",
                "agent_transcript_representations.transcript_version",
            ],
            ondelete="RESTRICT",
        ),
    )

    transcript_ref: Mapped[str] = mapped_column(String(64), primary_key=True)
    transcript_version: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    parent_transcript_ref: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    parent_transcript_version: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    delta_depth: Mapped[int] = mapped_column(Integer, nullable=False)
    logical_message_count: Mapped[int] = mapped_column(Integer, nullable=False)
    logical_transcript_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    payload_root_ref: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(
            "agent_transcript_payload_nodes.payload_root_ref",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
