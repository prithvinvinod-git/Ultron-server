"""Unit tests for the task status/priority state machine (T038).

`TaskStatus` fixes the vocabulary and defers the transitions here, so these
tests pin the *rules* the module docstring claims: one entrance to RUNNING,
cancellation from everywhere live, COMPLETED/CANCELLED as absolute
endpoints, FAILED as the only retryable terminal, resume and restart both
re-entering the queue, no self-transitions, and a priority that is
historical record the moment the attempt finishes.
"""

from __future__ import annotations

from collections import deque

import pytest

from app.core.errors import ConflictError, ErrorCode
from app.database.models import TaskPriority, TaskStatus
from app.tasks.state import (
    LEGAL_TRANSITIONS,
    LIVE_STATUSES,
    TERMINAL_STATUSES,
    can_change_priority,
    can_transition,
    ensure_legal_priority,
    ensure_legal_transition,
    is_terminal,
)

pytestmark = pytest.mark.unit

ALL_STATUSES = list(TaskStatus)
LIVE = list(LIVE_STATUSES)
TERMINAL = list(TERMINAL_STATUSES)


# --------------------------------------------------------------------------- #
# The table itself
# --------------------------------------------------------------------------- #
def test_the_table_covers_every_status() -> None:
    assert set(LEGAL_TRANSITIONS) == set(TaskStatus)


def test_every_target_is_a_real_status() -> None:
    for current, targets in LEGAL_TRANSITIONS.items():
        assert targets <= set(TaskStatus), current


def test_live_and_terminal_partition_the_vocabulary() -> None:
    assert set(TaskStatus) == LIVE_STATUSES | TERMINAL_STATUSES
    assert set() == LIVE_STATUSES & TERMINAL_STATUSES


def test_no_status_transitions_to_itself() -> None:
    for status in ALL_STATUSES:
        assert not can_transition(status, status), status


def test_the_table_is_irreflexive_even_when_enforced() -> None:
    for status in ALL_STATUSES:
        with pytest.raises(ConflictError) as excinfo:
            ensure_legal_transition(status, status)
        assert excinfo.value.http_status == 409


# --------------------------------------------------------------------------- #
# Rule 1 — one entrance to RUNNING
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("status", [s for s in ALL_STATUSES if s is not TaskStatus.QUEUED])
def test_only_the_queue_may_start_a_task(status: TaskStatus) -> None:
    assert not can_transition(status, TaskStatus.RUNNING)
    with pytest.raises(ConflictError):
        ensure_legal_transition(status, TaskStatus.RUNNING)


def test_the_queue_starts_a_task() -> None:
    assert can_transition(TaskStatus.QUEUED, TaskStatus.RUNNING)
    assert ensure_legal_transition(TaskStatus.QUEUED, TaskStatus.RUNNING) is TaskStatus.RUNNING


# --------------------------------------------------------------------------- #
# Rule 2 — cancellation everywhere live; COMPLETED/CANCELLED absolute
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("status", LIVE)
def test_every_live_state_can_be_cancelled(status: TaskStatus) -> None:
    assert can_transition(status, TaskStatus.CANCELLED)
    assert ensure_legal_transition(status, TaskStatus.CANCELLED) is TaskStatus.CANCELLED


@pytest.mark.parametrize("status", [TaskStatus.COMPLETED, TaskStatus.CANCELLED])
def test_finished_states_never_move(status: TaskStatus) -> None:
    assert LEGAL_TRANSITIONS[status] == frozenset()
    for target in ALL_STATUSES:
        with pytest.raises(ConflictError):
            ensure_legal_transition(status, target)


# --------------------------------------------------------------------------- #
# Rule 4 — FAILED is finished but retryable
# --------------------------------------------------------------------------- #
def test_failed_moves_only_to_pending_or_queued() -> None:
    assert LEGAL_TRANSITIONS[TaskStatus.FAILED] == {
        TaskStatus.PENDING,
        TaskStatus.QUEUED,
    }


@pytest.mark.parametrize("target", [TaskStatus.PENDING, TaskStatus.QUEUED])
def test_a_failed_task_may_be_retried(target: TaskStatus) -> None:
    assert ensure_legal_transition(TaskStatus.FAILED, target) is target


@pytest.mark.parametrize(
    "target",
    [
        TaskStatus.RUNNING,
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.PAUSED,
        TaskStatus.BLOCKED,
    ],
)
def test_a_failed_task_cannot_jump_the_lifecycle(target: TaskStatus) -> None:
    assert not can_transition(TaskStatus.FAILED, target)


# --------------------------------------------------------------------------- #
# Rule 3 — pause/resume goes through the queue
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "status",
    [TaskStatus.PENDING, TaskStatus.QUEUED, TaskStatus.RUNNING],
)
def test_pausable_statuses_enter_paused(status: TaskStatus) -> None:
    assert can_transition(status, TaskStatus.PAUSED)


