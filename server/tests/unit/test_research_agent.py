"""T114 tests: the research agent (spec §10 + §59.10).

The agent is a fixed pipeline, not a tool-calling loop, so it is exercised with
a real ``ToolExecutor`` (§16) over two fake web tools and a scripted
``FakeProvider`` (T065) for the single synthesis call. That keeps the flow real
— search → per-source fetch → numbered synthesis → cited report — while the
network, the search backend and the model stay in-process. The provenance
assertions are the point: URLs and fetch times preserved, citations bound to
collected sources, unsupported claims counted, failed fetches recorded not
raised.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, ClassVar

import pytest

from app.agents.research import ResearchAgent
from app.agents.research.artifacts import ResearchArtifactStore
from app.agents.research.report import ResearchReport, Source
from app.config import ModelRouterSettings
from app.core.errors import (
    AgentError,
    DependencyUnavailableError,
    InvalidInputError,
    NotFoundError,
)
from app.core.permissions import PermissionManager
from app.database.models import PermissionLevel, VerificationOutcome
from app.events.bus import EventBus
from app.models.base import FinishReason, ModelResponse
from app.models.fake import FakeProvider
from app.models.router import ModelRouter
from app.security.audit import AuditLogger
from app.tools.base import Reversibility, Tool
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry
from app.tools.web.search_provider import SearchProvider, SearchResult, SearchResults
from app.tools.web.search_tool import WebSearchTool

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# Doubles
# --------------------------------------------------------------------------- #
class FakeSearchProvider(SearchProvider):
    """A search backend that returns scripted hits (or a scripted failure)."""

    name = "fake"

    def __init__(
        self,
        results: Sequence[SearchResult] = (),
        *,
        error: Exception | None = None,
    ) -> None:
        self._results = list(results)
        self._error = error
        self.calls: list[str] = []

    async def search(
        self,
        query: str,
        *,
        max_results: int = 10,
        safe_search: bool = True,
        language: str | None = None,
        region: str | None = None,
    ) -> SearchResults:
        self.calls.append(query)
        if self._error is not None:
            raise self._error
        return SearchResults(
            query=query, results=self._results[:max_results], provider=self.name
        )


class FakeFetchTool(Tool):
    """A ``web.fetch`` that returns scripted pages, keyed by URL."""

    name = "web.fetch"
    description = "Fetch a page (fake)."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"url": {"type": "string", "minLength": 1}},
        "required": ["url"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.READ_ONLY
    timeout = 5.0
    reversibility = Reversibility.REVERSIBLE

    def __init__(self, pages: Mapping[str, Any], *, errors: Mapping[str, Exception] | None = None):
        self._pages = dict(pages)
        self._errors = dict(errors or {})
        self.calls: list[str] = []

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        url = str(arguments["url"])
        self.calls.append(url)
        if url in self._errors:
            raise self._errors[url]
        if url not in self._pages:
            raise DependencyUnavailableError(url, reason="no page")
        return self._pages[url]

    async def verify(self, result: Any, arguments: Mapping[str, Any]) -> VerificationOutcome:
        return VerificationOutcome.SUCCESS


class RecordingBus(EventBus):
    """A real bus that keeps every published event, for assertions."""

    def __init__(self) -> None:
        super().__init__()
        self.published: list[tuple[str, dict[str, Any]]] = []

    async def publish(self, event_type: Any, payload: Any = None, **kwargs: Any) -> Any:
        self.published.append((str(event_type), dict(payload or {})))
        return await super().publish(event_type, payload, **kwargs)


def _page(text: str, *, final_url: str | None = None) -> dict[str, Any]:
    return {
        "url": final_url or "https://example.test/x",
        "final_url": final_url or "https://example.test/x",
        "status_code": 200,
        "content_type": "text/html",
        "text": text,
        "truncated": False,
    }


def _answer(text: str = "It is documented in [1].") -> ModelResponse:
    return ModelResponse(
        model="llama3",
        content=text,
        finish_reason=FinishReason.STOP,
        provider="fake",
    )


def _provider(*responses: ModelResponse) -> FakeProvider:
    return FakeProvider(name="ollama", local=True, responses=responses)


def _router(provider: FakeProvider) -> ModelRouter:
    return ModelRouter(
        [provider],
        settings=ModelRouterSettings(default="ollama:llama3", fallback_chain=[]),
    )


def _executor(registry: ToolRegistry, audit_sink: Any) -> ToolExecutor:
    return ToolExecutor(
        registry=registry,
        permissions=PermissionManager(
            audit=AuditLogger(audit_sink),
            default_level=PermissionLevel.DESTRUCTIVE,
        ),
        events=EventBus(),
    )


def _registry(
    provider: SearchProvider, fetch: Tool
) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(WebSearchTool(provider))
    registry.register(fetch)
    return registry


def _agent(
    *,
    provider: FakeProvider,
    search: SearchProvider,
    fetch: Tool,
    audit_sink: Any,
    bus: EventBus | None = None,
    **kwargs: Any,
) -> ResearchAgent:
    registry = _registry(search, fetch)
    return ResearchAgent(
        router=_router(provider),
        executor=_executor(registry, audit_sink),
        bus=bus,
        **kwargs,
    )


def _hit(url: str, title: str = "Result") -> SearchResult:
    return SearchResult(title=title, url=url, snippet="a snippet")


# --------------------------------------------------------------------------- #
# Happy path: search -> per-source fetch -> cited synthesis
# --------------------------------------------------------------------------- #
async def test_agent_searches_fetches_and_synthesizes(audit_sink: Any) -> None:
    search = FakeSearchProvider([_hit("https://a.test/1"), _hit("https://b.test/2")])
    fetch = FakeFetchTool(
        {
            "https://a.test/1": _page("Alpha fact.", final_url="https://a.test/1"),
            "https://b.test/2": _page("Beta fact.", final_url="https://b.test/2"),
        }
    )
    provider = _provider(_answer("Alpha says [1] and Beta agrees [2]."))
    agent = _agent(provider=provider, search=search, fetch=fetch, audit_sink=audit_sink)

    report = await agent.run({"question": "what is the fact?", "task_id": "t-1"})

    assert report["agent"] == "research.agent"
    assert report["question"] == "what is the fact?"
    assert report["task_id"] == "t-1"
    assert report["model"] == "llama3"
    assert report["answer"] == "Alpha says [1] and Beta agrees [2]."
    # Two sources collected, each with its URL and fetch time (§59.10).
    assert [s["index"] for s in report["sources"]] == [1, 2]
    assert all(s["url"] and s["fetched_at"] for s in report["sources"])
    # The answer's citations resolve to those sources.
    assert [c["index"] for c in report["citations"]] == [1, 2]
    assert search.calls == ["what is the fact?"]
    assert fetch.calls == ["https://a.test/1", "https://b.test/2"]


async def test_agent_preserves_the_final_url_after_redirect(audit_sink: Any) -> None:
    search = FakeSearchProvider([_hit("https://a.test/old")])
    fetch = FakeFetchTool(
        {"https://a.test/old": _page("Moved.", final_url="https://a.test/new")}
    )
    agent = _agent(
        provider=_provider(_answer("Answer [1].")),
        search=search,
        fetch=fetch,
        audit_sink=audit_sink,
    )

    report = await agent.run({"question": "q"})

    assert report["sources"][0]["url"] == "https://a.test/old"
    assert report["sources"][0]["final_url"] == "https://a.test/new"
    # The citation points a reader at where it actually landed.
    assert report["citations"][0]["url"] == "https://a.test/new"


async def test_agent_uses_seed_urls_without_searching(audit_sink: Any) -> None:
    search = FakeSearchProvider([_hit("https://ignored.test/")])
    fetch = FakeFetchTool({"https://seed.test/": _page("Seed body.")})
    agent = _agent(
        provider=_provider(_answer("From the seed [1].")),
        search=search,
        fetch=fetch,
        audit_sink=audit_sink,
    )

    report = await agent.run({"question": "q", "urls": ["https://seed.test/"]})

    assert search.calls == []
    assert fetch.calls == ["https://seed.test/"]
    assert [s["url"] for s in report["sources"]] == ["https://seed.test/"]


# --------------------------------------------------------------------------- #
# Provenance: citations, unsupported markers, no self-citation
# --------------------------------------------------------------------------- #
async def test_agent_counts_unsupported_claims(audit_sink: Any) -> None:
    search = FakeSearchProvider([_hit("https://a.test/1")])
    fetch = FakeFetchTool({"https://a.test/1": _page("Only this.")})
    agent = _agent(
        provider=_provider(_answer("Supported [1]. This is [unsupported].")),
        search=search,
        fetch=fetch,
        audit_sink=audit_sink,
    )

    report = await agent.run({"question": "q"})

    assert report["unsupported"] == 1
    assert [c["index"] for c in report["citations"]] == [1]


async def test_agent_ignores_a_citation_to_a_source_it_did_not_collect(
    audit_sink: Any,
) -> None:
    search = FakeSearchProvider([_hit("https://a.test/1")])
    fetch = FakeFetchTool({"https://a.test/1": _page("Only this.")})
    agent = _agent(
        provider=_provider(_answer("Trust me [7].")),
        search=search,
        fetch=fetch,
        audit_sink=audit_sink,
    )

    report = await agent.run({"question": "q"})

    assert report["citations"] == []


async def test_agent_hands_the_model_numbered_sources_and_no_source_rule(
    audit_sink: Any,
) -> None:
    search = FakeSearchProvider([_hit("https://a.test/1", title="Alpha")])
    fetch = FakeFetchTool({"https://a.test/1": _page("Alpha body.", final_url="https://a.test/1")})
    provider = _provider(_answer("Answer [1]."))
    agent = _agent(provider=provider, search=search, fetch=fetch, audit_sink=audit_sink)

    await agent.run({"question": "what?"})

    assert provider.last_request is not None
    messages = provider.last_request.messages
    system, user = messages[0], messages[1]
    assert "never cite yourself" in system.content
    assert "[unsupported]" in system.content
    assert "Question: what?" in user.content
    assert "[1] Alpha — https://a.test/1" in user.content
    assert "Alpha body." in user.content


async def test_agent_reports_no_sources_without_fabricating(audit_sink: Any) -> None:
    search = FakeSearchProvider([])
    fetch = FakeFetchTool({})
    provider = _provider(_answer("I could not retrieve any sources."))
    agent = _agent(provider=provider, search=search, fetch=fetch, audit_sink=audit_sink)

    report = await agent.run({"question": "q"})

    assert report["sources"] == []
    assert report["citations"] == []
    assert provider.last_request is not None
    assert "No sources could be retrieved" in provider.last_request.messages[1].content


# --------------------------------------------------------------------------- #
# Bounded, resilient retrieval
# --------------------------------------------------------------------------- #
async def test_agent_records_a_failed_fetch_as_skipped_and_continues(
    audit_sink: Any,
) -> None:
    search = FakeSearchProvider([_hit("https://good.test/"), _hit("https://bad.test/")])
    fetch = FakeFetchTool(
        {"https://good.test/": _page("Good.")},
        errors={"https://bad.test/": DependencyUnavailableError("https://bad.test/")},
    )
    agent = _agent(
        provider=_provider(_answer("Only the good one [1].")),
        search=search,
        fetch=fetch,
        audit_sink=audit_sink,
    )

    report = await agent.run({"question": "q"})

    assert [s["url"] for s in report["sources"]] == ["https://good.test/"]
    assert [s["url"] for s in report["skipped"]] == ["https://bad.test/"]
    assert report["skipped"][0]["error"]


async def test_agent_caps_how_many_sources_it_uses(audit_sink: Any) -> None:
    search = FakeSearchProvider([_hit(f"https://s{i}.test/") for i in range(5)])
    fetch = FakeFetchTool({f"https://s{i}.test/": _page(f"body {i}") for i in range(5)})
    agent = _agent(
        provider=_provider(_answer("ok [1]")),
        search=search,
        fetch=fetch,
        audit_sink=audit_sink,
        max_sources=2,
    )

    report = await agent.run({"question": "q"})

    assert len(report["sources"]) == 2
    assert fetch.calls == ["https://s0.test/", "https://s1.test/"]


async def test_agent_truncates_a_long_source(audit_sink: Any) -> None:
    search = FakeSearchProvider([_hit("https://a.test/1")])
    fetch = FakeFetchTool({"https://a.test/1": _page("x" * 100)})
    provider = _provider(_answer("ok [1]"))
    agent = _agent(
        provider=provider,
        search=search,
        fetch=fetch,
        audit_sink=audit_sink,
        max_chars_per_source=10,
    )

    report = await agent.run({"question": "q"})

    assert report["sources"][0]["chars"] == 10
    assert report["sources"][0]["truncated"] is True
    assert provider.last_request is not None
    assert "x" * 10 in provider.last_request.messages[1].content
    assert "x" * 11 not in provider.last_request.messages[1].content


async def test_run_max_sources_cannot_exceed_the_configured_cap(audit_sink: Any) -> None:
    search = FakeSearchProvider([_hit(f"https://s{i}.test/") for i in range(5)])
    fetch = FakeFetchTool({f"https://s{i}.test/": _page("b") for i in range(5)})
    agent = _agent(
        provider=_provider(_answer("ok [1]")),
        search=search,
        fetch=fetch,
        audit_sink=audit_sink,
        max_sources=2,
    )

    report = await agent.run({"question": "q", "max_sources": 99})

    assert len(report["sources"]) == 2


# --------------------------------------------------------------------------- #
# Progress events
# --------------------------------------------------------------------------- #
async def test_agent_publishes_the_pipeline_phases(audit_sink: Any) -> None:
    bus = RecordingBus()
    search = FakeSearchProvider([_hit("https://a.test/1")])
    fetch = FakeFetchTool({"https://a.test/1": _page("body")})
    agent = _agent(
        provider=_provider(_answer("ok [1]")),
        search=search,
        fetch=fetch,
        audit_sink=audit_sink,
        bus=bus,
    )

    await agent.run({"question": "q"})

    phases = [payload["phase"] for _, payload in bus.published if "phase" in payload]
    assert phases == ["search", "retrieve", "synthesize", "report"]
    assert all(kind == "TASK_PROGRESS" for kind, _ in bus.published)


# --------------------------------------------------------------------------- #
# Context handling and construction
# --------------------------------------------------------------------------- #
async def test_agent_requires_a_question(audit_sink: Any) -> None:
    agent = _agent(
        provider=_provider(_answer()),
        search=FakeSearchProvider(),
        fetch=FakeFetchTool({}),
        audit_sink=audit_sink,
    )
    with pytest.raises(InvalidInputError):
        await agent.run({})


async def test_agent_accepts_prompt_as_a_question_alias(audit_sink: Any) -> None:
    agent = _agent(
        provider=_provider(_answer("ok")),
        search=FakeSearchProvider([]),
        fetch=FakeFetchTool({}),
        audit_sink=audit_sink,
    )

    report = await agent.run({"prompt": "aliased question"})

    assert report["question"] == "aliased question"


async def test_agent_falls_back_to_its_own_id_for_the_task(audit_sink: Any) -> None:
    agent = _agent(
        provider=_provider(_answer("ok")),
        search=FakeSearchProvider([]),
        fetch=FakeFetchTool({}),
        audit_sink=audit_sink,
    )

    report = await agent.run({"question": "q"})

    assert report["task_id"] == str(agent.agent_id)


def test_agent_declares_its_type() -> None:
    assert ResearchAgent.agent_type == "research.agent"
    assert ResearchAgent.description


def test_agent_rejects_a_nonsense_source_cap(audit_sink: Any) -> None:
    with pytest.raises(ValueError, match="max_sources"):
        ResearchAgent(
            router=_router(_provider(_answer())),
            executor=_executor(
                _registry(FakeSearchProvider(), FakeFetchTool({})), audit_sink
            ),
            max_sources=0,
        )


async def test_agent_rejects_a_bad_max_sources_in_context(audit_sink: Any) -> None:
    agent = _agent(
        provider=_provider(_answer()),
        search=FakeSearchProvider(),
        fetch=FakeFetchTool({}),
        audit_sink=audit_sink,
    )
    with pytest.raises(InvalidInputError):
        await agent.run({"question": "q", "max_sources": -1})


# --------------------------------------------------------------------------- #
# Failure translation
# --------------------------------------------------------------------------- #
async def test_agent_passes_a_search_failure_through_untouched(audit_sink: Any) -> None:
    search = FakeSearchProvider(error=DependencyUnavailableError("web.search"))
    agent = _agent(
        provider=_provider(_answer()),
        search=search,
        fetch=FakeFetchTool({}),
        audit_sink=audit_sink,
    )
    with pytest.raises(DependencyUnavailableError):
        await agent.run({"question": "q"})


async def test_agent_wraps_an_unexpected_error(audit_sink: Any) -> None:
    provider = _provider()
    provider.queue_error(ValueError("boom"))
    agent = _agent(
        provider=provider,
        search=FakeSearchProvider([]),
        fetch=FakeFetchTool({}),
        audit_sink=audit_sink,
    )

    with pytest.raises(AgentError) as excinfo:
        await agent.run({"question": "q"})
    assert isinstance(excinfo.value.cause, ValueError)


# --------------------------------------------------------------------------- #
# Identity binding (§15/§16): every web access is attributed to the agent
# --------------------------------------------------------------------------- #
async def test_agent_attributes_web_calls_to_itself(audit_sink: Any) -> None:
    search = FakeSearchProvider([_hit("https://a.test/1")])
    fetch = FakeFetchTool({"https://a.test/1": _page("body")})
    agent = _agent(
        provider=_provider(_answer("ok [1]")),
        search=search,
        fetch=fetch,
        audit_sink=audit_sink,
    )

    await agent.run({"question": "q", "task_id": "t-9"})

    rows = [row for row in audit_sink.rows if row.get("resource_type") == "tool"]
    assert rows
    assert {str(row["actor_id"]) for row in rows} == {str(agent.agent_id)}
    assert {row["resource_id"] for row in rows} == {"web.search", "web.fetch"}


# --------------------------------------------------------------------------- #
# The report artifact itself (T114's structured output)
# --------------------------------------------------------------------------- #
def test_report_serialises_sources_citations_and_skips() -> None:
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    first = Source(
        index=1,
        title="Alpha",
        url="https://a.test/old",
        final_url="https://a.test/new",
        fetched_at=now,
        status_code=200,
        text="body",
    )
    report = ResearchReport(
        agent="research.agent",
        task_id="t1",
        question="q",
        answer="A [1] and B [2].",
        sources=(first,),
        citations=(1, 2),
        unsupported=0,
        model="llama3",
        generated_at=now,
        skipped=({"url": "https://bad.test/", "error": "dependency_unavailable"},),
    )

    data = report.to_dict()

    assert data["agent"] == "research.agent"
    assert data["sources"][0]["url"] == "https://a.test/old"
    # Only the collected source appears among the citations; [2] is dangling.
    assert [c["index"] for c in data["citations"]] == [1]
    assert data["citations"][0]["url"] == "https://a.test/new"
    assert data["skipped"][0]["url"] == "https://bad.test/"
    assert report.cited_sources() == (first,)


# --------------------------------------------------------------------------- #
# T115: the report persisted as a first-class artifact (§59.10)
# --------------------------------------------------------------------------- #
def _sample_report() -> ResearchReport:
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    source = Source(
        index=1,
        title="Alpha",
        url="https://a.test/1",
        final_url="https://a.test/1",
        fetched_at=now,
        status_code=200,
        text="body",
    )
    return ResearchReport(
        agent="research.agent",
        task_id="t1",
        question="q",
        answer="A [1].",
        sources=(source,),
        citations=(1,),
        unsupported=0,
        model="llama3",
        generated_at=now,
    )


async def test_store_saves_and_loads_a_report(tmp_path: Any) -> None:
    store = ResearchArtifactStore(tmp_path / "artifacts")

    artifact_id = await store.save(_sample_report())
    document = await store.load(artifact_id)

    assert document["artifact_id"] == artifact_id
    assert document["question"] == "q"
    assert document["answer"] == "A [1]."
    assert document["sources"][0]["url"] == "https://a.test/1"
    assert document["citations"][0]["index"] == 1
    assert document["stored_at"]


async def test_store_creates_its_directory_on_first_save(tmp_path: Any) -> None:
    root = tmp_path / "nested" / "research"
    store = ResearchArtifactStore(root)
    assert not root.exists()

    await store.save(_sample_report())

    assert root.is_dir()


async def test_store_loads_a_missing_artifact_as_not_found(tmp_path: Any) -> None:
    store = ResearchArtifactStore(tmp_path)
    with pytest.raises(NotFoundError):
        await store.load("research-20260101T000000-deadbeef")


def test_store_refuses_a_traversing_id(tmp_path: Any) -> None:
    store = ResearchArtifactStore(tmp_path)
    with pytest.raises(InvalidInputError):
        store.path_for("../../etc/passwd")


async def test_store_leaves_no_temporary_file_behind(tmp_path: Any) -> None:
    store = ResearchArtifactStore(tmp_path)
    artifact_id = await store.save(_sample_report())

    files = sorted(p.name for p in tmp_path.iterdir())
    assert files == [f"{artifact_id}.json"]


async def test_agent_persists_the_report_when_given_a_store(
    tmp_path: Any, audit_sink: Any
) -> None:
    store = ResearchArtifactStore(tmp_path / "artifacts")
    search = FakeSearchProvider([_hit("https://a.test/1")])
    fetch = FakeFetchTool({"https://a.test/1": _page("body", final_url="https://a.test/1")})
    agent = _agent(
        provider=_provider(_answer("Answer [1].")),
        search=search,
        fetch=fetch,
        audit_sink=audit_sink,
        artifacts=store,
    )

    report = await agent.run({"question": "q", "task_id": "t-1"})

    assert report["artifact_id"]
    stored = await store.load(report["artifact_id"])
    assert stored["task_id"] == "t-1"
    assert stored["question"] == "q"
    # What was retrieved is stored alongside what was concluded (§59.10).
    assert [s["url"] for s in stored["sources"]] == ["https://a.test/1"]
    assert stored["citations"][0]["index"] == 1


async def test_agent_does_not_persist_without_a_store(audit_sink: Any) -> None:
    agent = _agent(
        provider=_provider(_answer("ok")),
        search=FakeSearchProvider([]),
        fetch=FakeFetchTool({}),
        audit_sink=audit_sink,
    )

    report = await agent.run({"question": "q"})

    assert "artifact_id" not in report

