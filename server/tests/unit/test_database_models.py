"""Tests for the ORM models (task T014).

Two kinds of check here, and the split matters:

*   **Dialect-level checks** compile every table to PostgreSQL DDL. This catches
    broken constraints, unsupported types, and bad index definitions without a
    running server -- which matters because no PostgreSQL is available yet
    (that is task T026).
*   **Behavioural checks** exercise the pure-Python logic on the models:
    namespace parsing, permission comparison, terminal-state predicates,
    expiry arithmetic, token totals.

Round-trip persistence checks against a live database are deliberately absent.
They would pass against SQLite while proving nothing about JSONB, `vector`, or
`timestamptz` -- the three things most likely to break. Those are T026's job.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any, cast

import pytest
from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, inspect as sa_inspect
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from app.database.models import (
    Agent,
    AgentLog,
    AgentStatus,
    AuditLogEntry,
    AuditOutcome,
    Base,
    Conversation,
    Device,
    DeviceEvent,
    DeviceStatus,
    Event,
    Memory,
    MemoryLayer,
    Message,
    MessageRole,
    ModelUsage,
    ModelUsageStatus,
    PermissionLevel,
    Project,
    Schedule,
    ScheduleKind,
    ScheduleStatus,
    Session,
    StepStatus,
    Task,
    TaskPriority,
    TaskStatus,
    TaskStep,
    ToolExecution,
    ToolExecutionStatus,
    User,
    VerificationOutcome,
    namespace_scope,
)

DIALECT = postgresql.dialect()

EXPECTED_TABLES = {
    "agent_logs",
    "agents",
    "audit_logs",
    "conversations",
    "device_events",
    "devices",
    "events",
    "memories",
    "messages",
    "model_usage",
    "projects",
    "schedules",
    "sessions",
    "task_steps",
    "tasks",
    "tool_executions",
    "users",
}


# ---------------------------------------------------------------------------
# Table inventory
# ---------------------------------------------------------------------------


def test_exactly_the_seventeen_spec_tables_exist() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_no_unexpected_table_is_registered() -> None:
    """A stray table usually means a model module was added but not reviewed."""
    assert len(Base.metadata.tables) == 17


def test_every_table_has_an_explicit_primary_key() -> None:
    for name, table in Base.metadata.tables.items():
        assert table.primary_key.columns, f"{name} has no primary key"


def test_every_table_has_uuid_primary_key() -> None:
    for name, table in Base.metadata.tables.items():
        pk = list(table.primary_key.columns)
        assert len(pk) == 1, f"{name} has a composite primary key"
        assert isinstance(pk[0].type, type(Base.metadata.tables["users"].c.id.type)), name


# ---------------------------------------------------------------------------
# PostgreSQL DDL compilation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("table_name", sorted(EXPECTED_TABLES))
def test_table_compiles_to_postgresql(table_name: str) -> None:
    table = Base.metadata.tables[table_name]
    ddl = str(CreateTable(table).compile(dialect=DIALECT))
    assert f"CREATE TABLE {table_name}" in ddl


@pytest.mark.parametrize("table_name", sorted(EXPECTED_TABLES))
def test_table_compiles_with_schema_none(table_name: str) -> None:
    """No table may depend on a non-default search_path."""
    table = Base.metadata.tables[table_name]
    ddl = str(CreateTable(table).compile(dialect=DIALECT))
    assert "SET search_path" not in ddl
    assert "public." not in ddl


def test_every_index_compiles() -> None:
    for table in Base.metadata.tables.values():
        for index in table.indexes:
            ddl = str(CreateIndex(index).compile(dialect=DIALECT))
            # Unique indexes render as CREATE UNIQUE INDEX, so the prefix check
            # has to allow for that rather than demanding plain CREATE INDEX.
            assert ddl.startswith(("CREATE INDEX", "CREATE UNIQUE INDEX")), ddl


def test_unique_constraints_are_named_deterministically() -> None:
    """Aren't named by column order, so reordering a mixin cannot churn DDL."""
    names = {index.name for table in Base.metadata.tables.values() for index in table.indexes}
    assert all(name is not None for name in names)
    # Constraint names must be unique across the database.
    constraint_names = [
        constraint.name
        for table in Base.metadata.tables.values()
        for constraint in table.constraints
        if constraint.name is not None
    ]
    assert len(constraint_names) == len(set(constraint_names))


def test_memories_embedding_is_vector_when_pgvector_is_enabled() -> None:
    """The column type follows ENABLE_PGVECTOR, not a hard-coded choice."""
    from app.config.settings import get_settings

    column = Base.metadata.tables["memories"].c.embedding
    if get_settings().database.enable_pgvector:
        assert isinstance(column.type, Vector), type(column.type)
        assert column.type.dim == get_settings().embeddings.dimensions
    else:
        assert isinstance(column.type, JSON), type(column.type)


