from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import or_, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError, SQLAlchemyError

from ....runtimes.capability.contracts.definition import (
    CapabilityDefinition,
    CapabilityExecutionMode,
    CapabilityKind,
)
from ..models.sql.capability.publication import (
    CapabilityPublicationRecord,
    SKILL_PUBLICATION_HEAD,
    SKILL_PUBLICATION_SCHEMA,
    SKILL_PUBLICATION_VERSION_TABLE,
)


class PublicationAuthorityError(RuntimeError):
    pass


class PublicationAuthorityUnavailable(PublicationAuthorityError):
    pass


class PublicationConflict(PublicationAuthorityError):
    pass


class PublicationPermissionDenied(PublicationAuthorityError):
    pass


class PublicationStaleRevision(PublicationAuthorityError):
    pass


PURPOSE_NAMESPACE = "NAMESPACE_RESERVATION"
PURPOSE_DIRECT = "DIRECT_CONTEXT"
STATE_ACTIVE = "ACTIVE"
STATE_REVOKED = "REVOKED"
VISIBILITY_NONE = "NONE"
VISIBILITY_OWNER = "OWNER_ONLY"
VISIBILITY_SERVER_PUBLIC = "SERVER_PUBLIC"
ORIGIN_SYSTEM = "SYSTEM_BOOTSTRAP"
ORIGIN_CALLER = "CALLER_HTTP"
PUBLISHER_SYSTEM = "SYSTEM"
PUBLISHER_USER = "USER"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stable_json(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value: dict) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _origin_fingerprint(
    *, capability_id: str, kind: str, origin_class: str, publisher_type: str, publisher_id: str
) -> str:
    material = "|".join(
        ("skill-publication-origin-v1", origin_class, publisher_type, publisher_id, kind, capability_id)
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _canonical_definition(definition: CapabilityDefinition) -> dict:
    return definition.model_dump(mode="json", by_alias=True)


def _definition_from_record(row: CapabilityPublicationRecord) -> CapabilityDefinition:
    data = dict(row.canonical_definition or {})
    if not data:
        raise PublicationAuthorityUnavailable(
            f"Publication '{row.capability_id}' has no canonical definition."
        )
    return CapabilityDefinition.model_validate(data)


class CapabilityPublicationRepository:
    """PostgreSQL-backed authority for Skill publication and namespace fencing."""

    def __init__(self, session_factory):
        self._session_factory = session_factory

    @staticmethod
    def _wrap_unavailable(exc: BaseException) -> PublicationAuthorityUnavailable:
        return PublicationAuthorityUnavailable(
            f"Skill publication authority unavailable ({type(exc).__name__})."
        )

    async def ensure_ready(self) -> None:
        try:
            async with self._session_factory() as session:
                version = (
                    await session.execute(
                        text(
                            f"SELECT version_num FROM {SKILL_PUBLICATION_SCHEMA}."
                            f"{SKILL_PUBLICATION_VERSION_TABLE}"
                        )
                    )
                ).scalar_one()
                if version != SKILL_PUBLICATION_HEAD:
                    raise PublicationAuthorityUnavailable(
                        "Skill publication schema head mismatch."
                    )
                await session.execute(
                    select(CapabilityPublicationRecord.capability_id).limit(1)
                )
        except PublicationAuthorityUnavailable:
            raise
        except (SQLAlchemyError, DBAPIError) as exc:
            raise self._wrap_unavailable(exc) from exc

    async def _locked(self, session, capability_id: str):
        return (
            await session.execute(
                select(CapabilityPublicationRecord)
                .where(CapabilityPublicationRecord.capability_id == capability_id)
                .with_for_update()
            )
        ).scalar_one_or_none()

    async def _read(self, capability_id: str):
        async with self._session_factory() as session:
            return (
                await session.execute(
                    select(CapabilityPublicationRecord).where(
                        CapabilityPublicationRecord.capability_id == capability_id
                    )
                )
            ).scalar_one_or_none()

    async def _insert_reservation(
        self,
        *,
        capability_id: str,
        kind: CapabilityKind,
        origin_class: str,
        publisher_type: str,
        publisher_id: str,
    ) -> CapabilityPublicationRecord:
        fingerprint = _origin_fingerprint(
            capability_id=capability_id,
            kind=kind.value,
            origin_class=origin_class,
            publisher_type=publisher_type,
            publisher_id=publisher_id,
        )
        now = _now()
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    current = await self._locked(session, capability_id)
                    if current is None:
                        current = CapabilityPublicationRecord(
                            capability_id=capability_id,
                            capability_kind=kind.value,
                            origin_class=origin_class,
                            origin_fingerprint=fingerprint,
                            purpose=PURPOSE_NAMESPACE,
                            publisher_type=publisher_type,
                            publisher_id=publisher_id,
                            visibility=VISIBILITY_NONE,
                            recipient_user_id=None,
                            revision=1,
                            state=STATE_ACTIVE,
                            canonical_definition=None,
                            instruction=None,
                            payload_digest=None,
                            created_at=now,
                            updated_at=now,
                        )
                        session.add(current)
                        await session.flush()
                        return current
                    if (
                        current.capability_kind == kind.value
                        and current.origin_class == origin_class
                        and current.origin_fingerprint == fingerprint
                        and current.publisher_type == publisher_type
                        and current.publisher_id == publisher_id
                    ):
                        return current
                    raise PublicationConflict(
                        f"Capability id '{capability_id}' is already reserved."
                    )
        except PublicationConflict:
            raise
        except IntegrityError as exc:
            current = await self._read(capability_id)
            if current is not None and (
                current.capability_kind == kind.value
                and current.origin_class == origin_class
                and current.origin_fingerprint == fingerprint
                and current.publisher_type == publisher_type
                and current.publisher_id == publisher_id
            ):
                return current
            raise PublicationConflict(
                f"Capability id '{capability_id}' was concurrently reserved."
            ) from exc
        except (SQLAlchemyError, DBAPIError) as exc:
            raise self._wrap_unavailable(exc) from exc

    async def reserve_system_namespace(
        self, definition: CapabilityDefinition, *, publisher_id: str = "assistant-bootstrap"
    ) -> CapabilityPublicationRecord:
        return await self._insert_reservation(
            capability_id=definition.capability_id,
            kind=definition.kind,
            origin_class=ORIGIN_SYSTEM,
            publisher_type=PUBLISHER_SYSTEM,
            publisher_id=publisher_id,
        )

    async def reserve_user_namespace(
        self, definition: CapabilityDefinition, *, publisher_id: str
    ) -> CapabilityPublicationRecord:
        if not publisher_id:
            raise PublicationPermissionDenied("Authenticated publisher is required.")
        return await self._insert_reservation(
            capability_id=definition.capability_id,
            kind=definition.kind,
            origin_class=ORIGIN_CALLER,
            publisher_type=PUBLISHER_USER,
            publisher_id=publisher_id,
        )

    async def publish_user_context(
        self, definition: CapabilityDefinition, *, publisher_id: str
    ) -> CapabilityPublicationRecord:
        if not publisher_id:
            raise PublicationPermissionDenied("Authenticated publisher is required.")
        if definition.kind is not CapabilityKind.SKILL:
            raise PublicationConflict("Only Skill definitions may be published as DIRECT context.")
        if definition.execution_mode is not CapabilityExecutionMode.CONTEXT_ONLY:
            raise PublicationConflict("Executable caller Skills are outside P1A.")
        instruction = str(definition.metadata.get("instruction", "")).strip()
        if not instruction:
            raise PublicationConflict("Skill instruction must not be empty.")
        canonical = _canonical_definition(definition)
        digest = _digest(canonical)
        fingerprint = _origin_fingerprint(
            capability_id=definition.capability_id,
            kind=definition.kind.value,
            origin_class=ORIGIN_CALLER,
            publisher_type=PUBLISHER_USER,
            publisher_id=publisher_id,
        )
        now = _now()
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    current = await self._locked(session, definition.capability_id)
                    if current is None:
                        current = CapabilityPublicationRecord(
                            capability_id=definition.capability_id,
                            capability_kind=definition.kind.value,
                            origin_class=ORIGIN_CALLER,
                            origin_fingerprint=fingerprint,
                            purpose=PURPOSE_DIRECT,
                            publisher_type=PUBLISHER_USER,
                            publisher_id=publisher_id,
                            visibility=VISIBILITY_OWNER,
                            recipient_user_id=publisher_id,
                            revision=1,
                            state=STATE_ACTIVE,
                            canonical_definition=canonical,
                            instruction=instruction,
                            payload_digest=digest,
                            created_at=now,
                            updated_at=now,
                        )
                        session.add(current)
                        await session.flush()
                        return current
                    if not (
                        current.capability_kind == definition.kind.value
                        and current.origin_class == ORIGIN_CALLER
                        and current.origin_fingerprint == fingerprint
                        and current.publisher_type == PUBLISHER_USER
                        and current.publisher_id == publisher_id
                    ):
                        raise PublicationConflict(
                            f"Capability id '{definition.capability_id}' belongs to another origin."
                        )
                    was_revoked = current.state == STATE_REVOKED
                    if current.purpose == PURPOSE_NAMESPACE:
                        current.purpose = PURPOSE_DIRECT
                    elif current.purpose != PURPOSE_DIRECT:
                        raise PublicationConflict("Incompatible publication purpose.")
                    if current.payload_digest == digest and not was_revoked:
                        return current
                    current.revision += 1
                    current.state = STATE_ACTIVE
                    current.visibility = VISIBILITY_OWNER
                    current.recipient_user_id = publisher_id
                    current.canonical_definition = canonical
                    current.instruction = instruction
                    current.payload_digest = digest
                    current.updated_at = now
                    await session.flush()
                    return current
        except PublicationConflict:
            raise
        except IntegrityError as exc:
            current = await self._read(definition.capability_id)
            if current is not None and (
                current.capability_kind == definition.kind.value
                and current.origin_class == ORIGIN_CALLER
                and current.origin_fingerprint == fingerprint
                and current.publisher_type == PUBLISHER_USER
                and current.publisher_id == publisher_id
                and current.purpose == PURPOSE_DIRECT
                and current.state == STATE_ACTIVE
                and current.payload_digest == digest
            ):
                return current
            raise PublicationConflict(
                f"Capability id '{definition.capability_id}' was concurrently published."
            ) from exc
        except (SQLAlchemyError, DBAPIError) as exc:
            raise self._wrap_unavailable(exc) from exc

    async def publish_system_direct_context(
        self,
        definition: CapabilityDefinition,
        *,
        publisher_id: str = "assistant-bootstrap",
    ) -> CapabilityPublicationRecord:
        if definition.kind is not CapabilityKind.SKILL:
            raise PublicationConflict("Only a trusted Skill may become server-public context.")
        instruction = str(definition.metadata.get("instruction", "")).strip()
        if not instruction:
            raise PublicationConflict("Trusted server-public Skill must be materialized.")
        canonical = _canonical_definition(definition)
        digest = _digest(canonical)
        fingerprint = _origin_fingerprint(
            capability_id=definition.capability_id,
            kind=definition.kind.value,
            origin_class=ORIGIN_SYSTEM,
            publisher_type=PUBLISHER_SYSTEM,
            publisher_id=publisher_id,
        )
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    current = await self._locked(session, definition.capability_id)
                    if current is None:
                        raise PublicationConflict(
                            "Trusted Skill must hold a SYSTEM namespace reservation before publication."
                        )
                    if not (
                        current.capability_kind == definition.kind.value
                        and current.origin_class == ORIGIN_SYSTEM
                        and current.origin_fingerprint == fingerprint
                        and current.publisher_type == PUBLISHER_SYSTEM
                        and current.publisher_id == publisher_id
                    ):
                        raise PublicationConflict("SYSTEM namespace origin mismatch.")
                    if (
                        current.purpose == PURPOSE_DIRECT
                        and current.state == STATE_ACTIVE
                        and current.payload_digest == digest
                    ):
                        return current
                    current.purpose = PURPOSE_DIRECT
                    current.visibility = VISIBILITY_SERVER_PUBLIC
                    current.recipient_user_id = None
                    current.state = STATE_ACTIVE
                    current.revision += 1
                    current.canonical_definition = canonical
                    current.instruction = instruction
                    current.payload_digest = digest
                    current.updated_at = _now()
                    await session.flush()
                    return current
        except PublicationConflict:
            raise
        except (SQLAlchemyError, DBAPIError) as exc:
            raise self._wrap_unavailable(exc) from exc

    async def revoke_user_context(
        self, capability_id: str, *, publisher_id: str
    ) -> CapabilityPublicationRecord:
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    current = await self._locked(session, capability_id)
                    if current is None or current.purpose != PURPOSE_DIRECT:
                        raise PublicationPermissionDenied("Unknown caller Skill publication.")
                    if (
                        current.publisher_type != PUBLISHER_USER
                        or current.publisher_id != publisher_id
                    ):
                        raise PublicationPermissionDenied("Caller does not own this Skill publication.")
                    if current.state != STATE_REVOKED:
                        current.state = STATE_REVOKED
                        current.revision += 1
                        current.updated_at = _now()
                        await session.flush()
                    return current
        except PublicationPermissionDenied:
            raise
        except (SQLAlchemyError, DBAPIError) as exc:
            raise self._wrap_unavailable(exc) from exc

    async def list_visible_definitions(self, identity) -> list[CapabilityDefinition]:
        user_id = str(getattr(identity, "user_id", "") or "")
        predicate = CapabilityPublicationRecord.visibility == VISIBILITY_SERVER_PUBLIC
        if user_id:
            predicate = or_(
                predicate,
                (
                    (CapabilityPublicationRecord.visibility == VISIBILITY_OWNER)
                    & (CapabilityPublicationRecord.recipient_user_id == user_id)
                ),
            )
        try:
            async with self._session_factory() as session:
                rows = (
                    await session.execute(
                        select(CapabilityPublicationRecord)
                        .where(
                            CapabilityPublicationRecord.purpose == PURPOSE_DIRECT,
                            CapabilityPublicationRecord.state == STATE_ACTIVE,
                            predicate,
                        )
                        .order_by(CapabilityPublicationRecord.capability_id)
                    )
                ).scalars().all()
                return [_definition_from_record(row) for row in rows]
        except PublicationAuthorityError:
            raise
        except (SQLAlchemyError, DBAPIError, ValueError) as exc:
            raise self._wrap_unavailable(exc) from exc

    async def get_visible_definition(
        self, capability_id: str, identity
    ) -> CapabilityDefinition | None:
        definitions: Iterable[CapabilityDefinition] = await self.list_visible_definitions(identity)
        for definition in definitions:
            if definition.capability_id == capability_id:
                return definition
        return None


__all__ = [
    "CapabilityPublicationRepository",
    "PublicationAuthorityError",
    "PublicationAuthorityUnavailable",
    "PublicationConflict",
    "PublicationPermissionDenied",
    "PublicationStaleRevision",
    "PURPOSE_NAMESPACE",
    "PURPOSE_DIRECT",
    "STATE_ACTIVE",
    "STATE_REVOKED",
]
