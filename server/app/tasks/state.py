"""The task status/priority state machine (T038): what may go where.

`TaskStatus` (enums.py) fixes the vocabulary — PENDING, QUEUED, RUNNING,
BLOCKED, PAUSED, COMPLETED, FAILED, CANCELLED — and its docstring defers
the interesting half here: *"Phase 2's state machine (task T038) is the
authority on legal transitions; this enum only fixes the vocabulary."*
This module is that authority. Like the permission policy
(`app/security/permissions.py`), it is **pure**: a data table plus the
functions over it, no persistence and no events — `TaskManager` (T039)
writes, the graph executor (T041) publishes `TASK_*` (§19), both ask
*here* whether a move is legal first.

The design, and the spec each rule answers:

1. **One entrance to RUNNING**: only `QUEUED → RUNNING`. A task starts
   from the queue or not at all — no edge reaches RUNNING directly from
   PENDING, BLOCKED or PAUSED, so "running" always means a worker
   admitted it through the one door.

2. **Every live state can be cancelled; two states are absolute.**
   `PENDING/QUEUED/RUNNING/BLOCKED/PAUSED → CANCELLED` — §66.11's
   cancellation cannot be refused by lifecycle position. `COMPLETED` and
   `CANCELLED` have **no outgoing edges**: history does not resurrect
   (§66.16 — a finished record that changed afterwards would be a
   falsehood with a timestamp). `FAILED` is terminal in the `is_terminal`
   sense (the attempt is over) yet has exactly two exits — see 4.

3. **Pause/resume is a detour through the queue** (§66.11): PAUSED is
   entered from PENDING, QUEUED or RUNNING, and leaves only to QUEUED or
   CANCELLED. Resume therefore *re-enters* the queue rather than jumping
   back to RUNNING, which keeps rule 1 intact — a resumed task is picked
   up again, not magically mid-flight. `PAUSED → RUNNING` and
   `PAUSED → PENDING` are deliberately illegal.

4. **FAILED may be retried, nothing else may move once finished**
   (§66.11's retry, §66.4's replan): `FAILED → QUEUED` is a straight
   retry; `FAILED → PENDING` is a replan, back to the start of the
   lifecycle. This is the only way out of a terminal state, and the only
   way *into* PENDING from one.

5. **Restart survival has an edge of its own** (§18: "tasks must survive
   server restarts"): `RUNNING → QUEUED` requeues an in-flight task whose
   worker died — through the queue, per rule 1, never back to PENDING
   (the admission already happened) and never straight back to RUNNING
   (nobody is running it). `RUNNING → PENDING` is illegal for the same
   reason `PENDING → RUNNING` is.

6. **BLOCKED is graph vocabulary** (§18's task graphs): a task whose
   dependencies are unsatisfied. It enters from PENDING (or is pulled
   back from QUEUED when admission discovers a gap) and may leave only
   through the queue (`→ QUEUED`, dependencies satisfied) or a terminal
   (`→ FAILED` or `→ CANCELLED` — which of the two when a dependency
   dies is graph *policy* for T041 to choose; both are legal movement).
   `BLOCKED → RUNNING` is illegal: blocked work must be admitted first.

7. **No self-transitions.** `X → X` is never legal: a status write that
   changes nothing is not a transition, and allowing it would make
   "did anything actually happen?" unanswerable in tests and audit logs.

**Priority** is a second, smaller machine over the same statuses: it may
change in **any direction on any live task** — a boost and a demotion are
equally ordinary for a scheduler — but **never on a terminal one**. A
finished task's priority is the record of what was scheduled (§66.16);
reprioritising it would rewrite history rather than affect any queue.

Deliberately absent: `StepStatus` has the same vocabulary-only treatment
but its transitions belong to graph execution (T041/T044), event
publication belongs to the executor (T041), and persistence to the
manager (T039). This module answers exactly one question — *is this move
legal?* — and refuses illegal ones with `ConflictError` (409), the same
"the request conflicts with current state" answer the registry gives.
"""

from __future__ import annotations

from typing import Final

from app.core.errors import ConflictError
from app.database.models import TaskPriority, TaskStatus

