"""Persistent workflows (T172, §44 "persistent workflows").

A **workflow** is a named, ordered description of work: a goal plus the steps
that reach it. §44 asks for persistent workflows and the ``schedules`` table is
where they persist — a schedule's ``payload`` is free-form JSON, so a workflow
lives there as ``payload["workflow"]`` and survives a restart exactly like any
other schedule row (§44: "Persist important scheduled jobs").

**Why a value type and not a new table.** A workflow is *data a schedule
carries*, not a second aggregate with its own life: it has no id of its own, no
status a client polls, and no relationships. Giving it a table would mean a
second thing to migrate, join and keep in sync with the schedule that owns it,
for no capability §44 asks for. If a future phase needs workflows to outlive the
schedules that trigger them, this is the seam to split — :meth:`Workflow.to_payload`
is already the whole serialised form.

**Parsing is strict and total.** :meth:`Workflow.from_payload` accepts either
the full ``{"workflow": {...}}`` form or the shorthand ``{"goal": ...,
"steps": [...]}`` that a plain schedule uses, and refuses anything it cannot
read as a workflow with an :class:`~app.core.errors.InvalidInputError` naming the
field. Total because the poll loop reads payloads it did not write: a schedule
created before a schema change must surface as one clear 422, never as an
``AttributeError`` three frames deep in a worker at 3 a.m.

**Steps mirror §18's step record.** A :class:`WorkflowStep` becomes a
``TaskStep`` row (§18's ``steps`` field) when a workflow is planned, so the
graph executor (T041) runs it with no new code path.

**Retries and timeouts ride the existing policy.** A background agent or job
that must not loop forever is bounded by the *task's* `max_retries` (§66.11) and
by the scheduler's `max_instances`/`misfire_grace`; a workflow does not invent a
second bound (§66.11 and §44's amendment both say the scheduler and the task DAG
are the only machinery).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.errors import InvalidInputError
from app.database.models import TaskPriority

__all__ = ["Workflow", "WorkflowStep"]

#: The priority vocabulary, as values, for validating a serialised workflow.
_PRIORITIES: frozenset[str] = frozenset(member.value for member in TaskPriority)


def _require_object(value: object, *, field: str) -> dict[str, Any]:
    """Return ``value`` as a JSON object, or refuse it."""
    if not isinstance(value, dict):
        raise InvalidInputError(
            f"{field} must be a JSON object",
            details={"field": field, "type": type(value).__name__},
        )
    return dict(value)


def _optional_object(value: object, *, field: str) -> dict[str, Any]:
    """Return ``value`` as a JSON object, treating absent as empty."""
    if value is None:
        return {}
    return _require_object(value, field=field)


def _require_text(value: object, *, field: str, limit: int = 100) -> str:
    """Return ``value`` as non-empty text, or refuse it."""
    if not isinstance(value, str) or not value.strip():
        raise InvalidInputError(
            f"{field} must be non-empty text",
            details={field: str(value)[:limit]},
        )
    return value


@dataclass(frozen=True, slots=True)
class WorkflowStep:
    """One step of a workflow; becomes a ``TaskStep`` when the workflow runs."""

    name: str
    description: str | None = None
    input: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text(self.name, field="a workflow step name")
        if self.description is not None and not isinstance(self.description, str):
            raise InvalidInputError(
                "a step description must be text",
                details={"description": type(self.description).__name__},
            )
        object.__setattr__(self, "input", dict(self.input or {}))

    def to_payload(self) -> dict[str, Any]:
        """Return the step as plain JSON."""
        payload: dict[str, Any] = {"name": self.name}
        if self.description is not None:
            payload["description"] = self.description
        if self.input:
            payload["input"] = dict(self.input)
        return payload

    @classmethod
    def from_payload(cls, value: object) -> WorkflowStep:
        """Parse one step from JSON, refusing a malformed one."""
        data = _require_object(value, field="a workflow step")
        name = data.get("name")
        if not isinstance(name, str) or not name.strip():
            raise InvalidInputError(
                "a workflow step needs a non-empty name",
                details={"name": str(name)[:100]},
            )
        description = data.get("description")
        if description is not None and not isinstance(description, str):
            raise InvalidInputError(
                "a step description must be text",
                details={"description": type(description).__name__},
            )
        return cls(
            name=name,
            description=description,
            input=_optional_object(data.get("input"), field="a workflow step input"),
        )


@dataclass(frozen=True, slots=True)
class Workflow:
    """A named goal and the steps that reach it, persisted in a schedule."""

    name: str
    goal: str
    steps: tuple[WorkflowStep, ...] = ()
    priority: str = TaskPriority.NORMAL.value

    def __post_init__(self) -> None:
        _require_text(self.name, field="a workflow name")
        _require_text(self.goal, field="a workflow goal")
        if self.priority not in _PRIORITIES:
            raise InvalidInputError(
                "workflow priority is not a known level",
                details={"priority": str(self.priority)[:40]},
            )
        object.__setattr__(self, "steps", tuple(self.steps))

    @property
    def task_priority(self) -> TaskPriority:
        """The workflow's priority as the task enum."""
        return TaskPriority(self.priority)

    def to_payload(self) -> dict[str, Any]:
        """Return the ``payload`` fragment a schedule stores for this workflow."""
        return {
            "workflow": {
                "name": self.name,
                "goal": self.goal,
                "priority": self.priority,
                "steps": [step.to_payload() for step in self.steps],
            }
        }

    @classmethod
    def from_payload(cls, payload: object) -> Workflow:
        """Parse a workflow from a schedule ``payload``.

        Accepts the full ``{"workflow": {...}}`` form, or the shorthand where the
        payload *is* the workflow (``{"name"?, "goal", "priority"?, "steps"?}``).
        """
        data = _require_object(payload, field="a schedule payload")
        nested = data.get("workflow")
        body = _require_object(nested, field="the workflow") if nested is not None else data
        goal = body.get("goal")
        if not isinstance(goal, str) or not goal.strip():
            raise InvalidInputError(
                "a workflow needs a non-empty goal",
                details={"goal": str(goal)[:100]},
            )
        name = body.get("name")
        if not isinstance(name, str) or not name.strip():
            name = goal.strip()[:80]
        raw_steps = body.get("steps") or []
        if not isinstance(raw_steps, list):
            raise InvalidInputError(
                "workflow steps must be a list",
                details={"steps": type(raw_steps).__name__},
            )
        priority = body.get("priority", TaskPriority.NORMAL.value)
        if not isinstance(priority, str):
            raise InvalidInputError(
                "workflow priority must be text",
                details={"priority": type(priority).__name__},
            )
        return cls(
            name=name,
            goal=goal,
            steps=tuple(WorkflowStep.from_payload(step) for step in raw_steps),
            priority=priority.lower(),
        )
