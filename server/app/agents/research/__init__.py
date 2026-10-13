"""Research agent: source collection, comparison, summarisation and citation."""

from __future__ import annotations

from .agent import ResearchAgent
from .artifacts import ResearchArtifactStore
from .report import ResearchReport, Source

__all__ = ["ResearchAgent", "ResearchArtifactStore", "ResearchReport", "Source"]