def test_resume_requeues_rather_than_restarting_mid_flight() -> None:
    assert can_transition(TaskStatus.PAUSED, TaskStatus.QUEUED)
    assert not can_transition(TaskStatus.PAUSED, TaskStatus.RUNNING)
    assert not can_transition(TaskStatus.PAUSED, TaskStatus.PENDING)


def test_blocked_and_terminal_states_cannot_be_paused() -> None:
    assert not can_transition(TaskStatus.BLOCKED, TaskStatus.PAUSED)
    assert not can_transition(TaskStatus.COMPLETED, TaskStatus.PAUSED)


# --------------------------------------------------------------------------- #
# Rule 5 — restart survival
# --------------------------------------------------------------------------- #
def test_a_running_task_survives_a_restart_by_requeueing() -> None:
    assert ensure_legal_transition(TaskStatus.RUNNING, TaskStatus.QUEUED) is (TaskStatus.QUEUED)


def test_a_running_task_cannot_rewind_to_pending() -> None:
    assert not can_transition(TaskStatus.RUNNING, TaskStatus.PENDING)


# --------------------------------------------------------------------------- #
# Rule 6 — BLOCKED leaves only through the queue or a terminal
# --------------------------------------------------------------------------- #
def test_a_pending_task_becomes_blocked_on_unsatisfied_dependencies() -> None:
    assert can_transition(TaskStatus.PENDING, TaskStatus.BLOCKED)


def test_blocked_work_must_be_admitted_before_it_runs() -> None:
    assert not can_transition(TaskStatus.BLOCKED, TaskStatus.RUNNING)
    assert can_transition(TaskStatus.BLOCKED, TaskStatus.QUEUED)


@pytest.mark.parametrize("target", [TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.QUEUED])
def test_blocked_exits_are_legal(target: TaskStatus) -> None:
    assert can_transition(TaskStatus.BLOCKED, target)


def test_a_queued_task_may_be_pulled_back_to_blocked() -> None:
    assert can_transition(TaskStatus.QUEUED, TaskStatus.BLOCKED)


# --------------------------------------------------------------------------- #
# Structure — every live state reaches a terminal one
# --------------------------------------------------------------------------- #
def test_every_live_status_can_reach_a_terminal_state() -> None:
    for start in LIVE_STATUSES:
        seen: set[TaskStatus] = set()
        queue: deque[TaskStatus] = deque([start])
        while queue:
            status = queue.popleft()
            if status in seen:
                continue
            seen.add(status)
            queue.extend(LEGAL_TRANSITIONS[status] - seen)
        assert TaskStatus.COMPLETED in seen, start
        assert TaskStatus.CANCELLED in seen, start


# --------------------------------------------------------------------------- #
# Enforcement — the refusal is a 409 that names the way out
# --------------------------------------------------------------------------- #
def test_an_illegal_transition_is_a_409_with_the_legal_targets() -> None:
    with pytest.raises(ConflictError) as excinfo:
        ensure_legal_transition(TaskStatus.PENDING, TaskStatus.RUNNING, task="t1")

    error = excinfo.value
    assert error.code is ErrorCode.CONFLICT
    assert error.http_status == 409
    assert error.details == {
        "current": "pending",
        "target": "running",
        "legal_targets": ["blocked", "cancelled", "failed", "paused", "queued"],
        "task": "t1",
    }


def test_ensure_returns_the_target_it_was_asked_about() -> None:
    result = ensure_legal_transition(TaskStatus.QUEUED, TaskStatus.PAUSED)
    assert result is TaskStatus.PAUSED


# --------------------------------------------------------------------------- #
# Terminality
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("status", TERMINAL)
def test_terminal_statuses_are_terminal(status: TaskStatus) -> None:
    assert is_terminal(status)


@pytest.mark.parametrize("status", LIVE)
def test_live_statuses_are_not_terminal(status: TaskStatus) -> None:
    assert not is_terminal(status)


# --------------------------------------------------------------------------- #
# Priority — legal on live tasks, historical record on terminal ones
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("status", LIVE)
def test_any_live_task_may_be_reprioritised(status: TaskStatus) -> None:
    assert can_change_priority(status)
    assert ensure_legal_priority(status, TaskPriority.URGENT) is TaskPriority.URGENT
    assert ensure_legal_priority(status, TaskPriority.LOW) is TaskPriority.LOW


@pytest.mark.parametrize("status", TERMINAL)
def test_a_terminal_tasks_priority_is_historical_record(status: TaskStatus) -> None:
    assert not can_change_priority(status)
    with pytest.raises(ConflictError) as excinfo:
        ensure_legal_priority(status, TaskPriority.URGENT, task="t2")

    error = excinfo.value
    assert error.code is ErrorCode.CONFLICT
    assert error.http_status == 409
    assert error.details == {
        "current_status": status.value,
        "target_priority": "urgent",
        "task": "t2",
    }
