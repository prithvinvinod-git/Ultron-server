"""Structured provenance for the research agent (T114, §10/§59.10).

§10 lists what a research result is — search, retrieve, extract, compare,
summarise, cite, preserve URLs, create an artifact — and §59.10 fixes the shape
that keeps it honest:

- every source is recorded with its URL and fetch time;
- extraction happens per source, **before** any reasoning;
- every factual claim in the answer is bound to a collected source;
- a claim no collected source supports is omitted or marked unsupported, never
  presented as established;
- model-generated text is not a source — the agent never cites itself.

This module is that record: the *artifact* the agent produces, kept separate
from the agent so it can be persisted (T115), serialised and reviewed without
the agent that made it. :class:`Source` is one collected document;
:class:`ResearchReport` is the whole result — the question, the sources, the
synthesised answer, the citation indices the answer actually referenced, and a
count of the explicit ``[unsupported]`` markers the answer carries.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

__all__ = ["ResearchReport", "Source"]


@dataclass(frozen=True, slots=True)
class Source:
    """One collected document: where it came from and when it was fetched.

    ``index`` is the number the synthesis prompt showed the model (and the
    number the answer cites back), so it is stable and dense: failed fetches are
    not numbered, they are recorded separately by the report. ``url`` is what was
    requested; ``final_url`` is where the fetch actually landed (redirects), which
    is the URL a reader should be given. ``text`` is the per-source extraction the
    model reasoned over — deliberately not stored in the serialised report, which
    keeps only what a reviewer needs to re-find and re-fetch it.
    """

    index: int
    title: str
    url: str
    final_url: str
    fetched_at: datetime
    status_code: int | None
    text: str
    truncated: bool = False

    def citation(self) -> dict[str, Any]:
        """The minimum a reader needs to find the source again (§59.10)."""
        return {"index": self.index, "title": self.title, "url": self.final_url or self.url}

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "title": self.title,
            "url": self.url,
            "final_url": self.final_url,
            "fetched_at": self.fetched_at.isoformat(),
            "status_code": self.status_code,
            "chars": len(self.text),
            "truncated": self.truncated,
        }


@dataclass(frozen=True, slots=True)
class ResearchReport:
    """A cited research result: sources in, citations out (§59.10).

    ``sources`` are the collected documents (successful fetches); ``skipped``
    records the URLs that could not be retrieved, so the attempt is visible even
    though it produced no evidence. ``citations`` are the source indices the
    answer referenced, in first-mention order; ``unsupported`` is how many
    ``[unsupported]`` markers the answer carries.
    """

    agent: str
    task_id: str | None
    question: str
    answer: str
    sources: tuple[Source, ...]
    citations: tuple[int, ...]
    unsupported: int
    model: str | None
    generated_at: datetime
    skipped: tuple[dict[str, Any], ...] = ()

    def cited_sources(self) -> tuple[Source, ...]:
        """The sources the answer actually cited, in source order."""
        wanted = set(self.citations)
        return tuple(source for source in self.sources if source.index in wanted)

    def to_dict(self) -> dict[str, Any]:
        """The serialisable artifact (§59.10): what was retrieved vs concluded."""
        return {
            "agent": self.agent,
            "task_id": self.task_id,
            "question": self.question,
            "answer": self.answer,
            "model": self.model,
            "generated_at": self.generated_at.isoformat(),
            "sources": [source.to_dict() for source in self.sources],
            "citations": [source.citation() for source in self.cited_sources()],
            "unsupported": self.unsupported,
            "skipped": list(self.skipped),
        }
