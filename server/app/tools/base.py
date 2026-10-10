"""The Tool ABC (T034): what every capability in ULTRON is, and nothing more.

Spec §14 fixes the common interface every tool must implement — `name`,
`description`, `input_schema`, `permission_level`, `execute()`, `verify()` —
and §40 keeps the concepts apart: TOOL is *capability*, not agent, not task,
not orchestration. §59.6 makes `timeout` part of every registered tool and
requires the call to map to a typed error from `app/core/errors.py`; §66.17
adds the reversibility declaration every tool must make.

The governing rule across all of them is §66.7's: **tools declare, the
pipeline decides.** This class is therefore a *data contract plus two
behaviours*:

- **Declared, never computed** — name, schema, required permission level,
  timeout, reversibility. The ABC contains no permission logic of its own:
  whether a call is allowed is `PermissionManager`'s answer (T033), and the
  pipeline (T036) is what refuses a tool that would skip that check. A tool
  that grew a "actually I'm safe, skip the check" method would be a bypass
  with a docstring.

- **`execute()`** does the work, taking the already schema-validated argument
  mapping (§16 stage 1 belongs to the pipeline, not the tool) and raising a
  typed error from `app/core/errors.py` on failure (§59.6's error-handling
  row). It is abstract: a capability that cannot execute is not a tool.

- **`verify()`** is §17's "never assume success". The default does **not**
  claim one — it returns `VerificationOutcome.UNVERIFIED`, the honest answer
  for a tool that has no post-condition to check. A tool that *can* inspect
  the world (exit codes, page state, service health) overrides it; one that
  silently returned SUCCESS would turn §17 into decoration.

Declarations are enforced at class-definition time (``__init_subclass__``)
rather than at first call: a concrete tool missing a required field, a
non-positive timeout, or a non-coroutine ``execute`` raises ``TypeError`` at
import, where the mistake belongs. Abstract intermediate classes are exempt —
they are works in progress, not registrations.

Deliberately *not* here: `output_schema`, `node_requirements`, `risk_level`,
`availability`, `version` are §66.14's additions, assigned to the registry
work (T390); `category` is derived from the name prefix (``filesystem.read``
→ ``filesystem``) by the registry (T035), not stored twice.
"""

from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from collections.abc import Mapping
from enum import StrEnum
from typing import Any, ClassVar

from app.database.models import PermissionLevel, VerificationOutcome

__all__ = ["Reversibility", "Tool"]


class Reversibility(StrEnum):
    """§66.17: every tool declares `reversible | partially_reversible |
    irreversible`, and the registry shows it before execution.

    Irreversible is the strong-confirmation case in §66.17/§66.7 — the
    permission engine's ``irreversible`` flag is fed from this declaration,
    which is why there is **no default**: a tool that has not said which it is
    cannot be registered, rather than silently inheriting the safe-looking
    answer.
    """

    REVERSIBLE = "reversible"
    PARTIALLY_REVERSIBLE = "partially_reversible"
    IRREVERSIBLE = "irreversible"


class Tool(ABC):
    """A single capability: declared metadata, one execution, one check."""

    # -- §14 / §59.6 declarations, all mandatory on a concrete tool --------- #
    name: ClassVar[str]
    """Unique and stable: it appears in events and audit rows (§59.6)."""
    description: ClassVar[str]
    """What the agent reads to decide whether to choose this tool (§59.6)."""
    input_schema: ClassVar[dict[str, Any]]
    """JSON Schema, validated by the pipeline *before* execution (§16 stage 1)."""
    permission_level: ClassVar[PermissionLevel]
    """§15's level for this tool; enforced by the pipeline, never by the tool."""
    timeout: ClassVar[float]
    """Seconds. §59.6: bounded and always declared — no tool may run unbounded."""
    reversibility: ClassVar[Reversibility]
    """§66.17 declaration, surfaced by the registry before execution."""

    # -- Declarations with honest defaults ---------------------------------- #
    node_scope: ClassVar[str | None] = None
    """§64.11: which node executes this tool. ``None`` means the cloud server —
    the target-selection stage (§16 amendment) dispatches node-targeted tools,
    and tools that do not declare a node run where they always ran."""
    audit: ClassVar[bool] = False
    """§59.6: whether the call is an audited *sensitive* operation beyond the
    permission check §15 already logs for every execution."""

    @abstractmethod
    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        """Perform the capability on schema-validated ``arguments``.

        Raise a typed error from ``app/core/errors.py`` on failure (§59.6);
        the pipeline catches, records and reports it. Returning normally means
        the work happened — success itself is never assumed (§17), which is
        what :meth:`verify` is for.
        """

    async def verify(self, result: Any, arguments: Mapping[str, Any]) -> VerificationOutcome:
        """§17: check that the world actually reached the intended state.

        The default asserts nothing and reports ``UNVERIFIED`` — the honest
        outcome for a tool with no post-condition. Overriding tools check
        something real: an exit code, a page's expected state, a service's
        health. There is deliberately no default that says SUCCESS.
        """
        return VerificationOutcome.UNVERIFIED

    # ---------------------------------------------------------------------- #
    # Definition-time guards
    # ---------------------------------------------------------------------- #
    #: Fields §14/§59.6/§66.17 require every concrete tool to declare.
    _REQUIRED_DECLARATIONS: ClassVar[tuple[str, ...]] = (
        "name",
        "description",
        "input_schema",
        "permission_level",
        "timeout",
        "reversibility",
    )

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Fail at import when a concrete tool is mis-declared (§14, §59.6).

        Skipped for classes that remain abstract: an intermediate ABC is not
        registrable, so its declarations are not yet due. For everything else
        each required field must appear somewhere in the class's own lineage
        (``Tool`` itself declares none), so one family base may declare on
        behalf of its family — but no tool may ship *undeclared*.
        """
        super().__init_subclass__(**kwargs)
        if inspect.isabstract(cls):
            return

        lineage = tuple(klass for klass in cls.mro() if klass is not Tool)
        not_own = [
            field
            for field in cls._REQUIRED_DECLARATIONS
            if not any(field in klass.__dict__ for klass in lineage)
        ]
        if not_own:
            raise TypeError(
                f"{cls.__name__} must declare {', '.join(not_own)} "
                "(spec 14/59.6/66.17: every tool declares; the pipeline decides)"
            )

        timeout = getattr(cls, "timeout", None)
        if not isinstance(timeout, (int, float)) or timeout <= 0:
            raise TypeError(
                f"{cls.__name__}.timeout must be a positive number of seconds "
                "(spec 59.6: bounded, always declared)"
            )

        level = getattr(cls, "permission_level", None)
        if not isinstance(level, PermissionLevel):
            raise TypeError(
                f"{cls.__name__}.permission_level must be a PermissionLevel "
                "(spec 15: the only permission scale)"
            )

        reversibility = getattr(cls, "reversibility", None)
        if not isinstance(reversibility, Reversibility):
            raise TypeError(
                f"{cls.__name__}.reversibility must be a Reversibility "
                "(spec 66.17: reversible | partially_reversible | irreversible)"
            )

        if not inspect.iscoroutinefunction(getattr(cls, "execute", None)):
            # A sync ``execute`` would pass most type checkers (it returns
            # Any) and then explode on the pipeline's first ``await``.
            raise TypeError(
                f"{cls.__name__}.execute must be an async function "
                "(spec 16: the pipeline awaits every execution)"
            )
