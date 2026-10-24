"""Structured provenance for the decision-support agent (§59.11).

§59.11's output contract:
- Options: the identified alternatives
- Criteria: the dimensions along which options are compared
- Documented facts: evidence bound to sources (like research)
- Tradeoffs: where options diverge on criteria
- Unknowns: what is not known and affects the decision
- Missing constraints: constraints the user must supply
- Alternatives: presented with costs and reasoning, uncertainty exposed

The agent must NOT make the decision — it presents and asks.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

__all__ = [
    "DecisionReport",
    "Option",
    "Criterion",
    "Tradeoff",
    "Unknown",
    "MissingConstraint",
    "Alternative",
]


@dataclass(frozen=True, slots=True)
class Option:
    """One option being evaluated."""

    id: str
    name: str
    description: str
    metadata: dict[str, Any] = None

    def __post_init__(self):
        if self.metadata is None:
            object.__setattr__(self, "metadata", {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "metadata": self.metadata,
        }


@dataclass(frozen=True, slots=True)
class Criterion:
    """One evaluation dimension."""

    id: str
    name: str
    description: str
    weight: float = 1.0  # relative importance, 1.0 = neutral
    higher_is_better: bool = True  # for scoring

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "weight": self.weight,
            "higher_is_better": self.higher_is_better,
        }


@dataclass(frozen=True, slots=True)
class Tradeoff:
    """A tradeoff between options on a criterion."""

    criterion_id: str
    option_a_id: str
    option_b_id: str
    description: str
    evidence_a: str = ""
    evidence_b: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "criterion_id": self.criterion_id,
            "option_a_id": self.option_a_id,
            "option_b_id": self.option_b_id,
            "description": self.description,
            "evidence_a": self.evidence_a,
            "evidence_b": self.evidence_b,
        }


@dataclass(frozen=True, slots=True)
class Unknown:
    """Something not known that affects the decision."""

    id: str
    description: str
    impact: str  # "high" | "medium" | "low"
    related_options: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "impact": self.impact,
            "related_options": list(self.related_options),
        }


@dataclass(frozen=True, slots=True)
class MissingConstraint:
    """A constraint the user must supply before the decision can be finalized."""

    id: str
    description: str
    why_needed: str
    related_options: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "why_needed": self.why_needed,
            "related_options": list(self.related_options),
        }


@dataclass(frozen=True, slots=True)
class Alternative:
    """An alternative presented with its reasoning and uncertainty."""

    option_id: str
    reasoning: str
    uncertainty: str
    estimated_cost: str | None = None
    estimated_time: str | None = None
    confidence: float = 0.5  # 0.0 to 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "option_id": self.option_id,
            "reasoning": self.reasoning,
            "uncertainty": self.uncertainty,
            "estimated_cost": self.estimated_cost,
            "estimated_time": self.estimated_time,
            "confidence": self.confidence,
        }


@dataclass(frozen=True, slots=True)
class DecisionReport:
    """A structured decision-support result (§59.11)."""

    agent: str
    task_id: str | None
    question: str
    options: tuple[Option, ...]
    criteria: tuple[Criterion, ...]
    tradeoffs: tuple[Tradeoff, ...]
    unknowns: tuple[Unknown, ...]
    missing_constraints: tuple[MissingConstraint, ...]
    alternatives: tuple[Alternative, ...]
    model: str | None
    generated_at: datetime
    recommendations: tuple[str, ...] = ()  # explicitly labelled recommendations

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "task_id": self.task_id,
            "question": self.question,
            "options": [o.to_dict() for o in self.options],
            "criteria": [c.to_dict() for c in self.criteria],
            "tradeoffs": [t.to_dict() for t in self.tradeoffs],
            "unknowns": [u.to_dict() for u in self.unknowns],
            "missing_constraints": [m.to_dict() for m in self.missing_constraints],
            "alternatives": [a.to_dict() for a in self.alternatives],
            "model": self.model,
            "generated_at": self.generated_at.isoformat(),
            "recommendations": list(self.recommendations),
        }