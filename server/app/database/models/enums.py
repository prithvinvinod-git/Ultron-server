"""Enumerations shared by the ORM models and the application layer.

Where the spec fixes a value set, the member names match the spec's wording
verbatim so the mapping is checkable by reading `server_arc.md` next to this
file. Where the spec is silent, the note on each enum records that the set is an
engineering decision.

Values are stored as their **name**, not their value. `Enum(..., values_callable=...)`
is not used, deliberately: SQLAlchemy's default persists the member *name*, and
that is what is relied upon. Two consequences follow. First, renaming a member
is a schema change and must go through a migration -- acceptable, since these
names appear in API payloads and event types that clients already depend on.
Second, no enum member may be given a numeric `value`, because two members with
the same stored name would collide; `check_name_shape` and the round-trip test in
`tests/unit/test_database_models.py` enforce that.
"""

from __future__ import annotations

from enum import StrEnum


class AgentStatus(StrEnum):
    """Agent lifecycle (spec section 6, lines 459-481 -- verbatim)."""

    CREATED = "created"
    INITIALIZING = "initializing"
    READY = "ready"
    RUNNING = "running"
    WAITING = "waiting"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


class TaskStatus(StrEnum):
    """Task status.

    **Not specified by the spec.** Section 18 (line 919) lists `status` as a
    field but never says what may go in it. This set is derived from the
    surrounding requirements instead of invented freely: the spec requires task
    graphs (line 931), restart survival (line 949), and the events
    `TASK_CREATED`/`TASK_STARTED`/`TASK_COMPLETED`/`TASK_FAILED`
    (lines 962-965), which together require a queued/running distinction and a
    terminal set. `BLOCKED` and `PAUSED` exist because a graph whose dependency
    has not finished is neither queued nor running, and a suspended task needs a
    state that is not merely slow.

    Phase 2's state machine (task T038) is the authority on legal transitions;
    this enum only fixes the vocabulary.
    """

    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    BLOCKED = "blocked"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskPriority(StrEnum):
    """Task priority.

    **Not specified by the spec** -- `priority` appears once, as a field name
    (line 920). The spec does require that a request becomes a task graph
    (section 50) and that schedules trigger work (section 44), so ordering has
    to be expressible; a plain three-level scale is enough. Numeric ordering is
    handled by `rank`, since a StrEnum sorts alphabetically and `HIGH` would
    otherwise sort below `LOW`.
    """

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"

    @property
    def rank(self) -> int:
        """Sortable weight, highest first."""
        return _PRIORITY_RANK[self]


_PRIORITY_RANK: dict[TaskPriority, int] = {
    TaskPriority.URGENT: 0,
    TaskPriority.HIGH: 1,
    TaskPriority.NORMAL: 2,
    TaskPriority.LOW: 3,
}


class StepStatus(StrEnum):
    """Status of one step within a task.

    **Not specified.** The spec names `task_steps` (line 1124) and `steps` as a
    task field (line 926) but defines no step vocabulary.
    """

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class ToolExecutionStatus(StrEnum):
    """Status of a single tool invocation.

    **Not specified.** The spec gives the `tool_executions` table (line 1125) and
    the events `TOOL_STARTED`/`TOOL_COMPLETED`/`TOOL_FAILED` (lines 973-975);
    the event names are the source of the three terminal-ish states here.
    `AWAITING_CONFIRM` is added because the permission system has a *confirm*
    verdict (spec section 15) and a tool call that is waiting on the user is
    neither running nor finished.
    """

    PENDING = "pending"
    AWAITING_CONFIRM = "awaiting_confirm"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DENIED = "denied"


