"""Memory system tests (T120-T129, spec §22 / §66.12-§66.13).

These run against SQLite in-memory through the same ``Base.metadata`` schema the
repository tests use, so the namespace, expiry, ranking and vector storage rules
are exercised against the real transaction behaviour rather than a stub. Redis
is ``fakeredis``; the embedders are the real providers (hash) or a scripted
double where a cosine ranking has to be exact.

The tests are weighted towards what would be *silently* wrong: a namespace that
leaks across projects, a short-term window that reverses chronology on the
mirror path, a semantic search that returns an empty list because no provider is
configured rather than because nothing matched, or a layer ``clear`` that wipes
the neighbours.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import fakeredis
import pytest
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as DriverConnectionError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.config import EmbeddingSettings
from app.core.errors import (
    DependencyUnavailableError,
    InvalidInputError,
)
from app.database.models import MemoryLayer
from app.database.redis_client import RedisClient
from app.database.repositories.memories import MemoryRepository
from app.database.session import Base, create_engine, dispose
from app.events.bus import EventBus
from app.events.types import EventType
from app.memory import namespaces
from app.memory.embeddings import (
    EmbeddingProvider,
    HashEmbeddingProvider,
    NullEmbeddingProvider,
    create_embedding_provider,
)
from app.memory.episodic import EpisodicMemory
from app.memory.long_term import LongTermMemory
from app.memory.manager import MemoryManager
from app.memory.project import ProjectMemory
from app.memory.recorder import MemoryRecorder
from app.memory.semantic import SemanticMemory
from app.memory.short_term import ShortTermMemory
from app.memory.types import MemoryMessage, MemoryRecord

pytestmark = pytest.mark.unit

SQLITE_URL = "sqlite+aiosqlite://"


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    """A private in-memory database with the production schema."""
    from app.config import DatabaseSettings

    created = create_engine(DatabaseSettings(url=SQLITE_URL))
    async with created.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield created
    finally:
        await dispose(created)


@pytest.fixture
async def db(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """A session bound to the test database, rolled back after each test."""
    connection = await engine.connect()
    transaction = await connection.begin()
    session = AsyncSession(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        yield session
    finally:
        await session.close()
        await transaction.rollback()
        await connection.close()


@pytest.fixture
def redis() -> RedisClient:
    """A working in-memory Redis."""
    return RedisClient(fakeredis.aioredis.FakeRedis())


@pytest.fixture
def repo(db: AsyncSession) -> MemoryRepository:
    return MemoryRepository(db)


class ScriptedEmbedder(EmbeddingProvider):
    """An embedder returning hand-chosen vectors, for exact cosine rankings."""

    name = "scripted"

    def __init__(self, vectors: dict[str, list[float]], dimensions: int = 3) -> None:
        self._vectors = vectors
        self.dimensions = dimensions

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vectors.get(text, [0.0] * self.dimensions) for text in texts]


class _FailingCommand:
    """A driver call that fails whichever way it is consumed."""

    async def _raise(self) -> None:
        raise DriverConnectionError("Error 111 connecting to redis. Connection refused.")

    def __await__(self) -> Any:
        return self._raise().__await__()

    def __call__(self, *_args: Any, **_kwargs: Any) -> _FailingCommand:
        return self


class _BrokenRedis:
    """A driver double that fails every command the way an outage does."""

    def __getattr__(self, _name: str) -> _FailingCommand:
        return _FailingCommand()


# --------------------------------------------------------------------------- #
# Namespaces (T120)
# --------------------------------------------------------------------------- #


def test_user_namespace_has_no_slug() -> None:
    assert namespaces.user() == "user"
    assert namespaces.parse("user").slug == ""


def test_project_and_agent_namespaces_are_built_and_normalised() -> None:
    assert namespaces.for_project("CampusCare") == "project:campuscare"
    assert namespaces.for_agent("Research") == "agent:research"


def test_project_namespace_requires_a_slug() -> None:
    with pytest.raises(InvalidInputError):
        namespaces.build("project", "")


def test_user_namespace_refuses_a_slug() -> None:
    with pytest.raises(InvalidInputError):
        namespaces.build("user", "alice")


def test_unknown_namespace_kind_is_rejected() -> None:
    with pytest.raises(InvalidInputError):
        namespaces.build("projekt", "campuscare")


def test_namespace_slug_charset_is_enforced() -> None:
    with pytest.raises(InvalidInputError):
        namespaces.for_project("has space")
    with pytest.raises(InvalidInputError):
        namespaces.for_agent("../escape")


def test_parse_round_trips_through_build() -> None:
    parsed = namespaces.parse("project:campuscare")
    assert parsed.kind == "project"
    assert parsed.slug == "campuscare"
    assert parsed.is_project and not parsed.is_user and not parsed.is_agent
    assert parsed.raw == namespaces.build("project", "campuscare")


# --------------------------------------------------------------------------- #
# Embeddings (T127)
# --------------------------------------------------------------------------- #


def test_provider_none_builds_the_null_provider() -> None:
    provider = create_embedding_provider(EmbeddingSettings(provider="none"))
    assert isinstance(provider, NullEmbeddingProvider)
    assert provider.available is False


async def test_ollama_without_a_model_is_unavailable_not_broken() -> None:
    provider = create_embedding_provider(EmbeddingSettings(provider="ollama", model=""))
    assert isinstance(provider, NullEmbeddingProvider)
    assert provider.available is False
    with pytest.raises(DependencyUnavailableError):
        await provider.embed(["anything"])


def test_openai_without_a_key_is_unavailable() -> None:
    provider = create_embedding_provider(
        EmbeddingSettings(provider="openai"), openai_api_key=""
    )
    assert isinstance(provider, NullEmbeddingProvider)


def test_hash_fallback_is_used_only_when_allowed() -> None:
    allowed = create_embedding_provider(
        EmbeddingSettings(provider="ollama", model="", allow_hash_fallback=True)
    )
    assert isinstance(allowed, HashEmbeddingProvider)
    assert allowed.is_fallback and allowed.available


async def test_null_embedder_raises_rather_than_returning_empty() -> None:
    with pytest.raises(DependencyUnavailableError):
        await NullEmbeddingProvider().embed(["anything"])


async def test_hash_embedder_is_deterministic_and_normalised() -> None:
    embedder = HashEmbeddingProvider(dimensions=8)
    first, second = await embedder.embed(["alpha beta", "alpha beta"])
    assert first == second
    assert abs(sum(value * value for value in first) - 1.0) < 1e-6


# --------------------------------------------------------------------------- #
# Types (T120)
# --------------------------------------------------------------------------- #


def test_memory_record_expiry_is_computed_from_the_timestamp() -> None:
    base = datetime.now(UTC)
    live = _record(expires_at=base + timedelta(days=1), created_at=base)
    dead = _record(expires_at=base - timedelta(days=1), created_at=base)
    assert live.is_expired is False
    assert dead.is_expired is True


def test_memory_record_prompt_line_carries_the_layer() -> None:
    record = _record(layer=MemoryLayer.LONG_TERM)
    assert record.to_prompt_line().startswith("[long_term]")


def test_memory_message_round_trips_through_its_wire_form() -> None:
    original = MemoryMessage.now(role="user", content="hello")
    restored = MemoryMessage.from_dict(original.to_dict())
    assert restored.role == "user"
    assert restored.content == "hello"
    assert restored.created_at == original.created_at


# --------------------------------------------------------------------------- #
# Long-term (T122)
# --------------------------------------------------------------------------- #


async def test_long_term_recall_is_importance_ordered_and_records_the_read(
    repo: MemoryRepository,
    db: AsyncSession,
) -> None:
    layer = LongTermMemory(repo)
    await layer.remember("a trivial note", namespace="user", importance=0.2)
    await layer.remember("a crucial preference", namespace="user", importance=0.9)

    recalled = await layer.recall(namespace="user")
    assert [record.content for record in recalled] == ["a crucial preference", "a trivial note"]

    db.expire_all()
    rows = await repo.list_by_layer("user", MemoryLayer.LONG_TERM)
    assert all(row.access_count == 1 for row in rows)


async def test_long_term_clear_removes_only_its_own_layer(repo: MemoryRepository) -> None:
    long_term = LongTermMemory(repo)
    episodic = EpisodicMemory(repo)
    await long_term.remember("a fact", namespace="user")
    await episodic.record("an episode", namespace="user")

    removed = await long_term.clear(namespace="user")
    assert removed == 1
    assert await long_term.recall(namespace="user") == []
    assert await episodic.recent(namespace="user") != []


async def test_long_term_is_namespaced_per_project(repo: MemoryRepository) -> None:
    layer = LongTermMemory(repo)
    await layer.remember("alpha context", namespace=namespaces.for_project("alpha"))
    await layer.remember("beta context", namespace=namespaces.for_project("beta"))

    alpha = await layer.recall(namespace=namespaces.for_project("alpha"))
    assert [record.content for record in alpha] == ["alpha context"]


# --------------------------------------------------------------------------- #
# Episodic (T123)
# --------------------------------------------------------------------------- #


async def test_episodes_carry_an_outcome_and_expire_on_their_own(repo: MemoryRepository) -> None:
    layer = EpisodicMemory(repo, retention_days=1)
    episode = await layer.record(
        "ran the suite",
        outcome="passed",
        namespace="user",
        task_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
    )
    assert episode.outcome == "passed"
    assert episode.expires_at is not None

    past = datetime.now(UTC) + timedelta(days=3)
    assert await layer.recent(namespace="user", now=past) == []


async def test_expired_episodes_are_purged(repo: MemoryRepository) -> None:
    layer = EpisodicMemory(repo, retention_days=1)
    await layer.record("old work", namespace="user")
    removed = await layer.purge_expired(now=datetime.now(UTC) + timedelta(days=3))
    assert removed == 1


async def test_episode_content_that_is_not_the_envelope_degrades_to_summary(
    repo: MemoryRepository,
) -> None:
    layer = EpisodicMemory(repo)
    await layer.record("plain text", namespace="user")
    episodes = await layer.recent(namespace="user")
    assert episodes[0].summary == "plain text"
    assert episodes[0].outcome == ""


# --------------------------------------------------------------------------- #
# Semantic (T124)
# --------------------------------------------------------------------------- #


async def test_semantic_search_ranks_by_cosine_and_honours_the_floor(
    repo: MemoryRepository,
) -> None:
    embedder = ScriptedEmbedder(
        {
            "coffee order": [1.0, 0.0, 0.0],
            "tea order": [0.5, 0.5, 0.0],
            "weather report": [0.0, 1.0, 0.0],
            "coffee": [1.0, 0.0, 0.0],
        },
        dimensions=3,
    )
    semantic = SemanticMemory(repo, embedder, min_similarity=0.5, default_namespace="user")
    await semantic.index("coffee order", namespace="user")
    await semantic.index("tea order", namespace="user")
    await semantic.index("weather report", namespace="user")

    results = await semantic.search("coffee", namespace="user", top_k=3, min_similarity=0.5)
    assert [record.content for record in results] == ["coffee order", "tea order"]
    first_score = results[0].score
    second_score = results[1].score
    assert first_score is not None and second_score is not None
    assert first_score > second_score


async def test_semantic_search_raises_when_no_provider_is_available(
    repo: MemoryRepository,
) -> None:
    semantic = SemanticMemory(repo, NullEmbeddingProvider(), default_namespace="user")
    assert semantic.available is False
    with pytest.raises(DependencyUnavailableError):
        await semantic.search("anything", namespace="user")


async def test_semantic_skips_a_row_of_the_wrong_width(repo: MemoryRepository) -> None:
    embedder = ScriptedEmbedder({"a correct item": [1.0, 0.0, 0.0]}, dimensions=3)
    semantic = SemanticMemory(repo, embedder, min_similarity=0.0, default_namespace="user")
    await semantic.index("a correct item", namespace="user")
    await repo.remember(
        namespace=namespaces.USER,
        namespace_kind="user",
        namespace_slug="",
        content="wrong width",
        layer=MemoryLayer.SEMANTIC,
        embedding=[1.0, 0.0],
    )

    results = await semantic.search("a correct item", namespace="user", top_k=5)
    assert [record.content for record in results] == ["a correct item"]


async def test_semantic_clear_removes_only_semantic_rows(repo: MemoryRepository) -> None:
    semantic = SemanticMemory(repo, ScriptedEmbedder({}, dimensions=3), default_namespace="user")
    await semantic.index("a semantic item", namespace="user")
    await LongTermMemory(repo).remember("a fact", namespace="user")

    assert await semantic.clear(namespace="user") == 1
    assert await LongTermMemory(repo).recall(namespace="user") != []


# --------------------------------------------------------------------------- #
# Project (T125)
# --------------------------------------------------------------------------- #


async def test_project_memory_is_scoped_by_slug(repo: MemoryRepository) -> None:
    layer = ProjectMemory(repo)
    await layer.remember("campuscare", "user is a student")
    await layer.remember("ultron", "a home assistant")

    campus = await layer.recall("campuscare")
    assert [record.content for record in campus] == ["user is a student"]


async def test_project_memory_rejects_an_empty_slug(repo: MemoryRepository) -> None:
    layer = ProjectMemory(repo)
    with pytest.raises(InvalidInputError):
        await layer.remember("", "context")


# --------------------------------------------------------------------------- #
# Short-term (T121)
# --------------------------------------------------------------------------- #


async def test_short_term_appends_to_both_redis_and_the_mirror(
    repo: MemoryRepository,
    redis: RedisClient,
) -> None:
    layer = ShortTermMemory(repo, redis, max_messages=3)
    await layer.append("user", "first", namespace="user")
    await layer.append("assistant", "second", namespace="user")

    recent = await layer.recent(namespace="user")
    assert [message.content for message in recent] == ["first", "second"]
    rows = await repo.list_by_layer("user", MemoryLayer.SHORT_TERM)
    assert len(rows) == 2


async def test_short_term_window_is_bounded(repo: MemoryRepository, redis: RedisClient) -> None:
    layer = ShortTermMemory(repo, redis, max_messages=2)
    for index in range(5):
        await layer.append("user", f"m{index}", namespace="user")

    recent = await layer.recent(namespace="user")
    assert [message.content for message in recent] == ["m3", "m4"]


async def test_short_term_falls_back_to_the_mirror_in_chronological_order(
    repo: MemoryRepository,
) -> None:
    layer = ShortTermMemory(repo, None, max_messages=10)
    await layer.append("user", "first", namespace="user")
    await layer.append("assistant", "second", namespace="user")

    recent = await layer.recent(namespace="user")
    assert [message.content for message in recent] == ["first", "second"]


async def test_short_term_outage_does_not_clobber_the_mirror(
    repo: MemoryRepository,
    redis: RedisClient,
) -> None:
    layer = ShortTermMemory(repo, redis, max_messages=10)
    await layer.append("user", "kept", namespace="user")

    offline = ShortTermMemory(
        repo,
        RedisClient(cast("Redis", _BrokenRedis())),
        max_messages=10,
    )
    await offline.append("user", "after outage", namespace="user")
    recovered = await offline.recent(namespace="user")
    assert [message.content for message in recovered] == ["kept", "after outage"]


async def test_short_term_clear_removes_the_key_and_the_mirror(
    repo: MemoryRepository,
    redis: RedisClient,
) -> None:
    layer = ShortTermMemory(repo, redis, max_messages=10)
    await layer.append("user", "a message", namespace="user")
    await LongTermMemory(repo).remember("a fact", namespace="user")

    assert await layer.clear(namespace="user") == 1
    assert await layer.recent(namespace="user") == []
    assert await redis.get_or_none("memory:short:user") is None
    assert await LongTermMemory(repo).recall(namespace="user") != []


# --------------------------------------------------------------------------- #
# Manager facade (T126)
# --------------------------------------------------------------------------- #


def _manager(repo: MemoryRepository, redis: RedisClient) -> MemoryManager:
    return MemoryManager(
        short_term=ShortTermMemory(repo, redis, max_messages=10),
        long_term=LongTermMemory(repo),
        episodic=EpisodicMemory(repo),
        semantic=SemanticMemory(
            repo,
            HashEmbeddingProvider(dimensions=16),
            min_similarity=0.0,
            default_namespace="user",
        ),
        project=ProjectMemory(repo),
        default_namespace="user",
        retrieval_top_k=8,
        min_similarity=0.0,
    )


async def test_manager_remember_dispatches_to_the_named_layer(
    repo: MemoryRepository,
    redis: RedisClient,
) -> None:
    manager = _manager(repo, redis)
    await manager.remember("a fact", namespace="user", layer=MemoryLayer.LONG_TERM)
    await manager.remember(
        "did a thing",
        namespace="user",
        layer=MemoryLayer.EPISODIC,
        outcome="done",
    )

    assert len(await manager.long_term.recall(namespace="user")) == 1
    assert (await manager.episodic.recent(namespace="user"))[0].outcome == "done"


async def test_manager_refuses_to_write_short_term_through_remember(
    repo: MemoryRepository,
    redis: RedisClient,
) -> None:
    manager = _manager(repo, redis)
    with pytest.raises(ValueError):
        await manager.remember("x", namespace="user", layer=MemoryLayer.SHORT_TERM)


async def test_manager_project_layer_requires_a_project_namespace(
    repo: MemoryRepository,
    redis: RedisClient,
) -> None:
    manager = _manager(repo, redis)
    with pytest.raises(ValueError):
        await manager.remember("context", namespace="user", layer=MemoryLayer.PROJECT)


async def test_manager_recall_merges_layers_and_ranks_by_score(
    repo: MemoryRepository,
    redis: RedisClient,
) -> None:
    manager = _manager(repo, redis)
    await manager.remember("a strong fact", namespace="user", importance=0.9)
    await manager.record_episode("weaker work", outcome="done", namespace="user")

    recalled = await manager.recall(namespace="user")
    assert [record.layer for record in recalled] == [MemoryLayer.LONG_TERM, MemoryLayer.EPISODIC]


async def test_manager_append_message_feeds_short_term(
    repo: MemoryRepository,
    redis: RedisClient,
) -> None:
    manager = _manager(repo, redis)
    await manager.append_message("user", "hello", namespace="user")
    messages = await manager.recent_messages(namespace="user")
    assert messages[0].content == "hello"


async def test_manager_build_context_returns_prompt_lines(
    repo: MemoryRepository,
    redis: RedisClient,
) -> None:
    manager = _manager(repo, redis)
    await manager.remember("the user likes tea", namespace="user")
    lines = await manager.build_context(namespace="user")
    assert lines and lines[0].startswith("[")


# --------------------------------------------------------------------------- #
# Episode recorder (T128)
# --------------------------------------------------------------------------- #


@asynccontextmanager
async def _scope(session: AsyncSession) -> AsyncIterator[AsyncSession]:
    yield session


async def test_recorder_turns_a_completed_task_into_an_episode(
    repo: MemoryRepository,
    db: AsyncSession,
) -> None:
    bus = EventBus()
    recorder = MemoryRecorder(bus, session_scope=lambda: _scope(db))
    await recorder.start()
    try:
        await bus.publish(
            EventType.TASK_COMPLETED.value,
            {"objective": "run the suite"},
            task_id=str(uuid.uuid4()),
            agent_id=str(uuid.uuid4()),
        )
        await _await(lambda: recorder.recorded == 1)
    finally:
        await recorder.stop()

    episodes = await EpisodicMemory(repo).recent(namespace="user")
    assert episodes[0].summary == "run the suite"
    assert episodes[0].outcome == "completed"


async def test_recorder_ignores_events_that_are_not_task_terminal(
    db: AsyncSession,
) -> None:
    bus = EventBus()
    recorder = MemoryRecorder(bus, session_scope=lambda: _scope(db))
    await recorder.start()
    try:
        await bus.publish(EventType.TASK_STARTED.value, {"step": "plan"})
        await asyncio.sleep(0.02)
        assert recorder.recorded == 0
    finally:
        await recorder.stop()


async def test_recorder_records_a_failure_as_an_episode(db: AsyncSession) -> None:
    bus = EventBus()
    recorder = MemoryRecorder(bus, session_scope=lambda: _scope(db))
    await recorder.start()
    try:
        await bus.publish(
            EventType.TASK_FAILED.value,
            {"description": "the build broke"},
            task_id=str(uuid.uuid4()),
        )
        await _await(lambda: recorder.recorded == 1)
    finally:
        await recorder.stop()

    episodes = await EpisodicMemory(MemoryRepository(db)).recent(namespace="user")
    assert episodes[0].outcome == "failed"
    assert episodes[0].importance == 0.7


async def test_recorder_survives_a_failing_write(db: AsyncSession) -> None:
    bus = EventBus()

    class _Boom:
        async def __aenter__(self) -> AsyncSession:
            raise RuntimeError("database gone")

        async def __aexit__(self, *_exc: object) -> None:
            return None

    recorder = MemoryRecorder(bus, session_scope=lambda: _Boom())
    await recorder.start()
    try:
        await bus.publish(EventType.TASK_COMPLETED.value, {"objective": "x"})
        await _await(lambda: recorder.failed == 1)
    finally:
        await recorder.stop()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _record(
    *,
    layer: MemoryLayer = MemoryLayer.LONG_TERM,
    created_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> MemoryRecord:
    """A MemoryRecord for assertions that do not need a database row."""
    stamp = created_at or datetime.now(UTC)
    return MemoryRecord(
        id=uuid.uuid4(),
        namespace="user",
        kind="user",
        slug="",
        layer=layer,
        content="a value",
        importance=0.5,
        created_at=stamp,
        expires_at=expires_at,
    )


async def _await(predicate: Any, *, limit: float = 2.0) -> None:
    """Poll ``predicate`` until it holds, or fail the test."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + limit
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("timed out waiting for the recorder to record")
        await asyncio.sleep(0.01)