@pytest.mark.parametrize(
    ("table_name", "column_name"),
    [
        ("events", "payload"),
        ("devices", "capabilities"),
        ("device_events", "payload"),
        ("tasks", "result"),
        ("agents", "tools"),
        ("agents", "permissions"),
        ("agent_logs", "payload"),
        ("tool_executions", "input_payload"),
        ("tool_executions", "output_payload"),
        ("task_steps", "depends_on"),
        ("task_steps", "input_payload"),
        ("audit_logs", "details"),
        ("conversations", "metadata"),
        ("projects", "settings"),
        ("schedules", "payload"),
    ],
)
def test_payload_columns_render_as_jsonb_on_postgresql(table_name: str, column_name: str) -> None:
    table = Base.metadata.tables[table_name]
    assert column_name in table.c, f"{table_name}.{column_name} does not exist"
    ddl = str(CreateTable(table).compile(dialect=DIALECT))
    assert f"{column_name} JSONB" in ddl, ddl


def test_model_usage_cost_is_numeric_not_float() -> None:
    column = Base.metadata.tables["model_usage"].c.cost_usd
    assert "NUMERIC" in str(column.type)
    assert "FLOAT" not in str(column.type)


# ---------------------------------------------------------------------------
# Spec-mandated fields
# ---------------------------------------------------------------------------


def test_agents_has_all_twelve_spec_attributes() -> None:
    """Spec section 6, lines 443-454."""
    columns = set(Base.metadata.tables["agents"].c.keys())
    expected = {
        "agent_type",
        "name",
        "description",
        "status",
        "task_id",
        "model",
        "tools",
        "permissions",
        "memory_namespace",
        "created_at",
        "updated_at",
    }
    assert expected <= columns
    assert len(expected) == 11  # plus agent_id, which is the id column


def test_tasks_has_all_twelve_spec_fields() -> None:
    """Spec section 18, lines 917-928.

    `steps` and `agent_id` are represented as relationships and as the reverse
    foreign key respectively; see the module docstring for why.
    """
    columns = set(Base.metadata.tables["tasks"].c.keys())
    expected = {
        "goal",
        "status",
        "priority",
        "created_at",
        "started_at",
        "completed_at",
        "parent_task_id",
        "result",
        "error",
    }
    assert expected <= columns
    # `steps` is a relationship, not a column; `agent_id` is the reverse
    # reference from `agents.task_id`. See the tasks module docstring.
    assert "steps" in sa_inspect(Task).relationships
    assert hasattr(Task, "steps")
    assert hasattr(Task, "current_agent_id")
    assert "task_id" in Base.metadata.tables["agents"].c


def test_devices_has_union_of_both_spec_field_lists() -> None:
    """Section 26 (7 fields) and the appendix (6 fields) disagree; take both."""
    columns = set(Base.metadata.tables["devices"].c.keys())
    assert {"name", "type", "capabilities", "status", "last_seen", "firmware_version"} <= columns
    assert "auth_token_hash" in columns


def test_agent_status_has_exactly_the_ten_spec_values() -> None:
    assert len(AgentStatus) == 10
    assert {member.name for member in AgentStatus} == {
        "CREATED",
        "INITIALIZING",
        "READY",
        "RUNNING",
        "WAITING",
        "VERIFYING",
        "COMPLETED",
        "FAILED",
        "CANCELLED",
        "TIMEOUT",
    }


def test_verification_outcome_matches_spec_section_17() -> None:
    assert {member.name for member in VerificationOutcome} == {
        "SUCCESS",
        "PARTIAL",
        "FAILED",
        "UNVERIFIED",
    }


def test_permission_levels_match_spec_section_15() -> None:
    assert len(PermissionLevel) == 6
    assert [member.level for member in PermissionLevel] == [0, 1, 2, 3, 4, 5]


def test_schedule_kinds_match_spec_section_44() -> None:
    assert {member.name for member in ScheduleKind} == {"INTERVAL", "CRON", "ONCE", "EVENT"}


# ---------------------------------------------------------------------------
# Enum behaviour
# ---------------------------------------------------------------------------


def test_permission_level_ordering_is_numeric() -> None:
    assert PermissionLevel.SYSTEM_CONFIG.allows(PermissionLevel.SAFE_ACTIONS)
    assert not PermissionLevel.SAFE_ACTIONS.allows(PermissionLevel.SYSTEM_CONFIG)
    assert PermissionLevel.DESTRUCTIVE.allows(PermissionLevel.DESTRUCTIVE)
    assert not PermissionLevel.READ_ONLY.allows(PermissionLevel.SAFE_ACTIONS)