class PermissionLevel(StrEnum):
    """Permission levels (spec section 15, lines 795-815 -- verbatim labels).

    A `StrEnum` whose members carry the digit as their value, because a
    `StrEnum` member must be a string -- an `IntEnum` would work numerically but
    would render as ``0`` instead of ``read_only`` in the JSON bodies and event
    payloads this project uses. `level` converts when an actual integer is
    needed.

    The digits are meaningful: level N implies level N-1, so `allows` is a
    comparison rather than a table lookup. The stored column value is the member
    *name*, so the digit never reaches the database (see the module docstring).
    """

    READ_ONLY = "0"
    SAFE_ACTIONS = "1"
    MODIFY_PROJECT = "2"
    EXECUTE_PROGRAMS = "3"
    SYSTEM_CONFIG = "4"
    DESTRUCTIVE = "5"

    @property
    def level(self) -> int:
        """The numeric level, for arithmetic and comparisons."""
        return int(self.value)

    def allows(self, requested: PermissionLevel) -> bool:
        """Report whether this level is sufficient for `requested`."""
        return self.level >= requested.level

    def __str__(self) -> str:
        return self.name


class VerificationOutcome(StrEnum):
    """Verification results (spec section 17, lines 899-906 -- verbatim)."""

    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    UNVERIFIED = "unverified"


class DeviceStatus(StrEnum):
    """Device status.

    **Not specified as a status set.** The spec lists device *events*
    `device_online` and `device_offline` (lines 1222-1223) but never says what
    `status` may hold. Two online-ish states are separated because the spec also
    requires a heartbeat timeout (`.env.example` line 263) and a disconnected
    device is distinguishable from one that has never answered.
    """

    UNKNOWN = "unknown"
    ONLINE = "online"
    OFFLINE = "offline"
    DISABLED = "disabled"


class ConversationStatus(StrEnum):
    """Conversation status. **Not specified by the spec.**"""

    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETED = "deleted"


class MessageRole(StrEnum):
    """Message author role. **Not specified by the spec.**

    The word "role" appears twice in the whole document (lines 3 and 1361) and
    neither is a message role, so this set is an engineering decision. `tool` is
    included because the tool pipeline records its output (spec section 16) and
    that output has to live somewhere in the transcript or the model cannot see
    the result of its own tool call.
    """

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class MemoryLayer(StrEnum):
    """Memory layers (spec section 22, lines 1071-1089).

    The spec names five layers as prose headings and never binds them to a
    column, but they are clearly meant to be distinguishable, so they are.
    """

    SHORT_TERM = "short_term"
    LONG_TERM = "long_term"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROJECT = "project"


class ScheduleKind(StrEnum):
    """Scheduler kinds (spec section 44, lines 1778-1781 -- verbatim)."""

    INTERVAL = "interval"
    CRON = "cron"
    ONCE = "once"
    EVENT = "event"


class ScheduleStatus(StrEnum):
    """Schedule status. **Not specified by the spec.**"""

    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class AuditOutcome(StrEnum):
    """Audit decision outcome. **Not specified by the spec.**"""

    ALLOWED = "allowed"
    DENIED = "denied"
    CONFIRM_REQUIRED = "confirm_required"


class ModelUsageStatus(StrEnum):
    """Outcome of one model call. **Not specified by the spec.**"""

    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"


def check_name_shape() -> None:
    """Raise if any enum member carries a value that could not be stored.

    SQLAlchemy persists the member *name*, so a name that is not a valid,
    non-empty identifier would silently produce an unusable column value. This
    is a guard for future edits, not a runtime check on data.
    """
    for enum_cls in (
        AgentStatus,
        TaskStatus,
        TaskPriority,
        StepStatus,
        ToolExecutionStatus,
        PermissionLevel,
        VerificationOutcome,
        DeviceStatus,
        ConversationStatus,
        MessageRole,
        MemoryLayer,
        ScheduleKind,
        ScheduleStatus,
        AuditOutcome,
        ModelUsageStatus,
    ):
        for member in enum_cls:
            if not member.name.isidentifier():
                msg = f"{enum_cls.__name__}.{member.name} is not a valid column value"
                raise ValueError(msg)