__all__ = [
    "LEGAL_TRANSITIONS",
    "LIVE_STATUSES",
    "TERMINAL_STATUSES",
    "can_change_priority",
    "can_transition",
    "ensure_legal_priority",
    "ensure_legal_transition",
    "is_terminal",
]

#: Every legal move, keyed by where the task is now. The authority TaskStatus
#: defers to; read it as "may go to", and see the module docstring for why
#: each set is exactly this size.
LEGAL_TRANSITIONS: Final[dict[TaskStatus, frozenset[TaskStatus]]] = {
    TaskStatus.PENDING: frozenset(
        {
            TaskStatus.QUEUED,
            TaskStatus.BLOCKED,
            TaskStatus.PAUSED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }
    ),
    TaskStatus.QUEUED: frozenset(
        {
            TaskStatus.RUNNING,
            TaskStatus.BLOCKED,
            TaskStatus.PAUSED,
            TaskStatus.CANCELLED,
        }
    ),
    TaskStatus.RUNNING: frozenset(
        {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.PAUSED,
            TaskStatus.QUEUED,  # restart survival (18) / pause-resume (66.11)
        }
    ),
    TaskStatus.BLOCKED: frozenset(
        {
            TaskStatus.QUEUED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }
    ),
    TaskStatus.PAUSED: frozenset({TaskStatus.QUEUED, TaskStatus.CANCELLED}),
    TaskStatus.COMPLETED: frozenset(),
    TaskStatus.FAILED: frozenset({TaskStatus.PENDING, TaskStatus.QUEUED}),
    TaskStatus.CANCELLED: frozenset(),
}

#: Work that could still move: PENDING, QUEUED, RUNNING, BLOCKED, PAUSED —
#: exactly the statuses that are not terminal (test-pinned as a complement).
LIVE_STATUSES: Final[frozenset[TaskStatus]] = frozenset(
    {
        TaskStatus.PENDING,
        TaskStatus.QUEUED,
        TaskStatus.RUNNING,
        TaskStatus.BLOCKED,
        TaskStatus.PAUSED,
    }
)
"""The five statuses that are still in flight. FAILED is excluded even
though it has outgoing edges: its attempt is over, and retry is a *new*
attempt entering the lifecycle, not the old one still running."""

TERMINAL_STATUSES: Final[frozenset[TaskStatus]] = frozenset(
    {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
)
"""Finished, for every caller's `is_finished` test: nothing is running,
nothing will start without a deliberate re-entry (rule 4)."""


def is_terminal(status: TaskStatus) -> bool:
    """Whether the attempt is over — COMPLETED, FAILED or CANCELLED."""
    return status in TERMINAL_STATUSES


def can_transition(current: TaskStatus, target: TaskStatus) -> bool:
    """Whether `current → target` is legal. Pure; never raises for legal
    statuses (the table has an entry for every one)."""
    return target in LEGAL_TRANSITIONS[current]


def ensure_legal_transition(
    current: TaskStatus,
    target: TaskStatus,
    *,
    task: str | None = None,
) -> TaskStatus:
    """Return `target` if the move is legal, else `ConflictError` (409).

    The error names where the task is, where it was asked to go, and every
    place it *may* go — a refusal the caller can act on without re-reading
    this module.
    """
    if not can_transition(current, target):
        details: dict[str, str | list[str]] = {
            "current": current.value,
            "target": target.value,
            "legal_targets": sorted(t.value for t in LEGAL_TRANSITIONS[current]),
        }
        if task is not None:
            details["task"] = task
        raise ConflictError(
            f"task cannot move from {current.value} to {target.value}",
            details=details,
        )
    return target


def can_change_priority(status: TaskStatus) -> bool:
    """Whether a task in `status` may be reprioritised — any live task may,
    in either direction; a terminal task's priority is historical record."""
    return not is_terminal(status)


def ensure_legal_priority(
    status: TaskStatus,
    target: TaskPriority,
    *,
    task: str | None = None,
) -> TaskPriority:
    """Return `target` if the reprioritisation is legal, else 409."""
    if not can_change_priority(status):
        details: dict[str, str] = {
            "current_status": status.value,
            "target_priority": target.value,
        }
        if task is not None:
            details["task"] = task
        raise ConflictError(
            f"cannot reprioritise a {status.value} task: its priority is "
            "the record of what was scheduled",
            details=details,
        )
    return target
