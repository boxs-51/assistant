from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.context.manager import ContextEngine
from src.domain.schemas.event import BaseEvent
from src.domain.schemas.identity import Identity
from src.infrastructure.event_bus.registry import EventRegistry
from src.runtimes.session.runtime import SessionRuntime
from src.runtimes.context.runtime import ContextRuntime


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

    async def add_message(self, session_id, role, content):
        message = SimpleNamespace(role=role, content=content)
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
