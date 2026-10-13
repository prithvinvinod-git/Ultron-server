"""The browser agent (T113): §11's worker over the ``browser.*`` tool family.

§11 names a Playwright-backed browser agent whose capabilities are exactly the
tool surface T112 already built — create a session, navigate, click, type,
select, inspect, extract, screenshot, download, cookies/session handling, close —
and states the one structural rule that keeps it safe: **"Browser sessions must
be isolated by agent."** That isolation is not this agent's to implement; it is
enforced underneath by :class:`~app.tools.browser.session.BrowserSessionManager`,
which keys one session per agent id. This agent is the *worker* that drives the
model through those tools.

The shape mirrors :class:`~app.agents.coding.CodingAgent` (§8/§9): an agent is a
*declaration* plus one ``run``, and it is given its collaborators rather than
naming them. Where the coding agent is handed a :class:`CodingEngine`, this one
is handed the model-driven cycle T068 already owns —
:class:`~app.agents.loop.ToolCallingLoop` — pre-advertised with the ``browser.*``
tool definitions:

```text
BrowserAgent #12  ->  ToolCallingLoop  ->  browser.*  ->  BrowserSession #12
```

The loop is built once, from the injected router (T066/T067) and the §16
pipeline (:class:`~app.tools.executor.ToolExecutor`), so a browser run is exactly
the same governed path every other tool call takes: schema → permission → policy
→ execution → verification → event. The agent never imports Playwright and never
holds a page — Playwright belongs to the runtime (T111), and the session belongs
to the manager.

**The agent id is the session key.** The browser tools resolve the *ambient*
agent id (bound by the executor into the correlation context) and ask the manager
for that agent's session. `run` therefore passes ``agent_id=str(self.agent_id)``
to the loop for every call, which is what makes §11's isolation hold all the way
down: the worker that runs and the session it browses are one agent.

`run(context)` is a single browsing goal:

- ``prompt`` (required) — the instruction the model receives.
- ``task_id`` (optional) — the owning task's id, carried into the loop and every
  event; defaults to the agent's own task id, then its agent id.
- ``principal`` (optional) — who the tool executions are attributed to (§15);
  defaults to the agent itself.

It returns a plain report — the model's answer, why the loop stopped, and how
much work it did — never a claim. Failure discipline mirrors the runtime (T044):
a typed :class:`UltronError` is re-raised untouched so its code survives, and
anything unexpected is wrapped in ``AgentError``. The agent does **not** drive its
own :class:`~app.agents.base.AgentStatus` lifecycle — the manager owns the
``agents`` row and its ``AGENT_*`` events.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.agents.base import Agent
from app.agents.loop import ToolCallingLoop, ToolLoopResult
from app.core.errors import AgentError, InvalidInputError, UltronError
from app.database.models import MessageRole
from app.events.bus import EventBus
from app.events.types import EventType
from app.models.base import ModelMessage, ToolDefinition
from app.models.router import ModelRouter
from app.tools.browser.session import BrowserSessionManager
from app.tools.executor import ToolExecutor

__all__ = ["BrowserAgent"]

#: The default system prompt: what the worker is and which tools it may use.
#: §11's browser tools are the only ones advertised, so the instruction can be
#: concrete about the DOM-tool surface without naming a specific model.
_DEFAULT_SYSTEM_PROMPT = (
    "You are ULTRON's browser agent. Accomplish the user's goal using only the "
    "provided browser.* tools: open a URL, then inspect or extract its content, "
    "click and type to interact, and take screenshots or downloads when asked. "
    "The tools operate on your own isolated browser session. When the goal is "
    "met — or cannot be met — stop calling tools and answer with a concise report "
    "of what you found or did."
)


class BrowserAgent(Agent):
    """Drive a per-agent Playwright browser to complete a browsing goal (§11)."""

    agent_type = "browser.agent"
    description = "Browse the web through the isolated browser.* tools and report what it found."

    def __init__(
        self,
        *,
        router: ModelRouter,
        executor: ToolExecutor,
        tools: Sequence[ToolDefinition],
        sessions: BrowserSessionManager | None = None,
        bus: EventBus | None = None,
        capability: str = "default",
        max_iterations: int = 10,
        max_tool_calls: int = 32,
        system_prompt: str | None = None,
        close_session: bool = False,
        **agent_kwargs: Any,
    ) -> None:
        """Wire the agent to the model router and the §16 pipeline.

        ``tools`` are the browser tool *definitions* advertised to the model; a
        browser agent with none is a misconfiguration and is refused at
        construction. ``sessions`` is optional and used only when
        ``close_session`` is set: the manager owns session lifetime, and a
        multi-step task reuses one agent (and therefore one session) across
        steps, so the default is to leave it to the manager's eviction.

        All §6 assignment kwargs (``name``, ``task_id``, ``model``, …) pass
        through to :class:`~app.agents.base.Agent`.
        """
        super().__init__(**agent_kwargs)
        definitions = tuple(tools)
        if not definitions:
            raise ValueError(
                "a browser agent needs at least one browser tool definition to advertise"
            )
        self._sessions = sessions
        self._bus = bus
        self._close_session = close_session
        self._loop = ToolCallingLoop(
            router=router,
            executor=executor,
            tools=definitions,
            capability=capability,
            max_iterations=max_iterations,
            max_tool_calls=max_tool_calls,
            system_prompt=system_prompt or _DEFAULT_SYSTEM_PROMPT,
        )

    async def run(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        """Perform one browsing task over ``context`` and return its report."""
        try:
            return await self._run(context)
        except UltronError:
            raise
        except Exception as exc:
            raise AgentError(
                str(self.agent_id),
                "browser agent failed unexpectedly",
                details={"agent_type": self.agent_type},
                cause=exc,
            ) from exc

    # -- the run ------------------------------------------------------------ #
    async def _run(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        prompt = _require_text(context, "prompt")
        task_id = _task_id(context) or _own_task_id(self)
        principal = _optional_text(context, "principal") or str(self.agent_id)

        await self._progress(
            task_id,
            {"task_id": task_id, "phase": "browse", "goal": prompt[:500]},
        )
        try:
            result = await self._loop.run(
                (ModelMessage(role=MessageRole.USER, content=prompt),),
                principal=principal,
                client="agent",
                task_id=task_id,
                agent_id=str(self.agent_id),
            )
        finally:
            if self._close_session and self._sessions is not None:
                await self._sessions.release(str(self.agent_id))

        await self._progress(
            task_id,
            {
                "task_id": task_id,
                "phase": "report",
                "stop_reason": result.stop_reason.value,
                "iterations": result.iterations,
                "tool_calls": result.tool_calls,
            },
        )
        return self._report(task_id, prompt, result)

    def _report(
        self,
        task_id: str,
        prompt: str,
        result: ToolLoopResult,
    ) -> dict[str, Any]:
        return {
            "agent": self.agent_type,
            "task_id": task_id,
            "goal": prompt,
            "answer": result.content,
            "completed": result.completed,
            "stop_reason": result.stop_reason.value,
            "iterations": result.iterations,
            "tool_calls": result.tool_calls,
        }

    async def _progress(self, task_id: str, payload: Mapping[str, Any]) -> None:
        """Publish one ephemeral ``TASK_PROGRESS`` (no bus: nowhere to publish)."""
        if self._bus is None:
            return
        await self._bus.publish(
            EventType.TASK_PROGRESS,
            payload,
            task_id=task_id,
            agent_id=str(self.agent_id),
            persist=False,
        )


# --------------------------------------------------------------------------- #
# Context helpers (stateless, mirroring the coding agent's split)
# --------------------------------------------------------------------------- #
def _require_text(context: Mapping[str, Any], key: str) -> str:
    value = context.get(key)
    if not isinstance(value, str) or not value.strip():
        raise InvalidInputError(
            f"a browser task needs a {key}",
            details={key: str(value)[:200]},
        )
    return value.strip()


def _optional_text(context: Mapping[str, Any], key: str) -> str | None:
    value = context.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _task_id(context: Mapping[str, Any]) -> str | None:
    value = context.get("task_id")
    return str(value) if value else None


def _own_task_id(agent: Agent) -> str:
    """The agent's task id, then its agent id — the loop's own fallback (§6)."""
    return str(agent.task_id) if agent.task_id is not None else str(agent.agent_id)
