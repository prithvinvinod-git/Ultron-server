"""The research agent (T114): §10's worker with §59.10's provenance discipline.

§10 asks for an agent that can search, retrieve, extract, compare, summarise,
cite and preserve source URLs, and states one rule about how: **"Use a Web Tool
abstraction. Do not make the LLM directly perform arbitrary HTTP requests."**
§59.10 then adds the provenance contract that turns a summary into *research*:
extraction happens **per source, before any reasoning**, every factual claim is
bound to a collected source, and a claim no source supports is omitted or marked
unsupported — never presented as established. Model text is not a source, so the
agent never cites itself.

This agent is that pipeline. It does not use a tool-calling loop: the stages are
fixed and ordered, so it runs them itself and makes exactly one model call, for
synthesis:

```text
question ──► web.search ──► candidates ──► web.fetch (per source)
                                              │  (URL + fetched_at recorded)
                                              ▼
                                       per-source extraction
                                              ▼
                              one model call: cite [n], mark [unsupported]
                                              ▼
                            ResearchReport (sources vs conclusions, §59.10)
```

Every web access goes through the §16 pipeline (T039) by *name* — ``web.search``
and ``web.fetch`` — so the SSRF guard, permission check, verification and event
emission that already govern those tools govern research too; the model never
touches HTTP. The agent is handed its collaborators (router T066, executor T039)
rather than naming them (§40), exactly like the coding and browser agents.

`run(context)` is one research question:

- ``question`` (required; ``prompt`` is accepted as an alias) — what to research.
- ``urls`` (optional) — seed URLs to fetch directly, skipping search.
- ``max_sources`` (optional) — cap for this run over the agent's configured cap.
- ``task_id``, ``principal`` — as every agent (§6/§15).

The return is the serialised :class:`~app.agents.research.report.ResearchReport`
— sources with URLs and fetch times, the answer, the citations it used, and the
count of unsupported claims. When an artifact store is configured (T115), the
report is also persisted as a first-class artifact and its id is returned.
Failure discipline mirrors the runtime (T044): a
typed :class:`UltronError` (a failed search, a provider error) is re-raised so
its code survives; a *single* source that cannot be fetched is recorded as
skipped, not fatal (§33: a failure is information); anything unexpected is
wrapped in ``AgentError``. The agent does not drive its own lifecycle — the
manager owns the ``agents`` row and its ``AGENT_*`` events.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from app.agents.base import Agent
from app.core.errors import AgentError, InvalidInputError, UltronError
from app.database.models import MessageRole
from app.events.bus import EventBus
from app.events.types import EventType
from app.models.base import CompletionRequest, ModelMessage
from app.models.router import ModelRouter
from app.tools.executor import ToolExecutor

from .artifacts import ResearchArtifactStore
from .report import ResearchReport, Source

__all__ = ["ResearchAgent"]

#: The synthesis instruction: the whole of §59.10's output contract in one place.
_DEFAULT_SYSTEM_PROMPT = (
    "You are ULTRON's research agent. Answer the question using ONLY the "
    "numbered sources below. Cite each factual claim with the source number in "
    "square brackets, e.g. [1]. When sources disagree, say so and cite both. If "
    "a statement is not supported by any source, omit it or mark it "
    "[unsupported] — never present unsupported text as established. You are not "
    "a source: never cite yourself."
)

#: A citation the model wrote: ``[12]``. Deliberately digits only, so the
#: ``[unsupported]`` marker is never mistaken for a source reference.
_CITATION = re.compile(r"\[(\d+)\]")

#: The marker §59.10 uses for a claim no collected source supports.
_UNSUPPORTED = "[unsupported]"


class ResearchAgent(Agent):
    """Search, fetch per source, synthesise with citations, report (§10/§59.10)."""

    agent_type = "research.agent"
    description = "Research a question across web sources and return a cited report."

    def __init__(
        self,
        *,
        router: ModelRouter,
        executor: ToolExecutor,
        search_tool: str = "web.search",
        fetch_tool: str = "web.fetch",
        bus: EventBus | None = None,
        capability: str = "default",
        max_sources: int = 5,
        max_results: int = 10,
        max_chars_per_source: int = 4000,
        artifacts: ResearchArtifactStore | None = None,
        system_prompt: str | None = None,
        **agent_kwargs: Any,
    ) -> None:
        """Wire the agent to the router and the §16 pipeline.

        ``search_tool``/``fetch_tool`` are resolved by *name* through the
        executor, so the agent depends on the web tool abstraction (§10), not on
        a provider. ``max_sources`` caps how many documents are fetched and cited
        (a per-run ``max_sources`` may lower it, never raise it); ``max_results``
        is how many hits search is asked for; ``max_chars_per_source`` bounds the
        per-source extraction handed to the synthesis prompt.

        ``artifacts`` is the T115 store: when given, every completed report is
        persisted as a first-class artifact (§59.10) and its id rides along in
        the returned report. A store that is not given leaves persistence to the
        caller.

        All §6 assignment kwargs (``name``, ``task_id``, ``model``, …) pass
        through to :class:`~app.agents.base.Agent`.
        """
        super().__init__(**agent_kwargs)
        self._router = router
        self._executor = executor
        self._search_tool = search_tool
        self._fetch_tool = fetch_tool
        self._bus = bus
        self._capability = capability
        self._artifacts = artifacts
        self._max_sources = _positive_int("max_sources", max_sources)
        self._max_results = _positive_int("max_results", max_results)
        self._max_chars_per_source = _positive_int("max_chars_per_source", max_chars_per_source)
        self._system_prompt = system_prompt or _DEFAULT_SYSTEM_PROMPT

    async def run(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        """Research one question over ``context`` and return its cited report."""
        try:
            return await self._run(context)
        except UltronError:
            raise
        except Exception as exc:
            raise AgentError(
                str(self.agent_id),
                "research agent failed unexpectedly",
                details={"agent_type": self.agent_type},
                cause=exc,
            ) from exc

    # -- the pipeline ------------------------------------------------------- #
    async def _run(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        question = _require_question(context)
        task_id = _task_id(context) or _own_task_id(self)
        principal = _optional_text(context, "principal") or str(self.agent_id)
        seeds = _urls(context)
        limit = min(_max_sources(context) or self._max_sources, self._max_sources)

        await self._progress(task_id, {"task_id": task_id, "phase": "search"})
        searched = [] if seeds else await self._search(question, principal, task_id)
        candidates = self._candidates(seeds, searched)[:limit]

        await self._progress(task_id, {"task_id": task_id, "phase": "retrieve"})
        sources, skipped = await self._retrieve(candidates, principal, task_id)

        await self._progress(task_id, {"task_id": task_id, "phase": "synthesize"})
        answer, model = await self._synthesize(question, sources)

        citations, unsupported = _parse_citations(answer, sources)
        report = ResearchReport(
            agent=self.agent_type,
            task_id=task_id,
            question=question,
            answer=answer,
            sources=tuple(sources),
            citations=citations,
            unsupported=unsupported,
            model=model,
            generated_at=datetime.now(UTC),
            skipped=tuple(skipped),
        )
        artifact_id = await self._persist(report)
        await self._progress(
            task_id,
            {
                "task_id": task_id,
                "phase": "report",
                "sources": len(sources),
                "citations": len(citations),
                "unsupported": unsupported,
                "skipped": len(skipped),
                "artifact_id": artifact_id,
            },
        )
        payload = report.to_dict()
        if artifact_id is not None:
            payload["artifact_id"] = artifact_id
        return payload

    async def _persist(self, report: ResearchReport) -> str | None:
        """Store the report as a first-class artifact (§59.10, T115), if configured."""
        if self._artifacts is None:
            return None
        return await self._artifacts.save(report)

    async def _search(
        self, question: str, principal: str, task_id: str
    ) -> list[dict[str, str]]:
        """Ask ``web.search`` for candidates (§10: the LLM never does HTTP).

        A search failure is a typed :class:`UltronError` and is allowed to
        propagate: without results and without seeds there is nothing to reason
        over, and a silent empty report would hide the dependency failure.
        """
        result = await self._executor.run(
            self._search_tool,
            {"query": question, "max_results": self._max_results},
            principal=principal,
            client="agent",
            task_id=task_id,
            agent_id=str(self.agent_id),
        )
        return _result_items(result.output)

    async def _retrieve(
        self, candidates: Sequence[Mapping[str, str]], principal: str, task_id: str
    ) -> tuple[list[Source], list[dict[str, Any]]]:
        """Fetch each candidate and extract it, one source at a time (§59.10).

        A source that cannot be fetched is recorded as skipped, not raised: the
        research continues over what *was* retrieved, and the report shows what
        was attempted. Successful fetches are numbered from 1 in the order they
        are used, which is the numbering the model cites.
        """
        sources: list[Source] = []
        skipped: list[dict[str, Any]] = []
        for candidate in candidates:
            url = candidate["url"]
            try:
                result = await self._executor.run(
                    self._fetch_tool,
                    {"url": url},
                    principal=principal,
                    client="agent",
                    task_id=task_id,
                    agent_id=str(self.agent_id),
                )
            except UltronError as exc:
                skipped.append({"url": url, "error": exc.code.value})
                continue
            source = _source_from_fetch(
                index=len(sources) + 1,
                title=candidate.get("title") or url,
                url=url,
                output=result.output,
                max_chars=self._max_chars_per_source,
            )
            sources.append(source)
        return sources, skipped

    async def _synthesize(self, question: str, sources: Sequence[Source]) -> tuple[str, str | None]:
        """One model call over the collected sources — the reasoning stage."""
        response = await self._router.complete(
            CompletionRequest(
                messages=(
                    ModelMessage(role=MessageRole.SYSTEM, content=self._system_prompt),
                    ModelMessage(
                        role=MessageRole.USER,
                        content=_synthesis_prompt(question, sources),
                    ),
                ),
                model="",
            ),
            capability=self._capability,
        )
        return response.content, response.model or None

    def _candidates(
        self, seeds: Sequence[str], searched: Sequence[Mapping[str, str]]
    ) -> list[dict[str, str]]:
        """Seeds first (explicit intent), then search hits, de-duplicated by URL."""
        ordered: list[dict[str, str]] = []
        seen: set[str] = set()
        for url in seeds:
            if url not in seen:
                ordered.append({"title": url, "url": url})
                seen.add(url)
        for item in searched:
            url = item["url"]
            if url not in seen:
                ordered.append(dict(item))
                seen.add(url)
        return ordered

    async def _progress(self, task_id: str, payload: Mapping[str, Any]) -> None:
        """Publish one ephemeral ``TASK_PROGRESS`` (no bus: nowhere to publish)."""
        if self._bus is None:
            return
        await self._bus.publish(
            EventType.TASK_PROGRESS,
            payload,
            task_id=task_id,
            agent_id=str(self.agent_id),
            persist=False,
        )


# --------------------------------------------------------------------------- #
# Output coercion (the web tools return dataclasses or mappings)
# --------------------------------------------------------------------------- #
def _result_items(output: Any) -> list[dict[str, str]]:
    """Normalise a search tool's output to ``{title, url, snippet}`` dicts.

    ``web.search`` returns a :class:`~app.tools.web.search_provider.SearchResults`
    (``.results``); a fake or a mapping-shaped provider returns ``{"results": …}``.
    Both are accepted so the agent depends on the result *shape*, not the class.
    """
    results = getattr(output, "results", None)
    if results is None and isinstance(output, Mapping):
        results = output.get("results")
    if not isinstance(results, (list, tuple)):
        return []
    items: list[dict[str, str]] = []
    for result in results:
        if isinstance(result, Mapping):
            title, url, snippet = result.get("title"), result.get("url"), result.get("snippet")
        else:
            title = getattr(result, "title", None)
            url = getattr(result, "url", None)
            snippet = getattr(result, "snippet", None)
        if url:
            items.append(
                {
                    "title": str(title or url),
                    "url": str(url),
                    "snippet": str(snippet or ""),
                }
            )
    return items


def _source_from_fetch(
    *, index: int, title: str, url: str, output: Any, max_chars: int
) -> Source:
    """Turn a ``web.fetch`` result into a bounded, sourced extraction."""
    if isinstance(output, Mapping):
        final_url = output.get("final_url") or url
        status = output.get("status_code")
        text = output.get("text")
        truncated = bool(output.get("truncated"))
    else:
        final_url = getattr(output, "final_url", None) or url
        status = getattr(output, "status_code", None)
        text = getattr(output, "text", None)
        truncated = bool(getattr(output, "truncated", False))
    body = text if isinstance(text, str) else ""
    if len(body) > max_chars:
        body = body[:max_chars]
        truncated = True
    return Source(
        index=index,
        title=title,
        url=url,
        final_url=str(final_url),
        fetched_at=datetime.now(UTC),
        status_code=status if isinstance(status, int) else None,
        text=body,
        truncated=truncated,
    )


def _parse_citations(answer: str, sources: Sequence[Source]) -> tuple[tuple[int, ...], int]:
    """The source indices the answer cited (first-mention order) and the marker count.

    A reference to a source that was not collected is ignored — the model cannot
    cite a document that does not exist (§59.10: no citation without a source).
    """
    valid = {source.index for source in sources}
    citations: list[int] = []
    for match in _CITATION.finditer(answer):
        index = int(match.group(1))
        if index in valid and index not in citations:
            citations.append(index)
    return tuple(citations), answer.lower().count(_UNSUPPORTED)


def _synthesis_prompt(question: str, sources: Sequence[Source]) -> str:
    """The user turn: the question, then each source numbered and bounded."""
    if not sources:
        return (
            f"Question: {question}\n\n"
            "No sources could be retrieved for this question. Say so plainly and "
            "do not state any facts."
        )
    blocks = [f"Question: {question}", "", "Sources:"]
    for source in sources:
        blocks.append(f"[{source.index}] {source.title} — {source.final_url}")
        blocks.append(f"fetched {source.fetched_at.isoformat()}")
        blocks.append(source.text or "(no extractable text)")
        blocks.append("")
    return "\n".join(blocks)


# --------------------------------------------------------------------------- #
# Context helpers (stateless, mirroring the sibling agents' split)
# --------------------------------------------------------------------------- #
def _require_question(context: Mapping[str, Any]) -> str:
    for key in ("question", "prompt"):
        value = context.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise InvalidInputError(
        "a research task needs a question",
        details={"keys": sorted(str(key) for key in context)},
    )


def _optional_text(context: Mapping[str, Any], key: str) -> str | None:
    value = context.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _task_id(context: Mapping[str, Any]) -> str | None:
    value = context.get("task_id")
    return str(value) if value else None


def _own_task_id(agent: Agent) -> str:
    """The agent's task id, then its agent id — the same fallback every agent uses."""
    return str(agent.task_id) if agent.task_id is not None else str(agent.agent_id)


def _urls(context: Mapping[str, Any]) -> tuple[str, ...]:
    value = context.get("urls")
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise InvalidInputError(
            "a research task's urls must be a sequence of URLs",
            details={"urls": str(value)[:200]},
        )
    return tuple(str(item).strip() for item in value if str(item).strip())


def _max_sources(context: Mapping[str, Any]) -> int | None:
    value = context.get("max_sources")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise InvalidInputError(
            "a research task's max_sources must be a positive whole number",
            details={"max_sources": str(value)[:200]},
        )
    return value


def _positive_int(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive whole number")
    return value
