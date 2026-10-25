"""Structured output for the health-information agent (§59.12).

§59.12's output contract requires:
- The question/health query
- The information provided (structured by category)
- Safety boundaries: what was refused and why
- Professional direction when appropriate
- Uncertainty framing

The report tracks both what was answered and what was refused,
so the boundary holds regardless of model behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

__all__ = ["HealthReport", "OutputFrame", "SafetyCategory"]


class SafetyCategory:
    """Categories of high-stakes content that must be refused."""

    DIAGNOSIS = "diagnosis"
    TREATMENT = "treatment"
    PRESCRIPTION = "prescription"
    PROGNOSIS = "prognosis"
    EMERGENCY = "emergency"
    SELF_HARM = "self_harm"


@dataclass(frozen=True, slots=True)
class OutputFrame:
    """One piece of information with its safety classification."""

    category: str  # the topic: "symptom", "medication", "terminology", etc.
    content: str
    uncertainty: str  # explicit uncertainty statement
    informational_only: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "content": self.content,
            "uncertainty": self.uncertainty,
            "informational_only": self.informational_only,
        }


@dataclass(frozen=True, slots=True)
class HealthReport:
    """A health-information result (§59.12)."""

    agent: str
    task_id: str | None
    question: str
    frames: tuple[OutputFrame, ...]
    refused: tuple[tuple[str, str], ...]  # (category, reason) pairs
    professional_direction: str | None
    uncertainty_summary: str
    model: str | None
    generated_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "task_id": self.task_id,
            "question": self.question,
            "frames": [f.to_dict() for f in self.frames],
            "refused": list(self.refused),
            "professional_direction": self.professional_direction,
            "uncertainty_summary": self.uncertainty_summary,
            "model": self.model,
            "generated_at": self.generated_at.isoformat(),
        }
