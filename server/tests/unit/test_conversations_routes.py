"""Conversation route tests (T?): the transcript surface behind the chat door.

The repositories' ordering and tenancy rules are covered by their own suite;
what these tests answer is the part only the assembled routes can: does a
created conversation list for its owner, does a foreign id read as 404 rather
than as another user's thread, does rename persist, and does delete soft-delete
while leaving the messages readable.

Like the schedule route tests, this suite uses a **real**
:class:`ConversationRepository` over an in-memory SQLite database rather than a
fake. The routes lean on the repository for tenancy and ordering, so a double
would let the suite pass while the wiring was wrong; the in-memory engine gives
the real repository a real session without PostgreSQL or Redis.
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
from app.config import DatabaseSettings, reload_settings
from app.database.models import MessageRole
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
from app.main import create_app
from app.security.authentication import Principal

pytestmark = pytest.mark.unit

SQLITE_URL = "sqlite+aiosqlite://"


class _StubContainer:
    """The slice of the container the conversation routes reach for.

    ``session_scope`` is the real unit of work over the test engine, so the
    routes read and write through the production repositories exactly as
    deployed.
    """

    def __init__(self, factory: Any) -> None:
        self._factory = factory

    def session_scope(self) -> Any:
        return _scope(self._factory)

    def get_conversation_repository(self, session: AsyncSession) -> ConversationRepository:
        return ConversationRepository(session)

    def get_message_repository(self, session: AsyncSession) -> MessageRepository:
        return MessageRepository(session)


def _principal(user_id: uuid.UUID) -> Any:
    def override() -> Principal:
        return Principal(
            actor_type="user",
            user_id=user_id,
            username="tester",
            is_superuser=False,
        )

    return override


@asynccontextmanager
async def _scope(factory: Any) -> AsyncIterator[AsyncSession]:
    async with session_scope(factory) as session:
        yield session


class _Api:
    """The assembled app plus the engine behind it."""

    def __init__(self, client: AsyncClient, factory: Any) -> None:
        self.client = client
        self.factory = factory


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    """A private in-memory database with the production schema."""
    created = create_engine(DatabaseSettings(url=SQLITE_URL))
    async with created.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield created
    finally:
        await dispose(created)


def _build_api(app: Any, factory: Any, user_id: uuid.UUID) -> None:
    app.state.container = _StubContainer(factory)
    app.dependency_overrides[require_principal] = _principal(user_id)


@pytest.fixture
async def api(
    monkeypatch: pytest.MonkeyPatch, engine: AsyncEngine
) -> AsyncIterator[_Api]:
    monkeypatch.setenv("ALLOWED_HOSTS", "testserver")
    monkeypatch.setenv("METRICS_ENABLED", "false")
    reload_settings()

    factory = create_session_factory(engine)
    app = create_app()
    _build_api(app, factory, uuid.uuid4())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield _Api(client, factory)
    app.dependency_overrides.clear()


async def _create(client: AsyncClient, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"title": "first thread"}
    body.update(overrides)
    response = await client.post("/conversations", json=body)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


async def _append(factory: Any, conversation_id: str, content: str) -> None:
    """Write a message straight through the repository, bypassing the routes."""
    async with session_scope(factory) as session:
        repository = MessageRepository(session)
        await repository.append(
            conversation_id=uuid.UUID(conversation_id),
            role=MessageRole.USER,
            content=content,
        )


# --------------------------------------------------------------------------- #
# GET /conversations
# --------------------------------------------------------------------------- #


async def test_list_is_empty_by_default(api: _Api) -> None:
    response = await api.client.get("/conversations")
    assert response.status_code == 200
    assert response.json() == []


async def test_a_created_conversation_is_listed(api: _Api) -> None:
    created = await _create(api.client)
    response = await api.client.get("/conversations")
    assert response.status_code == 200
    assert [row["id"] for row in response.json()] == [created["id"]]


async def test_message_count_reflects_a_transcript(api: _Api) -> None:
    created = await _create(api.client)
    await _append(api.factory, created["id"], "hello")
    await _append(api.factory, created["id"], "world")
    response = await api.client.get("/conversations")
    assert response.json()[0]["message_count"] == 2


# --------------------------------------------------------------------------- #
# POST /conversations
# --------------------------------------------------------------------------- #


async def test_create_defaults_title_the_status_and_the_project(api: _Api) -> None:
    created = await _create(api.client, title=None)
    assert created["title"] is None
    assert created["status"] == "active"
    assert created["project_id"] is None
    assert created["message_count"] == 0


async def test_create_rejects_an_unknown_field(api: _Api) -> None:
    response = await api.client.post(
        "/conversations", json={"title": "x", "status": "active"}
    )
    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# GET /conversations/{id}
# --------------------------------------------------------------------------- #


async def test_get_one_includes_its_transcript(api: _Api) -> None:
    created = await _create(api.client)
    await _append(api.factory, created["id"], "hello")
    await _append(api.factory, created["id"], "world")
    response = await api.client.get(f"/conversations/{created['id']}")
    assert response.status_code == 200
    detail = response.json()
    assert [message["content"] for message in detail["messages"]] == ["hello", "world"]
    assert [message["role"] for message in detail["messages"]] == ["user", "user"]


async def test_a_foreign_conversation_is_404(api: _Api, engine: AsyncEngine) -> None:
    """A conversation owned by someone else reads as not-found, not forbidden."""
    owner = uuid.uuid4()
    other = uuid.uuid4()
    factory = create_session_factory(engine)
    app = create_app()
    _build_api(app, factory, owner)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        created = await _create(client)

    app2 = create_app()
    _build_api(app2, factory, other)
    transport2 = ASGITransport(app=app2)
    async with AsyncClient(transport=transport2, base_url="http://testserver") as client:
        response = await client.get(f"/conversations/{created['id']}")
    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# PATCH /conversations/{id}
# --------------------------------------------------------------------------- #


async def test_rename_persists(api: _Api) -> None:
    created = await _create(api.client)
    response = await api.client.patch(
        f"/conversations/{created['id']}", json={"title": "renamed"}
    )
    assert response.status_code == 200
    assert response.json()["title"] == "renamed"
    read = await api.client.get(f"/conversations/{created['id']}")
    assert read.json()["title"] == "renamed"


async def test_rename_rejects_an_empty_title(api: _Api) -> None:
    created = await _create(api.client)
    response = await api.client.patch(
        f"/conversations/{created['id']}", json={"title": ""}
    )
    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# DELETE /conversations/{id}
# --------------------------------------------------------------------------- #


async def test_delete_hides_it_from_the_listing(api: _Api) -> None:
    created = await _create(api.client)
    response = await api.client.delete(f"/conversations/{created['id']}")
    assert response.status_code == 204
    listing = await api.client.get("/conversations")
    assert listing.json() == []


async def test_delete_leaves_the_messages_readable(api: _Api) -> None:
    """Delete is a soft delete: the transcript is the audit trail."""
    created = await _create(api.client)
    await _append(api.factory, created["id"], "remember me")
    await api.client.delete(f"/conversations/{created['id']}")
    response = await api.client.get(f"/conversations/{created['id']}")
    assert response.status_code == 200
    assert response.json()["status"] == "deleted"
    assert [message["content"] for message in response.json()["messages"]] == [
        "remember me"
    ]