def test_permission_level_str_is_the_name_not_the_digit() -> None:
    """A bare digit in a JSON payload is unreadable; the name is not."""
    assert str(PermissionLevel.MODIFY_PROJECT) == "MODIFY_PROJECT"
    assert PermissionLevel.MODIFY_PROJECT.value == "2"


def test_task_priority_rank_orders_urgent_first() -> None:
    ordered = sorted(TaskPriority, key=lambda member: member.rank)
    assert [member.name for member in ordered] == ["URGENT", "HIGH", "NORMAL", "LOW"]


def test_enum_names_are_valid_column_values() -> None:
    from app.database.models.enums import check_name_shape

    check_name_shape()  # must not raise


def test_str_enum_values_are_distinct() -> None:
    for enum_cls in (TaskStatus, StepStatus, ToolExecutionStatus, MessageRole):
        values = [member.value for member in enum_cls]
        assert len(values) == len(set(values)), enum_cls.__name__


# ---------------------------------------------------------------------------
# Namespace parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("namespace", "expected"),
    [
        ("user", ("user", "")),
        ("project:campuscare", ("project", "campuscare")),
        ("project:ultron", ("project", "ultron")),
        ("agent:coding", ("agent", "coding")),
        ("agent:research", ("agent", "research")),
        ("project:CampusCare", ("project", "campuscare")),
        ("project/campuscare", ("project", "campuscare")),
        ("project/campuscare/api", ("project", "campuscare/api")),
        ("  user  ", ("user", "")),
    ],
)
def test_namespace_scope(namespace: str, expected: tuple[str, str]) -> None:
    assert namespace_scope(namespace) == expected


def test_namespace_scope_keeps_the_first_separator_as_the_boundary() -> None:
    """`agent:x:y` is agent `x:y`, not agent `x` truncated at the colon."""
    assert namespace_scope("agent:team:one") == ("agent", "team:one")


# ---------------------------------------------------------------------------
# Model behaviour
# ---------------------------------------------------------------------------


def test_agent_active_and_terminal_are_disjoint() -> None:
    agent = Agent(name="a", agent_type="mock", status=AgentStatus.RUNNING)
    assert agent.is_active
    assert not agent.is_terminal

    agent.status = AgentStatus.COMPLETED
    assert not agent.is_active
    assert agent.is_terminal

    for status in AgentStatus:
        assert not (agent_is_active(status) and agent_is_terminal(status))


def agent_is_active(status: AgentStatus) -> bool:
    agent = Agent(name="a", agent_type="mock", status=status)
    return bool(agent.is_active)


def agent_is_terminal(status: AgentStatus) -> bool:
    agent = Agent(name="a", agent_type="mock", status=status)
    return bool(agent.is_terminal)


def test_every_agent_status_is_active_terminal_or_neither() -> None:
    """No status may be silently unclassifiable."""
    for status in AgentStatus:
        agent = Agent(name="a", agent_type="mock", status=status)
        assert isinstance(agent.is_active, bool)
        assert isinstance(agent.is_terminal, bool)
        assert not (agent.is_active and agent.is_terminal)


def test_task_terminal_states() -> None:
    task = Task(goal="g", status=TaskStatus.RUNNING)
    assert not task.is_terminal
    for terminal in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
        task.status = terminal
        assert task.is_terminal
    for non_terminal in (
        TaskStatus.PENDING,
        TaskStatus.QUEUED,
        TaskStatus.BLOCKED,
        TaskStatus.PAUSED,
    ):
        task.status = non_terminal
        assert not task.is_terminal


def test_task_duration_is_none_until_complete() -> None:
    task = Task(goal="g", status=TaskStatus.RUNNING)
    assert task.duration_seconds is None
    start = datetime.now(UTC)
    task.started_at = start
    assert task.duration_seconds is None
    task.completed_at = start + timedelta(seconds=30)
    assert task.duration_seconds == pytest.approx(30.0)


def test_task_step_duration_matches_task_semantics() -> None:
    step = TaskStep(task_id=uuid.uuid4(), name="s", status=StepStatus.PENDING)
    assert step.duration_seconds is None
    start = datetime.now(UTC)
    step.started_at = start
    step.completed_at = start + timedelta(seconds=2.5)
    assert step.duration_seconds == pytest.approx(2.5)


