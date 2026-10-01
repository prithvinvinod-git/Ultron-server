"""SQLAlchemy ORM models. PostgreSQL is the authoritative store.

Sixteen tables from spec section 23 plus `schedules`, seventeen in total:

    users            sessions         conversations   messages
    projects         agents           agent_logs
    tasks            task_steps       tool_executions
    events           memories
    devices          device_events
    model_usage      audit_logs       schedules

Two spec lists disagree about the membership -- section 23 (lines 1119-1136) omits
`schedules`, and the architecture diagram (lines 3082-3098) omits `device_events`
and `agent_logs`. The union is implemented, and the divergence is recorded here
rather than left for someone to rediscover.

Importing this package registers every table on `Base.metadata`, which is what
Alembic's autogenerate reads. A model module that is not imported here will not
appear in a generated migration.
"""

from app.database.models.agents import Agent, AgentLog
from app.database.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, json_type
from app.database.models.conversations import Conversation, Message
from app.database.models.devices import Device, DeviceEvent
from app.database.models.enums import (
    AgentStatus,
    AuditOutcome,
    ConversationStatus,
    DeviceStatus,
    MemoryLayer,
    MessageRole,
    ModelUsageStatus,
    PermissionLevel,
    ScheduleKind,
    ScheduleStatus,
    StepStatus,
    TaskPriority,
    TaskStatus,
    ToolExecutionStatus,
    VerificationOutcome,
    check_name_shape,
)
from app.database.models.events import Event
from app.database.models.memories import Memory, namespace_scope
from app.database.models.projects import Project
from app.database.models.schedules import Schedule
from app.database.models.sessions import Session
from app.database.models.tasks import Task, TaskStep, ToolExecution
from app.database.models.usage import ModelUsage
from app.database.models.users import AuditLogEntry, User

check_name_shape()

__all__ = [
    "Agent",
    "AgentLog",
    "AgentStatus",
    "AuditLogEntry",
    "AuditOutcome",
    "Base",
    "Conversation",
    "ConversationStatus",
    "Device",
    "DeviceEvent",
    "DeviceStatus",
    "Event",
    "Memory",
    "MemoryLayer",
    "Message",
    "MessageRole",
    "ModelUsage",
    "ModelUsageStatus",
    "PermissionLevel",
    "Project",
    "Schedule",
    "ScheduleKind",
    "ScheduleStatus",
    "Session",
    "StepStatus",
    "Task",
    "TaskPriority",
    "TaskStatus",
    "TaskStep",
    "TimestampMixin",
    "ToolExecution",
    "ToolExecutionStatus",
    "UUIDPrimaryKeyMixin",
    "User",
    "VerificationOutcome",
    "check_name_shape",
    "json_type",
    "namespace_scope",
]
