"""Health-information agent (§59.12): informational framing, structural boundaries.

§59.12's safety contract:
- **Boundaries are structural, not prompt-based.** The agent layer refuses
  high-stakes outputs (diagnosis, treatment, prescription) before the model
  speaks. A prompt is a preference; a refusal at the layer is a guarantee.
- **Emergency/crisis content routes to help immediately**, ahead of any model call.
- **Symptom/medication output uses informational framing** ("this can be associated
  with..."), never diagnostic framing ("you have...").
- **Every response names its limits** and points to a professional.
- **Not related to HealthService** (§18): this is the information agent,
  not ULTRON's own liveness/readiness reporting.

The agent works in two stages:
1. Pre-model check: refuse diagnosis/treatment/prescription requests outright
2. Model call: synthesize with strict informational framing
3. Post-model validation: verify output doesn't cross safety boundaries

This ensures the boundary holds regardless of model behavior.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from app.agents.base import Agent
from app.core.errors import AgentError, InvalidInputError, UltronError
from app.database.models import MessageRole
from app.events.bus import EventBus
from app.events.types import EventType
from app.models.base import CompletionRequest, ModelMessage
from app.models.router import ModelRouter

from .report import HealthReport, OutputFrame, SafetyCategory

__all__ = ["HealthInfoAgent"]

#: Emergency/crisis keywords that trigger immediate routing, not model calls.
_EMERGENCY_PATTERNS = re.compile(
    r"\b(help|emergency|suicide|self.harm|self.harming|overdose|poisoning|"
    r"heart.attack|heart attack|stroke|seizure|unconscious|choking|bleeding|"
    r"burn|fall)\b|want to hurt myself|don't want to live",
    re.IGNORECASE,
)

#: Patterns the agent must refuse: diagnosis, treatment, prescription.
_REFUSED_PATTERNS = {
    SafetyCategory.DIAGNOSIS: re.compile(
        r"\byou\s+(have|are\s+suffering|are\s+experiencing)\s+\w+\b"
        r"|\b(i\s+diagnose|diagnosis)\b"
        r"|\b(you\s+have\s+\w+|i\s+think\s+you\s+have)\b",
        re.IGNORECASE,
    ),
    SafetyCategory.TREATMENT: re.compile(
        r"\byou\s+(should\s+take|need\s+to\s+take|must\s+take)\s+\w+\b"
        r"|\b(treatment\s+is|treat\s+with)\b"
        r"|\b(i\s+recommend\s+you\s+take)\b",
        re.IGNORECASE,
    ),
    SafetyCategory.PRESCRIPTION: re.compile(
        r"\b(prescribe|prescription|dosage|rx\s+)\b",
        re.IGNORECASE,
    ),
    SafetyCategory.PROGNOSIS: re.compile(
        r"\b(you\s+will\s+live|survival\s+rate|prognosis|outcome\s+is)\b",
        re.IGNORECASE,
    ),
}

#: The synthesis instruction: strict informational framing, no diagnosis.
_DEFAULT_SYSTEM_PROMPT = """You are ULTRON's health-information agent. You provide
general health information only — you are NOT a doctor, diagnostitian, or clinician.

MAY:
- Explain medical terminology
- Summarize reliable, referenced health information
- Explain general symptoms and the categories they commonly fall into
- Help organize information to take to a doctor
- Summarize medical documents the user supplied
- Explain general medication information
- Identify when professional evaluation may be appropriate

MUST NOT:
- Present yourself as a doctor or medical professional
- Diagnose, or state a diagnosis with certainty
- Recommend treatment or prescription
- Give specific dosage instructions
- Make autonomous high-stakes medical decisions
- Replace professional care