def test_model_usage_total_tokens_is_none_when_partial() -> None:
    """A failed call may have prompt tokens and no completion tokens."""
    usage = ModelUsage(model="m", recorded_at=datetime.now(UTC))
    assert usage.total_tokens is None

    usage.prompt_tokens = 100
    assert usage.total_tokens is None

    usage.completion_tokens = 50
    assert usage.total_tokens == 150


def test_model_usage_succeeded() -> None:
    usage = ModelUsage(model="m", status=ModelUsageStatus.SUCCESS, recorded_at=datetime.now(UTC))
    assert usage.succeeded
    usage.status = ModelUsageStatus.TIMEOUT
    assert not usage.succeeded


def test_model_usage_cost_holds_exact_decimals() -> None:
    usage = ModelUsage(model="m", cost_usd=Decimal("0.00000001"), recorded_at=datetime.now(UTC))
    assert usage.cost_usd == Decimal("0.00000001")


def test_message_total_tokens_is_none_when_partial() -> None:
    message = Message(conversation_id=uuid.uuid4(), content="x", role=MessageRole.USER)
    assert message.total_tokens is None
    message.prompt_tokens = 5
    assert message.total_tokens is None
    message.completion_tokens = 7
    assert message.total_tokens == 12


def test_memory_expiry_with_and_without_timestamp() -> None:
    past = datetime.now(UTC) - timedelta(hours=1)
    assert Memory(
        namespace="user", layer=MemoryLayer.SHORT_TERM, content="x", expires_at=past
    ).is_expired

    future = datetime.now(UTC) + timedelta(hours=1)
    assert not Memory(
        namespace="user", layer=MemoryLayer.SHORT_TERM, content="x", expires_at=future
    ).is_expired

    assert not Memory(namespace="user", layer=MemoryLayer.LONG_TERM, content="x").is_expired


def test_memory_naive_expiry_is_treated_as_utc() -> None:
    naive_past = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)
    memory = Memory(
        namespace="user",
        layer=MemoryLayer.SHORT_TERM,
        content="x",
        expires_at=naive_past,
    )
    assert memory.is_expired


def test_memory_embedding_json_round_trip() -> None:
    memory = Memory(namespace="user", layer=MemoryLayer.SEMANTIC, content="x")
    assert memory.embedding_as_json() is None
    memory.embedding = [0.1, 0.2, 0.3]
    assert memory.embedding_as_json() == "[0.1, 0.2, 0.3]"


def test_memory_embedding_json_accepts_a_preexisting_string() -> None:
    """A vector read back from the JSON fallback is already serialised."""
    memory = Memory(namespace="user", layer=MemoryLayer.SEMANTIC, content="x", embedding="[0.1]")
    assert memory.embedding_as_json() == "[0.1]"


def test_project_memory_namespace_matches_spec_format() -> None:
    project = Project(name="CampusCare", slug="campuscare")
    assert project.memory_namespace == "project:campuscare"
    assert namespace_scope(project.memory_namespace) == ("project", "campuscare")


def test_device_stale_ignores_never_seen() -> None:
    device = Device(name="esp", status=DeviceStatus.UNKNOWN)
    assert not device.is_stale(timeout_seconds=60)


def test_device_stale_uses_heartbeat_timeout() -> None:
    now = datetime.now(UTC)
    device = Device(name="esp", status=DeviceStatus.ONLINE, last_seen=now - timedelta(seconds=30))
    assert not device.is_stale(timeout_seconds=60, now=now)
    device.last_seen = now - timedelta(seconds=90)
    assert device.is_stale(timeout_seconds=60, now=now)


def test_device_stale_treats_naive_last_seen_as_utc() -> None:
    now = datetime.now(UTC)
    device = Device(
        name="esp",
        status=DeviceStatus.ONLINE,
        last_seen=(now - timedelta(seconds=90)).replace(tzinfo=None),
    )
    assert device.is_stale(timeout_seconds=60, now=now)


def test_device_event_offline_signal_matches_both_spec_spellings() -> None:
    device_id = uuid.uuid4()
    for name in ("device_offline", "DEVICE_OFFLINE", "disconnected", "DEVICE_DISCONNECTED"):
        event = DeviceEvent(device_id=device_id, event_type=name, received_at=datetime.now(UTC))
        assert event.is_offline_signal, name

    for name in ("wake_word", "button_press", "sensor_event", "audio_stream"):
        event = DeviceEvent(device_id=device_id, event_type=name, received_at=datetime.now(UTC))
        assert not event.is_offline_signal, name


