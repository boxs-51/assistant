"""Security #408: explicit trusted Skill post-materialization publication.

The helper is invoked by server bootstrap only, never the Agent skill.load
driver. These portable tests verify provenance, operator intent, and CAS
wiring; the existing PostgreSQL G3 test covers real SQL ACL/revision behavior.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from se.src.main import _publish_explicit_trusted_bootstrap_skills
from se.src.runtimes.capability.contracts.definition import (
    CapabilityDefinition, CapabilityExecutionMode, CapabilityKind,
)


def _skill(name: str, *, instruction: str = "trusted-only instruction"):
    return CapabilityDefinition(
        id=name,
        name=name,
        description="Trusted test Skill",
        kind=CapabilityKind.SKILL,
        execution_kind="SKILL",
        execution_mode=CapabilityExecutionMode.CONTEXT_ONLY,
        require_auth=True,
        required_scopes=["allowed-scope"],
        metadata={
            "kind": "SKILL",
            "provenance": "SERVER_SKILL_MANIFEST",
            "server_managed": True,
            "runtime_owned": True,
            "lazy": True,
            "loaded": True,
            "instruction": instruction,
            "required_permissions": ["allowed-permission"],
        },
    )


class _Authority:
    def __init__(self):
        self.events = []

    async def reserve_system_namespace(self, definition):
        self.events.append(("reserve", definition.capability_id))
        return SimpleNamespace(revision=9)


class _Runtime:
    def __init__(self, authority=None):
        self.publication_authority = authority
        self.events = []

    async def publish_system_direct_context(self, definition, *, expected_revision):
        self.events.append((definition, expected_revision))


class _Loader:
    def __init__(self, entries):
        self._skill_manifests = {key: object() for key in entries}
        self.entries = entries
        self.calls = []

    def load_skill(self, skill_id):
        self.calls.append(skill_id)
        return self.entries[skill_id]


def _container(entries, *, authority=None):
    loader = _Loader(entries)
    runtime = _Runtime(authority)
    return SimpleNamespace(capability_runtime=runtime, support_loader=loader)


@pytest.mark.asyncio
async def test_no_operator_opt_in_never_materializes_or_publishes(monkeypatch):
    monkeypatch.delenv("ASSISTANT_SKILL_PUBLICATION_TRUSTED_IDS", raising=False)
    container = _container({"s": _skill("s")}, authority=_Authority())
    assert await _publish_explicit_trusted_bootstrap_skills(container, ["s"]) == ()
    assert not container.support_loader.calls
    assert not container.capability_runtime.events


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["*", "s,*", "s,s", ",s", "s,", " , "])
async def test_operator_wildcards_empty_and_duplicates_fail_before_effects(monkeypatch, raw):
    monkeypatch.setenv("ASSISTANT_SKILL_PUBLICATION_TRUSTED_IDS", raw)
    authority = _Authority()
    container = _container({"s": _skill("s")}, authority=authority)
    with pytest.raises(RuntimeError):
        await _publish_explicit_trusted_bootstrap_skills(container, ["s"])
    assert authority.events == []
    assert not container.support_loader.calls
    assert container.capability_runtime.events == []


@pytest.mark.asyncio
async def test_configured_opt_in_requires_durable_sql_and_known_manifest(monkeypatch):
    monkeypatch.setenv("ASSISTANT_SKILL_PUBLICATION_TRUSTED_IDS", "s")
    container = _container({"s": _skill("s")}, authority=None)
    with pytest.raises(RuntimeError, match="PostgreSQL"):
        await _publish_explicit_trusted_bootstrap_skills(container, ["s"])
    assert not container.support_loader.calls

    authority = _Authority()
    container = _container({"s": _skill("s")}, authority=authority)
    monkeypatch.setenv("ASSISTANT_SKILL_PUBLICATION_TRUSTED_IDS", "s,unknown")
    with pytest.raises(RuntimeError, match="not a server manifest"):
        await _publish_explicit_trusted_bootstrap_skills(container, ["s"])
    assert authority.events == []
    assert not container.capability_runtime.events


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("provenance", "CALLER_HTTP"),
        ("server_managed", False),
        ("runtime_owned", False),
        ("loaded", False),
        ("instruction", ""),
    ],
)
async def test_untrusted_or_lazy_skill_never_published(monkeypatch, field, value):
    monkeypatch.setenv("ASSISTANT_SKILL_PUBLICATION_TRUSTED_IDS", "s")
    definition = _skill("s")
    definition.metadata[field] = value
    authority = _Authority()
    container = _container({"s": definition}, authority=authority)
    with pytest.raises(RuntimeError, match="verified materialization"):
        await _publish_explicit_trusted_bootstrap_skills(container, ["s"])
    assert authority.events == []
    assert not container.capability_runtime.events


@pytest.mark.asyncio
async def test_operator_trusted_selection_preserves_acl_and_actual_sql_revision(monkeypatch):
    monkeypatch.setenv("ASSISTANT_SKILL_PUBLICATION_TRUSTED_IDS", "a, b")
    a, b = _skill("a"), _skill("b")
    authority = _Authority()
    container = _container({"a": a, "b": b}, authority=authority)
    assert await _publish_explicit_trusted_bootstrap_skills(
        container, ["a", "b"]
    ) == ("a", "b")
    assert container.support_loader.calls == ["a", "b"]
    assert authority.events == [("reserve", "a"), ("reserve", "b")]
    assert [(d.capability_id, revision) for d, revision in container.capability_runtime.events] == [
        ("a", 9), ("b", 9)
    ]
    for definition, revision in container.capability_runtime.events:
        assert definition.require_auth is True
        assert definition.required_scopes == ["allowed-scope"]
        assert definition.metadata["required_permissions"] == ["allowed-permission"]
        assert definition.metadata["instruction"] == "trusted-only instruction"
        assert revision == 9


@pytest.mark.asyncio
async def test_real_postgres_operator_trusted_publication_and_race_fence(monkeypatch):
    """Native PostgreSQL: operator-only positive, durable ACL, bad-origin reject."""
    import asyncio
    import os
    import uuid

    from alembic import command
    from alembic.config import Config
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from se.src.infrastructure.storage.repositories.capability_publications import (
        CapabilityPublicationRepository, PublicationConflict,
    )

    if os.getenv("ASSISTANT_SECURITY_PG_E2E") != "1":
        pytest.skip("native PostgreSQL attested publisher test is opt-in")
    dsn = str(os.environ.get("ASSISTANT_SKILL_PUBLICATION_DATABASE_URL") or "")
    assert dsn.startswith("postgresql+asyncpg://")
    # Alembic's env.py drives its own event loop.
    await asyncio.to_thread(
        command.upgrade, Config("alembic_skill_publications.ini"), "head"
    )
    engine = create_async_engine(dsn, pool_pre_ping=True)
    repo = CapabilityPublicationRepository(
        async_sessionmaker(engine, expire_on_commit=False)
    )

    class _SqlRuntime(_Runtime):
        async def publish_system_direct_context(
            self, definition, *, expected_revision
        ):
            await self.publication_authority.publish_system_direct_context(
                definition, expected_revision=expected_revision
            )

    name = "sec408-trusted-" + uuid.uuid4().hex
    collision = "sec408-collision-" + uuid.uuid4().hex
    try:
        await repo.ensure_ready()
        definition = _skill(name)
        container = _container({name: definition}, authority=repo)
        container.capability_runtime = _SqlRuntime(repo)
        monkeypatch.setenv("ASSISTANT_SKILL_PUBLICATION_TRUSTED_IDS", name)
        assert await _publish_explicit_trusted_bootstrap_skills(
            container, [name]
        ) == (name,)
        visible = await repo.get_visible_definition(
            name, SimpleNamespace(user_id="authorized")
        )
        assert visible is not None
        assert visible.capability_id == name
        assert visible.require_auth is True
        assert visible.required_scopes == ["allowed-scope"]
        assert visible.metadata["required_permissions"] == [
            "allowed-permission"
        ]
        assert visible.metadata["instruction"] == "trusted-only instruction"

        # Startup idempotency is granted only for the same immutable digest
        # and owner after PostgreSQL CAS validation.
        assert await _publish_explicit_trusted_bootstrap_skills(
            container, [name]
        ) == (name,)

        # A caller-owned row must NEVER be promoted into SYSTEM PUBLIC.
        await repo.reserve_user_namespace(
            _skill(collision), publisher_id="untrusted-principal"
        )
        bad_container = _container(
            {collision: _skill(collision)}, authority=repo
        )
        bad_container.capability_runtime = _SqlRuntime(repo)
        monkeypatch.setenv(
            "ASSISTANT_SKILL_PUBLICATION_TRUSTED_IDS", collision
        )
        with pytest.raises(PublicationConflict):
            await _publish_explicit_trusted_bootstrap_skills(
                bad_container, [collision]
            )
        assert await repo.get_visible_definition(
            collision, SimpleNamespace(user_id="untrusted-principal")
        ) is None
    finally:
        await engine.dispose()