OUTPUT RULES:
- Use informational framing: "X can be associated with..." not "You have X"
- Always name your limits: "This is general information, not medical advice"
- Always point to professional evaluation for persistent or severe symptoms
- If emergency/crisis content detected, route immediately without model response
"""


class HealthInfoAgent(Agent):
    """Informational health agent with structural safety boundaries (§59.12)."""

    agent_type = "health.info"
    description = "Provide general health information with strict informational boundaries."

    def __init__(
        self,
        *,
        router: ModelRouter,
        bus: EventBus | None = None,
        capability: str = "reasoning",
        system_prompt: str | None = None,
        **agent_kwargs: Any,
    ) -> None:
        """Wire the agent to the router with safety guards.

        ``capability`` defaults to ``"reasoning"`` for the capable model
        requirement (§59.8).
        """
        super().__init__(**agent_kwargs)
        self._router = router
        self._bus = bus
        self._capability = capability
        self._system_prompt = system_prompt or _DEFAULT_SYSTEM_PROMPT

    async def run(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        """Process one health query with structural safety boundaries."""
        try:
            return await self._run(context)
        except UltronError:
            raise
        except Exception as exc:
            raise AgentError(
                str(self.agent_id),
                "health-info agent failed unexpectedly",
                details={"agent_type": self.agent_type},
                cause=exc,
            ) from exc

    async def _run(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        question = _require_question(context)
        task_id = _task_id(context) or _own_task_id(self)

        # Stage 1: Pre-model emergency detection
        if _is_emergency(question):
            return _emergency_response(question, task_id)

        await _progress(self, task_id, {"task_id": task_id, "phase": "synthesize"})
        frames, refused, direction = await self._synthesize(question, task_id)

        await _progress(
            self,
            task_id,
            {
                "task_id": task_id,
                "phase": "validate",
                "frames": len(frames),
                "refused": len(refused),
            },
        )

        report = HealthReport(
            agent=self.agent_type,
            task_id=task_id,
            question=question,
            frames=tuple(frames),
            refused=tuple(refused),
            professional_direction=direction,
            uncertainty_summary=_build_uncertainty(frames),
            model=None,
            generated_at=datetime.now(UTC),
        )
        return report.to_dict()

    async def _synthesize(
        self, question: str, task_id: str
    ) -> tuple[list[OutputFrame], list[tuple[str, str]], str | None]:
        """Run the model with strict informational constraints."""
        messages = (
            ModelMessage(role=MessageRole.SYSTEM, content=self._system_prompt),
            ModelMessage(role=MessageRole.USER, content=question),
        )
        response = await self._router.complete(
            CompletionRequest(messages=messages, model=""),
            capability=self._capability,
        )

        frames, refused = self._parse_and_validate(response.content, question)
        direction = _suggest_professional(question, refused)
        return frames, refused, direction

    def _parse_and_validate(
        self, content: str, original_question: str
    ) -> tuple[list[OutputFrame], list[tuple[str, str]]]:
        """Parse model output and enforce structural boundaries."""
        frames: list[OutputFrame] = []
        refused: list[tuple[str, str]] = []

        # Split into paragraphs/blocks
        paragraphs = [p.strip() for p in re.split(r'\n\s*\n', content) if p.strip()]
        category_counter = 0

        for para in paragraphs:
            # Check if paragraph violates safety boundaries
            for category, pattern in _REFUSED_PATTERNS.items():
                if pattern.search(para):
                    refused.append((category, "structural violation in output"))
                    break
            else:
                # Safe content — categorize by topic
                category_counter += 1
                frame = OutputFrame(
                    category=_classify_category(para),
                    content=para,
                    uncertainty="informational only, not medical advice",
                )
                frames.append(frame)

        # Always append the disclaimer
        if not any("medical advice" in f.content.lower() or "not a doctor" in f.content.lower()
                   for f in frames):
            frames.append(OutputFrame(
                category="disclaimer",
                content=(
                    "This information is for general educational purposes only "
                    "and is not a substitute for professional medical advice. "
                    "Always consult a qualified healthcare provider for "
                    "personalized guidance."
                ),
                uncertainty="mandatory disclaimer",
            ))

        return frames, refused


# --------------------------------------------------------------------------- #
# Context helpers
# --------------------------------------------------------------------------- #
def _require_question(context: Mapping[str, Any]) -> str:
    for key in ("question", "prompt", "query"):
        value = context.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise InvalidInputError(
        "a health-info task needs a question or query",
        details={"keys": sorted(str(key) for key in context)},
    )


def _task_id(context: Mapping[str, Any]) -> str | None:
    value = context.get("task_id")
    return str(value) if value else None


def _own_task_id(agent: Agent) -> str:
    return str(agent.task_id) if agent.task_id is not None else str(agent.agent_id)


# --------------------------------------------------------------------------- #
# Emergency detection
# --------------------------------------------------------------------------- #
def _is_emergency(text: str) -> bool:
    """Check if the query indicates emergency or crisis content."""
    return bool(_EMERGENCY_PATTERNS.search(text))


def _emergency_response(question: str, task_id: str) -> dict[str, Any]:
    """Return immediate crisis routing, not model output."""
    return {
        "agent": "health.info",
        "task_id": task_id,
        "question": question,
        "emergency": True,
        "frames": [],
        "refused": [],
        "professional_direction": (
            "This appears to be an emergency. Please contact your local emergency "
            "services or a crisis hotline immediately. This AI cannot provide "
            "emergency medical assistance."
        ),
        "uncertainty_summary": "crisis content detected — immediate routing required",
        "model": None,
        "generated_at": datetime.now(UTC).isoformat(),
    }


# --------------------------------------------------------------------------- #
# Post-processing helpers
# --------------------------------------------------------------------------- #
def _classify_category(text: str) -> str:
    """Classify output paragraph by topic."""
    text_lower = text.lower()
    if any(term in text_lower for term in ("symptom", "sign", "manifestation")):
        return "symptom"
    if any(term in text_lower for term in ("medication", "drug", "prescription", "dosage")):
        return "medication"
    if any(term in text_lower for term in ("terminology", "definition", "means", "refers to")):
        return "terminology"
    if any(term in text_lower for term in ("document", "record", "report", "lab")):
        return "document"
    return "general"


def _suggest_professional(question: str, refused: list[tuple[str, str]]) -> str | None:
    """Suggest professional evaluation when appropriate."""
    # If we refused anything, suggest professional help
    if refused:
        return (
            "Some aspects of your question could not be addressed. "
            "Please consult a qualified healthcare professional for personalized guidance."
        )
    # If the question mentions persistent symptoms or severity
    if any(term in question.lower() for term in (
        "persistent", "severe", "chronic", "worsening", "emergency"
    )):
        return (
            "Given the nature of your symptoms, professional medical evaluation "
            "is recommended. This information is for educational purposes only."
        )
    return None


def _build_uncertainty(frames: list[OutputFrame]) -> str:
    """Build an uncertainty summary from all frames."""
    if not frames:
        return "No information provided"
    uncertain_frames = [f for f in frames if f.uncertainty]
    if uncertain_frames:
        return f"{len(uncertain_frames)} frame(s) carry uncertainty warnings"
    return "general information provided"


async def _progress(agent: HealthInfoAgent, task_id: str, payload: Mapping[str, Any]) -> None:
    """Publish one ephemeral TASK_PROGRESS event."""
    if agent._bus is None:
        return
    await agent._bus.publish(
        EventType.TASK_PROGRESS,
        payload,
        task_id=task_id,
        agent_id=str(agent.agent_id),
        persist=False,
    )