def test_schedule_validity_per_kind() -> None:
    now = datetime.now(UTC)
    assert Schedule(name="s", kind=ScheduleKind.INTERVAL, interval_seconds=60).is_valid
    assert not Schedule(name="s", kind=ScheduleKind.INTERVAL).is_valid
    assert not Schedule(name="s", kind=ScheduleKind.INTERVAL, interval_seconds=0).is_valid
    assert Schedule(name="s", kind=ScheduleKind.CRON, cron_expression="* * * * *").is_valid
    assert not Schedule(name="s", kind=ScheduleKind.CRON).is_valid
    assert Schedule(name="s", kind=ScheduleKind.ONCE, run_at=now).is_valid
    assert not Schedule(name="s", kind=ScheduleKind.ONCE).is_valid
    assert Schedule(name="s", kind=ScheduleKind.EVENT, payload={"event": "WAKE_DETECTED"}).is_valid
    assert not Schedule(name="s", kind=ScheduleKind.EVENT).is_valid


def test_schedule_is_due_requires_an_active_status_and_a_time() -> None:
    now = datetime.now(UTC)

    def schedule(**overrides: object) -> Schedule:
        """Build a schedule with the status set explicitly.

        `Schedule.status` has a column default, but a column default is applied
        by the database on INSERT -- an unflushed object has None there. Setting
        it here keeps the test about `is_due` rather than about when SQLAlchemy
        chooses to populate defaults.
        """
        defaults: dict[str, object] = {
            "name": "s",
            "kind": ScheduleKind.INTERVAL,
            "interval_seconds": 60,
            "status": ScheduleStatus.ACTIVE,
        }
        defaults.update(overrides)
        return Schedule(**cast("Any", defaults))

    assert schedule(next_run_at=now - timedelta(seconds=1)).is_due
    assert not schedule(next_run_at=now + timedelta(hours=1)).is_due
    assert not schedule().is_due
    assert not schedule(status=ScheduleStatus.PAUSED, next_run_at=now - timedelta(seconds=1)).is_due
    assert not schedule(status=ScheduleStatus.FAILED, next_run_at=now - timedelta(seconds=1)).is_due


def test_audit_log_denied_predicate() -> None:
    assert AuditLogEntry(
        occurred_at=datetime.now(UTC),
        actor_type="agent",
        action="tool.execute",
        decision=AuditOutcome.DENIED,
    ).denied
    assert not AuditLogEntry(
        occurred_at=datetime.now(UTC),
        actor_type="user",
        action="tool.execute",
        decision=AuditOutcome.ALLOWED,
    ).denied


def test_audit_log_distinguishes_confirm_from_deny() -> None:
    """A confirmation prompt is the case an auditor most wants to see."""
    entry = AuditLogEntry(
        occurred_at=datetime.now(UTC),
        actor_type="agent",
        action="file.delete",
        decision=AuditOutcome.CONFIRM_REQUIRED,
    )
    assert not entry.denied


# ---------------------------------------------------------------------------
# Session behaviour
# ---------------------------------------------------------------------------


def test_session_validity_and_revocation() -> None:
    now = datetime.now(UTC)
    session = Session(
        user_id=uuid.uuid4(),
        token_hash="h",
        expires_at=now + timedelta(minutes=5),
    )
    assert session.is_valid(now=now)

    session.revoke(reason="logout", now=now)
    assert not session.is_valid(now=now)
    assert session.revoke_reason == "logout"


def test_revoking_twice_preserves_the_first_reason() -> None:
    now = datetime.now(UTC)
    session = Session(
        user_id=uuid.uuid4(),
        token_hash="h",
        expires_at=now + timedelta(minutes=5),
    )
    session.revoke(reason="logout", now=now)
    session.revoke(reason="admin", now=now + timedelta(seconds=5))
    assert session.revoke_reason == "logout"
    assert session.revoked_at == now


def test_expired_session_is_invalid() -> None:
    now = datetime.now(UTC)
    session = Session(
        user_id=uuid.uuid4(),
        token_hash="h",
        expires_at=now - timedelta(seconds=1),
    )
    assert not session.is_valid(now=now)


def test_session_refresh_requires_a_refresh_expiry() -> None:
    now = datetime.now(UTC)
    without = Session(user_id=uuid.uuid4(), token_hash="h", expires_at=now + timedelta(minutes=5))
    assert not without.is_refreshable(now=now)

    with_refresh = Session(
        user_id=uuid.uuid4(),
        token_hash="h",
        expires_at=now - timedelta(minutes=1),
        refresh_expires_at=now + timedelta(days=1),
    )
    assert with_refresh.is_refreshable(now=now)
    assert not with_refresh.is_valid(now=now)


