from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.context.manager import ContextEngine
from se.src.domain.schemas.event import BaseEvent
from se.src.domain.schemas.identity import Identity
from se.src.infrastructure.event_bus.registry import EventRegistry
from se.src.runtimes.session.runtime import SessionRuntime
from se.src.runtimes.context.runtime import ContextRuntime
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.models.sql.chat_data.session import Session as DbSession
from se.src.infrastructure.storage.repositories.chat_data.sessions import SessionRepository
import se.src.infrastructure.storage.core.unit_of_work  # noqa: F401 - load all mapped tables


class FakeUow:
    def __init__(self, sessions, projects=None):
        self.sessions = sessions
        self.projects = projects
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def commit(self):
        self.committed = True


class FakeSessionRepository:
    def __init__(self, session=None, messages=None):
        self.session = session
        self.messages = messages or []
        self.created_args = None
        self.added_messages = []

    async def get_by_id(self, session_id, options=None):
        return self.session if self.session and self.session.id == session_id else None

    async def create_session(self, **kwargs):
        self.created_args = kwargs
        self.session = SimpleNamespace(
            id=kwargs["session_id"],
            user_id=kwargs["user_id"],
            organization_id=kwargs["organization_id"],
            status="active",
            metadata_json={},
            messages=[],
            attachments=[],
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        return self.session

    async def add_message(self, session_id, role, content, **temporal):
        message = SimpleNamespace(
            role=role,
            content=content,
            sequence=len(self.messages) + 1,
            created_at=temporal.get("created_at") or datetime.now(timezone.utc),
            completed_at=temporal.get("completed_at"),
            turn_id=temporal.get("turn_id"),
        )
        self.messages.append(message)
        self.session.messages = self.messages
        self.added_messages.append(message)
        return message

    async def get_messages_by_session_id(self, session_id, limit=100):
        return self.messages[:limit]


class FakeProjectRepository:
    async def get_by_id(self, project_id, with_relations=False):
        return None


class FakeBus:
    def __init__(self):
        self.handlers = {}
        self.published = []

    def subscribe(self, name, handler):
        self.handlers[name] = handler

    async def publish(self, event):
        self.published.append(event)


def identity():
    return Identity(user_id="user-1", organization_id="org-1", auth_type="api_key")


@pytest.mark.asyncio
async def test_context_engine_builds_snapshot_from_sql_model_shape():
    db_session = SimpleNamespace(
        id="session-1",
        user_id="user-1",
        organization_id="org-1",
        status="active",
        metadata_json={"summary": "hello"},
        messages=[SimpleNamespace(role="user", content={"type": "text", "data": "Hello"})],
        attachments=[],
        project_id=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    repository = FakeSessionRepository(db_session, db_session.messages)
    uow = FakeUow(repository, FakeProjectRepository())
    engine = ContextEngine(object(), lambda: uow)

    snapshot = await engine.load_context("session-1", identity())

    assert snapshot.session.session_id == "session-1"
    assert snapshot.session.metadata["summary"] == "hello"
    assert snapshot.session.messages[0].content == "Hello"


@pytest.mark.asyncio
async def test_session_runtime_creates_session_and_persists_latest_message():
    repository = FakeSessionRepository()
    uow = FakeUow(repository)
    bus = FakeBus()
    runtime = SessionRuntime()
    runtime.event_bus = bus
    runtime.uow_factory = lambda: uow

    await runtime._on_request_received(BaseEvent(
        event_name="transport.event.request_received",
        session_id="session-1",
        turn_id="turn-1",
        payload={
            "identity": identity().model_dump(),
            "request_body": {"messages": [{"role": "user", "content": "Hello"}]},
        },
    ))

    assert repository.created_args["session_id"] == "session-1"
    assert repository.added_messages[0].content["data"] == "Hello"
    assert bus.published[0].event_name == "session.event.loaded"
    assert bus.published[0].payload["session"]["messages"]


@pytest.mark.asyncio
async def test_session_runtime_persists_completed_stream_answer():
    repository = FakeSessionRepository(
        SimpleNamespace(id="session-1", user_id="user-1", messages=[])
    )
    uow = FakeUow(repository)
    runtime = SessionRuntime()
    runtime.uow_factory = lambda: uow

    await runtime._on_stream_chunk(BaseEvent(
            event_name="provider.stream.chunk_emitted",
            session_id="session-1",
            turn_id="turn-1",
        payload={
            "chunk": {
                "choices": [{"delta": {"content": "Hello "}}],
            },
        },
    ))
    await runtime._on_stream_chunk(BaseEvent(
            event_name="provider.stream.chunk_emitted",
            session_id="session-1",
            turn_id="turn-1",
        payload={
            "chunk": {
                "choices": [{"delta": {"content": "world"}}],
            },
        },
    ))
    await runtime._on_stream_completed(BaseEvent(
            event_name="provider.stream.completed",
            session_id="session-1",
            turn_id="turn-1",
        payload={},
    ))

    assert repository.added_messages[-1].role == "assistant"
    assert repository.added_messages[-1].content["data"] == "Hello world"


@pytest.mark.asyncio
async def test_session_runtime_isolates_interleaved_streams_by_turn_id():
    repository = FakeSessionRepository(
        SimpleNamespace(id="session-1", user_id="user-1", messages=[])
    )
    runtime = SessionRuntime()
    runtime.uow_factory = lambda: FakeUow(repository)

    async def chunk(turn_id, content):
        await runtime._on_stream_chunk(BaseEvent(
            event_name="provider.stream.chunk_emitted",
            session_id="session-1",
            turn_id=turn_id,
            payload={"chunk": {"choices": [{"delta": {"content": content}}]}},
        ))

    await chunk("turn-a", "A1")
    await chunk("turn-b", "B1")
    await chunk("turn-a", "A2")
    await chunk("turn-b", "B2")

    for turn_id in ("turn-b", "turn-a"):
        await runtime._on_stream_completed(BaseEvent(
            event_name="provider.stream.completed",
            session_id="session-1",
            turn_id=turn_id,
            payload={},
        ))

    by_turn = {message.turn_id: message for message in repository.added_messages}
    assert by_turn["turn-a"].content["data"] == "A1A2"
    assert by_turn["turn-b"].content["data"] == "B1B2"
    assert by_turn["turn-a"].created_at <= by_turn["turn-a"].completed_at
    assert by_turn["turn-b"].created_at <= by_turn["turn-b"].completed_at


@pytest.mark.asyncio
async def test_session_repository_assigns_monotonic_sequence_and_orders_by_it():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as db:
            db.add(DbSession(id="session-1"))
            await db.commit()
            repository = SessionRepository(db)
            first = await repository.add_message(
                "session-1", "user", {"type": "text", "data": "first"}, turn_id="turn-1"
            )
            second = await repository.add_message(
                "session-1", "assistant", {"type": "text", "data": "second"}, turn_id="turn-1"
            )
            await db.commit()

            history = await repository.get_messages_by_session_id("session-1")
            assert [first.sequence, second.sequence] == [1, 2]
            assert [message.sequence for message in history] == [1, 2]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_session_runtime_rejects_session_owned_by_another_user():
    session = SimpleNamespace(id="session-1", user_id="other-user")
    repository = FakeSessionRepository(session)
    uow = FakeUow(repository)
    bus = FakeBus()
    runtime = SessionRuntime()
    runtime.event_bus = bus
    runtime.uow_factory = lambda: uow

    await runtime._on_request_received(BaseEvent(
        event_name="transport.event.request_received",
        session_id="session-1",
        payload={"identity": identity().model_dump(), "request_body": {}},
    ))

    assert bus.published == []


@pytest.mark.asyncio
async def test_context_runtime_replaces_request_history_with_persisted_snapshot():
    bus = FakeBus()
    runtime = ContextRuntime()
    runtime.event_bus = bus
    runtime.context_engine = SimpleNamespace(
        load_context=lambda session_id, request_identity: __import__("asyncio").sleep(
            0,
            result=SimpleNamespace(
                model_dump=lambda: {"session": {"session_id": session_id}},
                session=SimpleNamespace(
                    messages=[SimpleNamespace(model_dump=lambda exclude_none=True: {
                        "role": "user", "content": "persisted"
                    })]
                ),
            ),
        )
    )

    await runtime._handle_build_context(BaseEvent(
        event_name="context.command.build",
        session_id="session-1",
        payload={
            "identity": identity().model_dump(),
            "request_body": {"messages": [{"role": "user", "content": "stale"}]},
        },
    ))

    published = bus.published[0]
    assert published.event_name == "context.event.built"
    assert published.payload["request_body"]["messages"][0]["content"] == "persisted"
