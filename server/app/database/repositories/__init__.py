"""Repository layer (T015).

One repository per aggregate, all sharing :class:`Repository` /
:class:`UuidRepository` from :mod:`app.database.repositories.base`.

Four rules hold across every module here, and they are why this layer exists
separately from the models:

- **No repository commits.** :func:`app.database.session.session_scope` owns the
  transaction. A repository that committed would make multi-write handlers
  impossible to roll back cleanly.
- **Reads are bounded.** Every list method routes through
  :func:`~app.database.repositories.base.apply_limit`, so no query can be
  accidentally unbounded.
- **SQLAlchemy errors are translated once,** in the base, and the driver's own
  message is never copied into an Ultron error.
- **Results are typed in one place,** via ``_fetch_all`` / ``_fetch_one``, so no
  list method needs a ``cast`` nobody re-checks.

This layer also does not make authorisation decisions. It records the decisions
it is given -- :class:`ToolExecutionRepository` is the clearest case, where the
``permission_level`` and ``decision`` arguments are mandatory and non-nullable, so
a tool call cannot be recorded without them. *Deciding* what is permitted belongs
to the permission layer (T033/T034), which knows the caller's identity and the
policy in force; a data-access class that decided permission would be trusting
whoever wrote the calling code.

Import repositories from here rather than from the submodules, so the module
layout stays an implementation detail.
"""

from app.database.repositories.agents import (
    ALIVE_STATUSES,
    MAX_ANCESTOR_DEPTH,
    AgentLogRepository,
    AgentRepository,
)
from app.database.repositories.base import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    Repository,
    UuidRepository,
    apply_limit,
    apply_offset,
    rowcount,
    scalars,
    translate,
)
from app.database.repositories.conversations import (
    ConversationRepository,
    MessageRepository,
)
from app.database.repositories.devices import (
    REACHABLE_STATUSES,
    DeviceEventRepository,
    DeviceRepository,
)
from app.database.repositories.events import EventRepository
from app.database.repositories.identity import (
    AuditLogRepository,
    SessionRepository,
    UserRepository,
)
from app.database.repositories.memories import MemoryRepository
from app.database.repositories.projects import ProjectRepository
from app.database.repositories.schedules import ScheduleRepository
from app.database.repositories.tasks import (
    TaskRepository,
    TaskStepRepository,
    ToolExecutionRepository,
)
from app.database.repositories.usage import ModelUsageRepository

__all__ = [
    "ALIVE_STATUSES",
    "DEFAULT_LIMIT",
    "MAX_ANCESTOR_DEPTH",
    "MAX_LIMIT",
    "REACHABLE_STATUSES",
    "AgentLogRepository",
    "AgentRepository",
    "AuditLogRepository",
    "ConversationRepository",
    "DeviceEventRepository",
    "DeviceRepository",
    "EventRepository",
    "MemoryRepository",
    "MessageRepository",
    "ModelUsageRepository",
    "ProjectRepository",
    "Repository",
    "ScheduleRepository",
    "SessionRepository",
    "TaskRepository",
    "TaskStepRepository",
    "ToolExecutionRepository",
    "UserRepository",
    "UuidRepository",
    "apply_limit",
    "apply_offset",
    "rowcount",
    "scalars",
    "translate",
]