def test_revoked_session_cannot_be_refreshed() -> None:
    now = datetime.now(UTC)
    session = Session(
        user_id=uuid.uuid4(),
        token_hash="h",
        expires_at=now - timedelta(minutes=1),
        refresh_expires_at=now + timedelta(days=1),
    )
    session.revoke(now=now)
    assert not session.is_refreshable(now=now)


# ---------------------------------------------------------------------------
# Relationship wiring
# ---------------------------------------------------------------------------


def test_agent_spec_alias_for_id() -> None:
    agent = Agent(id=uuid.uuid4(), name="a", agent_type="mock")
    assert agent.agent_id == agent.id


def test_task_spec_alias_for_id() -> None:
    task = Task(id=uuid.uuid4(), goal="g")
    assert task.task_id == task.id


def test_device_spec_alias_for_id() -> None:
    device = Device(id=uuid.uuid4(), name="d")
    assert device.device_id == device.id


def test_user_denial_of_service_row_has_sane_repr() -> None:
    user = User(username="alice")
    assert "alice" in repr(user)


def test_tool_execution_permission_columns_are_mandatory() -> None:
    """Section 15 requires every execution to be permission-checked and logged.

    NOT NULL on the verdict columns is what makes an unchecked execution
    unrepresentable rather than merely discouraged.
    """
    columns = Base.metadata.tables["tool_executions"].c
    assert columns["permission_level"].nullable is False
    assert columns["decision"].nullable is False


def test_session_cascade_and_rotation_use_the_intended_on_delete() -> None:
    sessions = Base.metadata.tables["sessions"]
    user_fk = next(fk for fk in sessions.c.user_id.foreign_keys)
    assert user_fk.ondelete == "CASCADE"

    rotation_fk = next(fk for fk in sessions.c.rotated_from.foreign_keys)
    assert rotation_fk.ondelete == "SET NULL"


def test_deleting_an_agent_does_not_delete_its_task() -> None:
    agents = Base.metadata.tables["agents"]
    task_fk = next(fk for fk in agents.c.task_id.foreign_keys)
    assert task_fk.ondelete == "SET NULL"


def test_deleting_a_task_deletes_its_steps() -> None:
    steps = Base.metadata.tables["task_steps"]
    task_fk = next(fk for fk in steps.c.task_id.foreign_keys)
    assert task_fk.ondelete == "CASCADE"


def test_deleting_a_task_does_not_delete_the_schedule_that_made_it() -> None:
    schedules = Base.metadata.tables["schedules"]
    task_fk = next(fk for fk in schedules.c.last_task_id.foreign_keys)
    assert task_fk.ondelete == "SET NULL"


def test_event_type_is_not_a_database_enum() -> None:
    """The bus must accept types this build does not know about yet."""
    column = Base.metadata.tables["events"].c.event_type
    assert "Enum" not in type(column.type).__name__


def test_collection_relationships_do_not_load_implicitly() -> None:
    """An agent row must not drag its whole log history along with it.

    `raise_on_sql` is the explicit form: touching the attribute without having
    asked for the load raises, rather than silently issuing a query whose cost
    grows with the table. SQLAlchemy 2.1 deprecates `noload` in favour of this,
    and `noload` returns None for related items in the meantime, which is worse
    than raising.
    """
    assert Agent.logs.property.lazy == "raise_on_sql"
    assert Task.steps.property.lazy == "raise_on_sql"
    assert Task.tool_executions.property.lazy == "raise_on_sql"
    assert Conversation.messages.property.lazy == "raise_on_sql"


def test_current_agent_id_reflects_the_agent_that_holds_the_task() -> None:
    """The spec's `agent_id` task field, derived rather than stored."""
    task_id = uuid.uuid4()
    other_task = uuid.uuid4()
    task = Task(id=task_id, goal="g")

    assert task.current_agent_id is None

    task.agents.append(Agent(id=uuid.uuid4(), name="a", agent_type="mock", task_id=task_id))
    assert task.current_agent_id is not None

    task.agents.append(Agent(id=uuid.uuid4(), name="b", agent_type="mock", task_id=other_task))
    assert task.current_agent_id in {agent.id for agent in task.agents if agent.task_id == task_id}


# ---------------------------------------------------------------------------
# Defensive branches
# ---------------------------------------------------------------------------


