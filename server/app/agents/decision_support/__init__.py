"""Decision-support agent (§59.11) — structured analysis without making the decision."""

from __future__ import annotations

from .agent import DecisionSupportAgent
from .report import DecisionReport, Option, Criterion, Tradeoff, Unknown, MissingConstraint, Alternative

__all__ = [
    "DecisionSupportAgent",
    "DecisionReport",
    "Option",
    "Criterion",
    "Tradeoff",
    "Unknown",
    "MissingConstraint",
    "Alternative",
]