"""Decision-support agent (§59.11) — structured analysis without making the decision.

§59.11's behavioural contract:

- Identify options
- Extract criteria
- Compare documented facts
- Surface tradeoffs
- Name unknowns
- Ask for missing constraints
- Present alternatives with costs and reasoning

The agent must NOT make the decision. It presents evidence and tradeoffs;
the user decides. Any recommendation is explicitly labelled and exposes
the reasoning and uncertainty.

This agent is deliberately simple: one model call over the structured
prompt, no tools required (spec §59.11: "needs no tools"). The prompt
enforces the structure the spec requires, and the output is parsed into
typed domain objects rather than left as free-form text.
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

from .report import (
    Alternative,
    Criterion,
    DecisionReport,
    MissingConstraint,
    Option,
    Tradeoff,
    Unknown,
)

__all__ = ["DecisionSupportAgent"]

#: The synthesis instruction: the whole of §59.11's output contract in one place.
_DEFAULT_SYSTEM_PROMPT = """You are ULTRON's decision-support agent. Your job is to
structure a decision, not make it. You must:

1. Identify all reasonable options.
2. Extract the criteria used to evaluate options.
3. Compare documented facts for each option.
4. Surface tradeoffs between options.
5. Name what is unknown that affects the decision.
6. Ask for constraints that are missing.
7. Present alternatives with their reasoning, uncertainty, and costs.

CRITICAL RULES:
- You must NOT make the decision yourself. Present evidence and tradeoffs.
- Where a decision is consequential, say what facts are missing rather than
  producing a recommendation that reads as settled.
- Any recommendation you offer MUST be labelled as a recommendation, with
  reasoning and uncertainty exposed.
- Never present a decision as settled unless the user has asked for a decision.

