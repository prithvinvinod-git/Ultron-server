"""Chat turn persistence (T?): a named conversation gets a stored transcript.

The conversation routes are covered by their own suite; what these tests answer
is the seam between them and ``/chat``: when a turn names a ``conversation_id``,
the user message and the assistant answer are appended to that thread, ordered
by ``sequence``, and the terminal payload echoes the id back. A turn that names
no conversation persists nothing — the stateless path is unchanged.

A real :class:`ConversationRepository` runs over an in-memory SQLite database,
so ordering and the append numbering are the production ones. Only the model is
faked (a scripted answer), because the provider is not this suite's subject.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.api.dependencies import require_principal
from app.config import DatabaseSettings, ModelRouterSettings, get_settings, reload_settings
from app.database.repositories.conversations import (
    ConversationRepository,
    MessageRepository,
)
from app.database.session import (
    Base,
    create_engine,
    create_session_factory,
    dispose,
    session_scope,
)
from app.events.bus import EventBus
from app.main import create_app
from app.models.base import FinishReason, ModelStreamChunk
from app.models.fake import FakeProvider
from app.models.router import ModelRouter
from app.security.authentication import Principal
from app.tools.registry import ToolRegistry

pytestmark = pytest.mark.unit

SQLITE_URL = "sqlite+aiosqlite://"


def _text_turn(*deltas: str) -> list[ModelStreamChunk]:
    return [
        *(ModelStreamChunk(delta=delta) for delta in deltas),
        ModelStreamChunk(finish_reason=FinishReason.STOP),
    ]


class _StubContainer:
    """The slice of the container the chat routes reach for, over a real session."""

    def __init__(self, *, router: ModelRouter, factory: Any) -> None:
        self.settings = get_settings()
        self.model_router = router
        self.tools = ToolRegistry()
        self.events = EventBus()
        self._factory = factory

    def session_scope(self) -> Any:
        return _scope(self._factory)

    def get_audit_log_repository(self, session: AsyncSession) -> Any:
        class _Sink:
            async def record(self, **fields: Any) -> None: ...

        return _Sink()

    def get_conversation_repository(self, session: AsyncSession) -> ConversationRepository:
        return ConversationRepository(session)

    def get_message_repository(self, session: AsyncSession) -> MessageRepository:
        return MessageRepository(session)

    def get_memory_manager(self, session: AsyncSession) -> Any:
        class _Memory:
            async def recall(self, *args: Any, **kwargs: Any) -> list[Any]:
                return []

            async def build_context(self, *args: Any, **kwargs: Any) -> list[str]:
                return []

            async def remember(self, *args: Any, **kwargs: Any) -> Any:
                raise AssertionError("memory.store is not used in this suite")

        return _Memory()


@asynccontextmanager
async def _scope(factory: Any) -> AsyncIterator[AsyncSession]:
    async with session_scope(factory) as session:
        yield session


_USER_ID = uuid.uuid4()


def _principal() -> Principal:
    return Principal(
        actor_type="user",
        user_id=_USER_ID,
        username="tester",
        is_superuser=False,
    )


class _Api:
    def __init__(self, client: AsyncClient, factory: Any) -> None:
        self.client = client
        self.factory = factory

    async def transcript(self, conversation_id: str) -> list[Any]:
        async with session_scope(self.factory) as session:
            return await MessageRepository(session).list_for_conversation(
                uuid.UUID(conversation_id)
            )


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    created = create_engine(DatabaseSettings(url=SQLITE_URL))
    async with created.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield created
    finally:
        await dispose(created)


@pytest.fixture
async def api(monkeypatch: pytest.MonkeyPatch, engine: AsyncEngine) -> AsyncIterator[_Api]:
    monkeypatch.setenv("ALLOWED_HOSTS", "testserver")
    monkeypatch.setenv("METRICS_ENABLED", "false")
    reload_settings()

    factory = create_session_factory(engine)
    provider = FakeProvider(name="ollama", local=True, streams=[_text_turn("hi there")])
    router = ModelRouter(
        [provider], settings=ModelRouterSettings(default="ollama:llama3", fallback_chain=[])
    )
    app = create_app()
    app.state.container = _StubContainer(router=router, factory=factory)
    app.dependency_overrides[require_principal] = _principal
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield _Api(client, factory)
    app.dependency_overrides.clear()


async def _create(client: AsyncClient) -> str:
    response = await client.post("/conversations", json={"title": "thread"})
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def test_a_named_turn_is_stored_and_the_id_echoed(api: _Api) -> None:
    conversation_id = await _create(api.client)
    response = await api.client.post(
        "/chat",
        json={"message": "hello", "conversation_id": conversation_id},
        timeout=30.0,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["conversation_id"] == conversation_id

    messages = await api.transcript(conversation_id)
    assert [(str(m.role), m.content, m.sequence) for m in messages] == [
        ("user", "hello", 0),
        ("assistant", body["answer"], 1),
    ]


async def test_a_turn_without_a_conversation_persists_nothing(api: _Api) -> None:
    response = await api.client.post("/chat", json={"message": "hello"}, timeout=30.0)
    assert response.status_code == 200, response.text
    assert response.json()["conversation_id"] is None
    listing = await api.client.get("/conversations")
    assert listing.json() == []


async def test_a_foreign_conversation_is_404(api: _Api, engine: AsyncEngine) -> None:
    """A turn cannot write into a thread the caller does not own."""
    factory = create_session_factory(engine)
    async with session_scope(factory) as session:
        owned = await ConversationRepository(session).add(
            _conversation_owned_by(uuid.uuid4())
        )
        await session.flush()
        foreign_id = owned.id

    response = await api.client.post(
        "/chat",
        json={"message": "hello", "conversation_id": str(foreign_id)},
        timeout=30.0,
    )
    assert response.status_code == 404


def _conversation_owned_by(user_id: uuid.UUID) -> Any:
    from app.database.models import Conversation, ConversationStatus

    return Conversation(status=ConversationStatus.ACTIVE, user_id=user_id)
