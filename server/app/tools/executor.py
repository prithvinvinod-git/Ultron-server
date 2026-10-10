"""The tool execution pipeline (T036): §16's stages, in one awaited call.

§16 fixes the order — **schema → permission → policy → (target selection) →
execute → verify → event → result** — and §66.7 fixes who decides: tools
declare, the pipeline decides. This module is that pipeline. A tool never
sees a permission check, never validates its own arguments, and never
decides whether it ran: everything that must happen *around* `execute()`
happens here, in order, once.

Stage by stage:

1. **Schema** — the registry resolves the tool by name (the registry is the
   authority: an instance the caller built is *not* the registered tool, the
   registered one is) and validates the argument mapping against the
   declared JSON Schema. Violations raise ``ToolSchemaInvalidError`` (422)
   with every error listed; nothing downstream runs.

2. **Permission** — a `PermissionRequest` is assembled from the tool's
   declarations (level, node, reversibility §66.17) and the caller's
   context, then `PermissionManager.enforce` decides. Denial (403) and
   confirmation-required (409) raise here; the manager has already written
   the §64.13 audit row. *No tool event is emitted for these*: the tool
   never started, and the audit row is the durable record of the refusal.

3. **Policy** — an injectable veto hook between permission and execution
   (§16's diagram; §59.4's "workspace policy"). It receives the resolved
   tool and the validated arguments and may raise to refuse. It is
   optional: without one the stage is a pass-through, and whatever typed
   error the hook raises propagates unchanged — no events, no execution.

4–5. **Execute + verify** — `execute()` runs under `asyncio.wait_for` with
   the tool's own declared timeout (§59.6: bounded, always declared), then
   `verify()` runs (§17: never assume success). A ``FAILED`` verification
   becomes ``ToolVerificationError`` — deliberately *after* execution,
   because the effect already happened (errors.py's own note).

6. **Event** — `TOOL_STARTED` is published only once every prior stage has
   passed (right before execution), so a strict ``STARTED → COMPLETED``
   (or ``STARTED → FAILED``) pairing brackets every execution that began.
   Failures that never reached execution — schema, permission, policy —
   emit no tool event at all; they are audited or returned as errors.

7. **Result** — a frozen `ToolResult` carries the output, the honest
   verification outcome, the duration, and the node.

Two rules the event payloads obey:

- **Never the arguments.** §59.6/§13: event payloads are observability,
  not a second copy of potentially sensitive input. Payloads carry the
  identifiers (tool, principal, client, node, task), and on completion the
  duration and verification; on failure the error code and a truncated
  message (``UltronError.details`` is documented never to carry secrets).

- **The bus never decides the outcome.** Publishing is best-effort: a
  closed or full bus logs a warning instead of failing a tool that ran —
  observability degrades, results do not.

Timeouts become ``OperationTimeoutError`` (504, retryable) rather than the
generic tool failure, because §59.6 distinguishes "the tool broke" from
"the tool did not answer in time"; any other non-ULTRON exception is
wrapped in ``ToolError`` with the original as ``cause``. A ``UltronError``
the tool raised itself propagates unchanged — §59.6's typed contract.

Deliberately *not* here: the ``tool_executions`` history row (spec §66.16's
persistence task, not this pipeline) and target selection across nodes
(§16's amendment — the stage dispatches node-targeted tools and comes with
its own task; until then ``node`` is a caller parameter that defaults to
the tool's declared scope, then to the cloud server).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from app.core.errors import (
    OperationCancelledError,
    OperationTimeoutError,
    ToolError,
    ToolVerificationError,
    UltronError,
)
from app.core.permissions import PermissionManager
from app.database.models import VerificationOutcome
from app.events.bus import EventBus
from app.events.types import EventType
from app.observability.logging import get_logger
from app.security.audit import ACTOR_USER
from app.security.permissions import PermissionRequest
from app.tools.base import Reversibility, Tool
from app.tools.registry import ToolRegistry

__all__ = ["CLOUD_NODE", "PolicyCheck", "ToolExecutor", "ToolResult"]

_LOGGER = get_logger(__name__)

CLOUD_NODE = "server"
"""§64.6.1/§64.7: the cloud server's node id — a tool that declares no
``node_scope`` (§64.11: ``None`` means the cloud server) runs here."""

#: §16 stage 3: the workspace-policy veto between permission and execution.
#: Receives the resolved tool and its validated arguments; raising refuses
#: the call. Errors it raises are its own contract — the pipeline passes
#: them through untouched, before any event is emitted.
PolicyCheck = Callable[[Tool, Mapping[str, Any]], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ToolResult:
    """§16 stage 7: what a completed execution returns."""

    tool: str
    output: Any
    verification: VerificationOutcome
    duration_ms: float
    node: str


class ToolExecutor:
    """§16's pipeline: the only path from a tool call to a tool result."""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        permissions: PermissionManager,
        events: EventBus,
        policy: PolicyCheck | None = None,
    ) -> None:
        """``policy`` is the optional stage-3 hook; ``None`` passes through.

        ``events`` is required (not optional): §16 makes the event stage part
        of the pipeline, and an executor that could be built without a bus
        would ship executions nothing observes.
        """
        self._registry = registry
        self._permissions = permissions
        self._events = events
        self._policy = policy

    async def run(
        self,
        tool: str | Tool,
        arguments: Mapping[str, Any],
        *,
        principal: str,
        client: str = "api",
        principal_type: str = ACTOR_USER,
        node: str | None = None,
        target: str | None = None,
        operation: str | None = None,
        confirmed: bool = False,
        workspace_authorized: bool = False,
        operation_authorized: bool = False,
        task_id: str | None = None,
        agent_id: str | None = None,
    ) -> ToolResult:
        """Run the full pipeline for one tool call.

        ``tool`` may be the registered name or an instance of a registered
        class; either way the *registry's* instance executes — the registry
        is the authority (§16 stage 1, §66.7).

        ``node`` defaults to the tool's declared scope, then to the cloud
        server; ``operation`` defaults to the tool's name (the common case
        where one tool is one operation). ``confirmed`` /
        ``workspace_authorized`` / ``operation_authorized`` are §64.12's
        confirmation flags; ``task_id`` / ``agent_id`` are forwarded to the
        event envelopes so executions correlate with the work that caused
        them.
        """
        # -- Stage 1: schema (resolve, then validate) ----------------------- #
        name = tool if isinstance(tool, str) else tool.name
        resolved = self._registry.lookup(name)
        validated = self._registry.validate(resolved, arguments)

        # -- Stage 2: permission (§15/§64.12; raises on refusal) ------------ #
        request = PermissionRequest(
            principal=principal,
            node=node or resolved.node_scope or CLOUD_NODE,
            tool=resolved.name,
            operation=operation or resolved.name,
            required_level=resolved.permission_level,
            client=client,
            target=target,
            principal_type=principal_type,
            workspace_authorized=workspace_authorized,
            operation_authorized=operation_authorized,
            confirmed=confirmed,
            irreversible=resolved.reversibility is Reversibility.IRREVERSIBLE,
        )
        await self._permissions.enforce(request)

        # -- Stage 3: policy (optional veto; no events yet) ----------------- #
        if self._policy is not None:
            await self._policy(resolved, validated)

        # -- Stage 4–5: execute (bounded) + verify, bracketed by events ----- #
        base: dict[str, Any] = {
            "tool": resolved.name,
            "principal": principal,
            "client": client,
            "node": request.node,
        }
        await self._publish(
            EventType.TOOL_STARTED,
            base,
            task_id=task_id,
            agent_id=agent_id,
            persist=False,
        )

        started = perf_counter()
        try:
            output = await asyncio.wait_for(
                resolved.execute(validated),
                timeout=resolved.timeout,
            )
            verification = await resolved.verify(output, validated)
            if verification is VerificationOutcome.FAILED:
                raise ToolVerificationError(
                    resolved.name,
                    f"tool '{resolved.name}' reported FAILED verification "
                    "after executing - the effect already happened",
                    details={"verification": verification.value},
                )
        except asyncio.CancelledError:
            # Cancellation is the *caller's* action: re-raised untouched
            # (converting it would break asyncio's structured cancellation),
            # but the STARTED pair still closes — an execution that began
            # must not vanish from observability. Emitting is guarded
            # because we are already in a cancelled task.
            try:
                await self._emit_failure(
                    base,
                    OperationCancelledError(resolved.name),
                    started,
                    task_id,
                    agent_id,
                )
            except BaseException:
                _LOGGER.warning(
                    "TOOL_FAILED not published for cancelled execution",
                    extra={"tool": resolved.name},
                    exc_info=True,
                )
            raise
        except UltronError as exc:
            # A typed error the tool raised itself propagates unchanged
            # (§59.6), including the verification failure raised above.
            await self._emit_failure(base, exc, started, task_id, agent_id)
            raise
        except TimeoutError as exc:
            # asyncio.wait_for's deadline (builtin TimeoutError since 3.11).
            failure: UltronError = OperationTimeoutError(
                resolved.name,
                timeout=resolved.timeout,
                cause=exc,
            )
            await self._emit_failure(base, failure, started, task_id, agent_id)
            raise failure from exc
        except Exception as exc:
            failure = ToolError(
                resolved.name,
                f"tool '{resolved.name}' raised {type(exc).__name__}",
                cause=exc,
            )
            await self._emit_failure(base, failure, started, task_id, agent_id)
            raise failure from exc

        duration_ms = (perf_counter() - started) * 1000.0
        await self._publish(
            EventType.TOOL_COMPLETED,
            {
                **base,
                "duration_ms": round(duration_ms, 3),
                "verification": verification.value,
            },
            task_id=task_id,
            agent_id=agent_id,
            persist=True,
        )

        # -- Stage 7: result ------------------------------------------------ #
        return ToolResult(
            tool=resolved.name,
            output=output,
            verification=verification,
            duration_ms=duration_ms,
            node=request.node,
        )

    # ---------------------------------------------------------------------- #
    # Internals
    # ---------------------------------------------------------------------- #
    async def _emit_failure(
        self,
        base: Mapping[str, Any],
        error: UltronError,
        started: float,
        task_id: str | None,
        agent_id: str | None,
    ) -> None:
        """Publish the paired ``TOOL_FAILED`` (§19: failures are events too).

        The message is truncated: it is observability, and while
        ``UltronError.details`` is documented never to carry secrets, the
        payload stays identifiers-plus-code by the module's second rule.
        """
        await self._publish(
            EventType.TOOL_FAILED,
            {
                **base,
                "duration_ms": round((perf_counter() - started) * 1000.0, 3),
                "error_code": error.code.value,
                "error": error.message[:500],
            },
            task_id=task_id,
            agent_id=agent_id,
            persist=True,
        )

    async def _publish(
        self,
        event_type: EventType,
        payload: Mapping[str, Any],
        *,
        task_id: str | None,
        agent_id: str | None,
        persist: bool,
    ) -> None:
        """Best-effort publish: a broken bus must not change the outcome.

        ``TOOL_STARTED``/``TOOL_COMPLETED``/``TOOL_FAILED`` pairing still
        holds while the bus is healthy; when it is not, the warning *is* the
        signal — §16's event stage observes executions, it does not gate
        them once execution has begun.
        """
        try:
            await self._events.publish(
                event_type,
                payload,
                task_id=task_id,
                agent_id=agent_id,
                persist=persist,
            )
        except Exception:
            _LOGGER.warning(
                "tool event not published",
                extra={"event_type": event_type.value},
                exc_info=True,
            )