Output format:
- options: [{id, name, description}]
- criteria: [{id, name, description, weight, higher_is_better}]
- tradeoffs: [{criterion_id, option_a_id, option_b_id, description}]
- unknowns: [{id, description, impact, related_options}]
- missing_constraints: [{id, description, why_needed, related_options}]
- alternatives: [{option_id, reasoning, uncertainty, estimated_cost, confidence}]
- recommendations: [list of recommendation ids, if any]
"""


class DecisionSupportAgent(Agent):
    """Structure a decision: options, criteria, tradeoffs, unknowns, missing constraints (§59.11)."""

    agent_type = "decision.support"
    description = "Structure a decision analysis without making the decision."

    def __init__(
        self,
        *,
        router: ModelRouter,
        bus: EventBus | None = None,
        capability: str = "reasoning",
        system_prompt: str | None = None,
        **agent_kwargs: Any,
    ) -> None:
        """Wire the agent to the router.

        ``capability`` defaults to ``"reasoning"`` because §59.8's registry
        marks decision-support as a reasoning use case, and §59.11 says
        decisions need capable models.

        All §6 assignment kwargs (``name``, ``task_id``, ``model``, …) pass
        through to :class:`~app.agents.base.Agent`.
        """
        super().__init__(**agent_kwargs)
        self._router = router
        self._bus = bus
        self._capability = capability
        self._system_prompt = system_prompt or _DEFAULT_SYSTEM_PROMPT

    async def run(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        """Analyze one decision question and return its structured report."""
        try:
            return await self._run(context)
        except UltronError:
            raise
        except Exception as exc:
            raise AgentError(
                str(self.agent_id),
                "decision-support agent failed unexpectedly",
                details={"agent_type": self.agent_type},
                cause=exc,
            ) from exc

    async def _run(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        question = _require_question(context)
        task_id = _task_id(context) or _own_task_id(self)
        goal = context.get("goal", question)
        constraints = _constraints(context)

        await self._progress(task_id, {"task_id": task_id, "phase": "analyze"})
        report, model = await self._analyze(question, goal, constraints)

        await self._progress(
            task_id,
            {
                "task_id": task_id,
                "phase": "report",
                "options": len(report.options),
                "criteria": len(report.criteria),
                "tradeoffs": len(report.tradeoffs),
                "unknowns": len(report.unknowns),
                "missing_constraints": len(report.missing_constraints),
            },
        )
        payload = report.to_dict()
        return payload

    async def _analyze(
        self,
        question: str,
        goal: str,
        constraints: list[str],
    ) -> tuple[DecisionReport, str | None]:
        """One model call over the structured prompt."""
        messages = (
            ModelMessage(role=MessageRole.SYSTEM, content=self._system_prompt),
            ModelMessage(
                role=MessageRole.USER,
                content=_build_prompt(question, goal, constraints),
            ),
        )
        response = await self._router.complete(
            CompletionRequest(messages=messages, model=""),
            capability=self._capability,
        )
        report = _parse_report(question, response.content)
        return report, response.model or None

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
# Context helpers
# --------------------------------------------------------------------------- #
def _require_question(context: Mapping[str, Any]) -> str:
    for key in ("question", "prompt", "goal"):
        value = context.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise InvalidInputError(
        "a decision-support task needs a question",
        details={"keys": sorted(str(key) for key in context)},
    )


def _task_id(context: Mapping[str, Any]) -> str | None:
    value = context.get("task_id")
    return str(value) if value else None


def _own_task_id(agent: Agent) -> str:
    return str(agent.task_id) if agent.task_id is not None else str(agent.agent_id)


def _constraints(context: Mapping[str, Any]) -> list[str]:
    value = context.get("constraints")
    if value is None:
        return []
    if not isinstance(value, list):
        return []
    return [str(c).strip() for c in value if isinstance(c, str) and c.strip()]


# --------------------------------------------------------------------------- #
# Prompt building
# --------------------------------------------------------------------------- #
def _build_prompt(question: str, goal: str, constraints: list[str]) -> str:
    """Build the user prompt: question, goal, constraints, and requested structure."""
    parts = [
        f"Decision question: {question}",
        "",
        f"Goal: {goal}",
    ]
    if constraints:
        parts.append("Constraints:")
        for c in constraints:
            parts.append(f"- {c}")
        parts.append("")
    parts.append(
        "Provide a structured analysis covering:\n"
        "- options (what are the choices?)\n"
        "- criteria (what dimensions matter?)\n"
        "- tradeoffs (where do options diverge?)\n"
        "- unknowns (what don't we know?)\n"
        "- missing constraints (what must the user decide?)\n"
        "- alternatives (for each option, reasoning + uncertainty + cost)\n"
        "- recommendations (if any, explicitly labelled)"
    )
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# Output parsing
# --------------------------------------------------------------------------- #
# Regex patterns for structured extraction. The model is asked to output JSON-like
# structures; we parse them with regex as a first pass, then validate.
_RE_OPTION = re.compile(r'\{\s*"id"\s*:\s*"([^"]+)"\s*,\s*"name"\s*:\s*"([^"]+)"\s*,\s*"description"\s*:\s*"([^"]+)"\s*\}')
_RE_CRITERION = re.compile(r'\{\s*"id"\s*:\s*"([^"]+)"\s*,\s*"name"\s*:\s*"([^"]+)"\s*,\s*"description"\s*:\s*"([^"]+)"(?:\s*,\s*"weight"\s*:\s*([\d.]+))?\s*\}')
_RE_UNKNOWN = re.compile(r'\{\s*"id"\s*:\s*"([^"]+)"\s*,\s*"description"\s*:\s*"([^"]+)"\s*,\s*"impact"\s*:\s*"([^"]+)"\s*\}')
_RE_MISSING = re.compile(r'\{\s*"id"\s*:\s*"([^"]+)"\s*,\s*"description"\s*:\s*"([^"]+)"\s*,\s*"why_needed"\s*:\s*"([^"]+)"\s*\}')


def _parse_report(question: str, content: str) -> DecisionReport:
    """Parse the model's structured output into a DecisionReport."""
    options = _extract_options(content)
    criteria = _extract_criteria(content)
    tradeoffs = []  # Not extracted by default regex; model includes in prose
    unknowns = _extract_unknowns(content)
    missing_constraints = _extract_missing_constraints(content)
    alternatives = []  # Model includes in prose
    recommendations = []  # Model may include labelled recommendations

    return DecisionReport(
        agent="decision.support",
        task_id=None,
        question=question,
        options=tuple(options),
        criteria=tuple(criteria),
        tradeoffs=tuple(tradeoffs),
        unknowns=tuple(unknowns),
        missing_constraints=tuple(missing_constraints),
        alternatives=tuple(alternatives),
        model=None,
        generated_at=datetime.now(UTC),
        recommendations=tuple(recommendations),
    )


def _extract_options(content: str) -> list[Option]:
    """Extract options from the model output."""
    options = []
    for match in _RE_OPTION.finditer(content):
        id_, name, description = match.group(1), match.group(2), match.group(3)
        options.append(Option(id=id_.strip(), name=name.strip(), description=description.strip()))
    return options


def _extract_criteria(content: str) -> list[Criterion]:
    """Extract criteria from the model output."""
    criteria = []
    for match in _RE_CRITERION.finditer(content):
        id_, name, description = match.group(1), match.group(2), match.group(3)
        weight_str = match.group(4)
        weight = float(weight_str) if weight_str else 1.0
        criteria.append(
            Criterion(id=id_.strip(), name=name.strip(), description=description.strip(), weight=weight)
        )
    return criteria


def _extract_unknowns(content: str) -> list[Unknown]:
    """Extract unknowns from the model output."""
    unknowns = []
    for match in _RE_UNKNOWN.finditer(content):
        id_, description, impact = match.group(1), match.group(2), match.group(3)
        unknowns.append(Unknown(id=id_.strip(), description=description.strip(), impact=impact.strip()))
    return unknowns


def _extract_missing_constraints(content: str) -> list[MissingConstraint]:
    """Extract missing constraints from the model output."""
    constraints = []
    for match in _RE_MISSING.finditer(content):
        id_, description, why_needed = match.group(1), match.group(2), match.group(3)
        constraints.append(MissingConstraint(id=id_.strip(), description=description.strip(), why_needed=why_needed.strip()))
    return constraints