"""Timing and workflow values for the scheduler (T170/T172, §44).

Pure tests: no database, no bus, no poll loop. A schedule is built in memory and
its next fire time is asked of the trigger engine, which is the one place a cron
expression or a one-shot time can be wrong without any other part of the scheduler
noticing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.core.errors import ConfigError, InvalidInputError, ScheduleError
from app.database.models import Schedule, ScheduleKind, TaskPriority
from app.scheduler.triggers import (
    build_trigger,
    next_run_time,
    resolve_timezone,
    validate_schedule,
)
from app.scheduler.workflows import Workflow, WorkflowStep

pytestmark = pytest.mark.unit

UTC_ZONE = resolve_timezone("UTC")


def _schedule(**fields: object) -> Schedule:
    defaults: dict[str, object] = {"name": "job", "kind": ScheduleKind.INTERVAL}
    defaults.update(fields)
    return Schedule(**defaults)  # type: ignore[arg-type]


class TestTimezone:
    def test_utc_is_accepted_as_an_alias(self) -> None:
        assert str(resolve_timezone("UTC")) == "UTC"
        assert str(resolve_timezone("utc")) == "UTC"

    def test_a_named_zone_resolves(self) -> None:
        assert str(resolve_timezone("Europe/London")) == "Europe/London"

    def test_an_unknown_zone_is_a_config_error(self) -> None:
        with pytest.raises(ConfigError):
            resolve_timezone("Mars/Olympus_Mons")


class TestBuildTrigger:
    def test_an_event_schedule_has_no_trigger(self) -> None:
        schedule = _schedule(kind=ScheduleKind.EVENT, payload={"event": "TASK_FAILED"})
        assert build_trigger(schedule, timezone=UTC_ZONE) is None

    def test_an_interval_schedule_builds_an_interval_trigger(self) -> None:
        trigger = build_trigger(_schedule(interval_seconds=30), timezone=UTC_ZONE)
        assert isinstance(trigger, IntervalTrigger)

    def test_a_cron_schedule_builds_a_cron_trigger(self) -> None:
        trigger = build_trigger(
            _schedule(kind=ScheduleKind.CRON, cron_expression="0 8 * * *"),
            timezone=UTC_ZONE,
        )
        assert isinstance(trigger, CronTrigger)

    def test_a_missing_field_is_an_invalid_input(self) -> None:
        with pytest.raises(InvalidInputError):
            build_trigger(_schedule(kind=ScheduleKind.CRON, cron_expression=None), timezone=UTC_ZONE)

    def test_an_unparseable_cron_is_a_schedule_error(self) -> None:
        with pytest.raises(ScheduleError):
            build_trigger(
                _schedule(kind=ScheduleKind.CRON, cron_expression="not a cron"),
                timezone=UTC_ZONE,
            )


class TestNextRunTime:
    def test_an_interval_advances_by_its_period(self) -> None:
        now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        upcoming = next_run_time(_schedule(interval_seconds=60), timezone=UTC_ZONE, now=now)
        assert upcoming == now + timedelta(seconds=60)

    def test_a_one_shot_in_the_future_fires_at_its_time(self) -> None:
        now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        run_at = now + timedelta(hours=1)
        upcoming = next_run_time(
            _schedule(kind=ScheduleKind.ONCE, run_at=run_at),
            timezone=UTC_ZONE,
            now=now,
        )
        assert upcoming == run_at

    def test_a_one_shot_in_the_past_has_no_next_run(self) -> None:
        now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        upcoming = next_run_time(
            _schedule(kind=ScheduleKind.ONCE, run_at=now - timedelta(minutes=1)),
            timezone=UTC_ZONE,
            now=now,
        )
        assert upcoming is None

    def test_an_event_schedule_never_has_a_next_run(self) -> None:
        schedule = _schedule(kind=ScheduleKind.EVENT, payload={"event": "X"})
        assert next_run_time(schedule, timezone=UTC_ZONE) is None

    def test_validate_schedule_refuses_a_broken_expression(self) -> None:
        with pytest.raises(ScheduleError):
            validate_schedule(
                _schedule(kind=ScheduleKind.CRON, cron_expression="99 * * * *"),
                timezone=UTC_ZONE,
            )


class TestWorkflowParsing:
    def test_the_shorthand_payload_is_a_workflow(self) -> None:
        workflow = Workflow.from_payload({"goal": "Water the plants", "steps": [{"name": "open tap"}]})
        assert workflow.goal == "Water the plants"
        assert workflow.name == "Water the plants"
        assert workflow.steps[0].name == "open tap"
        assert workflow.task_priority is TaskPriority.NORMAL

    def test_the_nested_payload_is_a_workflow(self) -> None:
        workflow = Workflow.from_payload(
            {"workflow": {"name": "night", "goal": "Back up", "priority": "high"}}
        )
        assert workflow.name == "night"
        assert workflow.priority == "high"
        assert workflow.task_priority is TaskPriority.HIGH

    def test_a_workflow_round_trips(self) -> None:
        original = Workflow(
            name="garden",
            goal="Water",
            steps=(WorkflowStep(name="tap", input={"seconds": 5}),),
            priority=TaskPriority.LOW.value,
        )
        assert Workflow.from_payload(original.to_payload()) == original

    def test_a_missing_goal_is_refused(self) -> None:
        with pytest.raises(InvalidInputError):
            Workflow.from_payload({"steps": []})

    def test_a_step_needs_a_name(self) -> None:
        with pytest.raises(InvalidInputError):
            WorkflowStep(name="   ")

    def test_an_unknown_priority_is_refused(self) -> None:
        with pytest.raises(InvalidInputError):
            Workflow.from_payload({"goal": "x", "priority": "whenever"})

    def test_steps_must_be_a_list(self) -> None:
        with pytest.raises(InvalidInputError):
            Workflow.from_payload({"goal": "x", "steps": "nope"})