def test_embedding_type_degrades_when_pgvector_is_disabled() -> None:
    """ENABLE_PGVECTOR=false must not make the package unimportable."""
    from dataclasses import dataclass

    from app.config.settings import Settings
    from app.database.models.memories import _embedding_type

    @dataclass
    class _FakeEmbeddings:
        dimensions: int = 768

    @dataclass
    class _FakeDatabase:
        enable_pgvector: bool = False

    @dataclass
    class _FakeSettings:
        embeddings: _FakeEmbeddings
        database: _FakeDatabase

    def as_settings(*, enable_pgvector: bool) -> Settings:
        """Adapt the fakes to the annotated parameter type."""
        fake = _FakeSettings(_FakeEmbeddings(384), _FakeDatabase(enable_pgvector=enable_pgvector))
        return cast("Settings", fake)

    disabled = _embedding_type(as_settings(enable_pgvector=False))
    assert isinstance(disabled, JSON)

    enabled = _embedding_type(as_settings(enable_pgvector=True))
    assert isinstance(enabled, Vector)
    assert enabled.dim == 384


def test_resolve_settings_returns_none_when_settings_cannot_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bare import with no environment must not raise."""
    import app.database.models.memories as memories_module

    def _boom() -> None:
        msg = "no environment"
        raise RuntimeError(msg)

    monkeypatch.setattr(memories_module, "get_settings", _boom)
    assert memories_module._resolve_settings() is None


def test_embedding_type_without_pgvector_installed_is_json(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    import app.database.models.memories as memories_module

    real_import = builtins.__import__

    def _fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "pgvector.sqlalchemy":
            msg = "no module named pgvector"
            raise ImportError(msg)
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    assert isinstance(memories_module._embedding_type(None), JSON)


def test_check_name_shape_rejects_an_unstorable_member(monkeypatch: pytest.MonkeyPatch) -> None:
    """A member name that is not an identifier could not be a column value."""
    import app.database.models.enums as enums_module

    # Built through the functional API because a non-identifier member name
    # cannot be written as a class-body assignment.
    broken = StrEnum("broken", {"GOOD": "good", "not an identifier": "bad"})

    monkeypatch.setattr(enums_module, "TaskStatus", broken)
    with pytest.raises(ValueError, match="not a valid column value"):
        enums_module.check_name_shape()


def test_schedule_is_valid_is_false_for_an_unknown_kind() -> None:
    """Defensive: a kind outside the enum must not be treated as runnable."""
    schedule = Schedule(name="s", kind=ScheduleKind.INTERVAL, interval_seconds=60)
    object.__setattr__(schedule, "kind", "not_a_real_kind")
    assert not schedule.is_valid


def test_schedule_is_due_treats_a_naive_next_run_as_utc() -> None:
    now = datetime.now(UTC)
    schedule = Schedule(
        name="s",
        kind=ScheduleKind.INTERVAL,
        interval_seconds=60,
        status=ScheduleStatus.ACTIVE,
        next_run_at=(now - timedelta(seconds=5)).replace(tzinfo=None),
    )
    assert schedule.is_due


def test_memory_embedding_from_a_binary_buffer() -> None:
    """A numpy-backed vector arrives as a flat float32 buffer."""
    import struct

    buffer = memoryview(struct.pack("<3f", 0.5, 0.25, 0.125))
    memory = Memory(
        namespace="user",
        layer=MemoryLayer.SEMANTIC,
        content="x",
        embedding=buffer,
        vector_dimensions=3,
    )
    assert json.loads(memory.embedding_as_json() or "[]") == pytest.approx([0.5, 0.25, 0.125])


def test_memory_embedding_buffer_of_the_wrong_length_is_rejected() -> None:
    """A truncated vector would be unsearchable rather than obviously wrong."""
    memory = Memory(
        namespace="user",
        layer=MemoryLayer.SEMANTIC,
        content="x",
        embedding=memoryview(b"\x00\x00\x80\x3f"),
        vector_dimensions=768,
    )
    with pytest.raises(ValueError, match="vector_dimensions records 768"):
        memory.embedding_as_json()


def test_memory_embedding_buffer_that_is_not_float32_is_rejected() -> None:
    memory = Memory(
        namespace="user",
        layer=MemoryLayer.SEMANTIC,
        content="x",
        embedding=memoryview(b"\x01\x02\x03"),
    )
    with pytest.raises(ValueError, match="not a whole number of float32"):
        memory.embedding_as_json()


def test_memory_embedding_buffer_accepts_bytes_directly() -> None:
    import struct

    memory = Memory(
        namespace="user",
        layer=MemoryLayer.SEMANTIC,
        content="x",
        embedding=struct.pack("<2f", 1.0, 2.0),
        vector_dimensions=2,
    )
    assert json.loads(memory.embedding_as_json() or "[]") == pytest.approx([1.0, 2.0])


# ---------------------------------------------------------------------------
# Representations
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("instance", "expected"),
    [
        (
            Agent(id=uuid.UUID(int=1), name="coder", agent_type="coding", status=AgentStatus.READY),
            "coder",
        ),
        (AgentLog(agent_id=uuid.UUID(int=2), level="INFO", message="m"), "INFO"),
        (Conversation(id=uuid.UUID(int=3), title="chat"), "chat"),
        (Device(id=uuid.UUID(int=4), name="esp32", status=DeviceStatus.ONLINE), "esp32"),
        (Event(event_type="TASK_CREATED", occurred_at=datetime.now(UTC)), "TASK_CREATED"),
        (Message(conversation_id=uuid.UUID(int=5), content="c", role=MessageRole.USER), "user"),
        (Project(id=uuid.UUID(int=6), name="CampusCare", slug="campuscare"), "campuscare"),
        (
            Schedule(
                id=uuid.UUID(int=7),
                name="nightly",
                kind=ScheduleKind.CRON,
                status=ScheduleStatus.ACTIVE,
            ),
            "nightly",
        ),
        (Session(user_id=uuid.UUID(int=8), token_hash="h", expires_at=datetime.now(UTC)), "active"),
        (Task(id=uuid.UUID(int=9), goal="g", status=TaskStatus.RUNNING), "running"),
        (TaskStep(task_id=uuid.UUID(int=10), name="s", status=StepStatus.COMPLETED), "completed"),
        (ToolExecution(tool_name="mock.echo", status=ToolExecutionStatus.SUCCEEDED), "succeeded"),
        (ModelUsage(model="qwen", status=ModelUsageStatus.ERROR), "error"),
        (
            AuditLogEntry(
                occurred_at=datetime.now(UTC),
                actor_type="agent",
                action="tool.execute",
                decision=AuditOutcome.ALLOWED,
            ),
            "allowed",
        ),
        (
            Memory(namespace="project:ultron", layer=MemoryLayer.SEMANTIC, content="a memory"),
            "project:ultron",
        ),
    ],
)
def test_repr_is_readable_and_does_not_leak_payload(instance: object, expected: str) -> None:
    """Reprs end up in logs and tracebacks, so they must identify without dumping.

    An enum renders as its stored value, not its member name, because the value
    is what actually sits in the column -- hence the lowercase expectations.
    """
    rendered = repr(instance)
    assert expected in rendered
    assert rendered.startswith("<")
    assert rendered.endswith(">")


def test_memory_repr_truncates_and_flattens_content() -> None:
    memory = Memory(
        namespace="user",
        layer=MemoryLayer.EPISODIC,
        content="x" * 200 + "\nsecond line",
    )
    rendered = repr(memory)
    assert "\n" not in rendered
    assert "second line" not in rendered


def test_base_repr_falls_back_when_there_is_no_id() -> None:
    assert repr(User(username="bob")).startswith("<User")


def test_base_repr_covers_a_model_that_forgets_its_own() -> None:
    """Every model overrides `__repr__`; this is the safety net for one that does not.

    `Base.__repr__` is reached explicitly rather than through a subclass, because
    adding a throwaway model just to exercise it would put a stray table in
    `Base.metadata`.
    """
    rendered = Base.__repr__(User(id=uuid.UUID(int=42), username="carol"))
    assert rendered.startswith("<User ")
    assert str(uuid.UUID(int=42)) in rendered


def test_base_repr_renders_nothing_when_no_id_is_set_yet() -> None:
    """An object with no id yet must still produce a usable repr."""
    rendered = Base.__repr__(User(username="dave"))
    assert rendered.startswith("<User")


def test_event_is_error_flag() -> None:
    occurred = datetime.now(UTC)
    assert not Event(event_type="TASK_CREATED", occurred_at=occurred).is_error
    assert Event(event_type="TASK_FAILED", occurred_at=occurred, error="boom").is_error


def test_tool_execution_succeeded_flag() -> None:
    assert ToolExecution(tool_name="mock.echo", status=ToolExecutionStatus.SUCCEEDED).succeeded
    assert not ToolExecution(tool_name="mock.fail", status=ToolExecutionStatus.FAILED).succeeded
    assert not ToolExecution(tool_name="mock.echo", status=ToolExecutionStatus.DENIED).succeeded


def test_device_event_repr_identifies_its_device() -> None:
    event = DeviceEvent(
        device_id=uuid.UUID(int=7),
        event_type="wake_word",
        received_at=datetime.now(UTC),
    )
    assert str(uuid.UUID(int=7)) in repr(event)
    assert "wake_word" in repr(event)


def test_session_repr_shows_revocation() -> None:
    session = Session(
        user_id=uuid.uuid4(),
        token_hash="h",
        expires_at=datetime.now(UTC),
        revoked_at=datetime.now(UTC),
    )
    assert "revoked" in repr(session)
