"""The computer agent (T144): §13's worker over the ``computer.*`` tool family.

§13 draws the Computer Node architecture — ``ULTRON SERVER → Computer Agent →
Computer Node Gateway → Windows laptop`` — and §42 the future of computer
control. The gateway (T141) and the tools (T143) are the transport and the
capability surface; this agent is the **worker** that drives a model through
them, exactly as :class:`~app.agents.browser.BrowserAgent` drives the
``browser.*`` family (§11).

The shape mirrors the other workers: an agent is a *declaration* plus one
``run``, handed its collaborators rather than naming them. The model-driven
cycle is :class:`~app.agents.loop.ToolCallingLoop` (T068), pre-advertised with
the ``computer.*`` tool definitions, so a computer run is the same governed
path every tool call takes (§16: schema → permission → policy → execution →
verification → event). The agent never imports a Windows API and never holds a
node connection — Windows belongs to the node, the connection to the gateway.

**The fallback chain is planned here** (T145, §66.10). Before the model acts,
the agent resolves the target node's advertised capabilities (§64.10), expands
them into interaction methods (:func:`~app.agents.computer.fallback.
methods_for_capabilities`), and builds the ordered plan
(:func:`~app.agents.computer.fallback.build_plan`) — API → DOM → UIA →
shortcuts → mouse → vision. The plan *guides* the model and is reported, but it
is executed on the node: §64.6.5's "the node executes tools, the Core reasons"
means the server never picks a pixel. A node is resolved here only to make the
plan honest; the tools re-resolve and dispatch, and a node that is offline
becomes a typed ``NODE_UNAVAILABLE`` when a tool runs (§64.11), never a hang.

`run(context)` is a single computer goal:

- ``prompt`` (required) — the instruction the model receives.
- ``target`` (optional) — the node id or kind to plan against; the tools may
  still choose another via their own ``node`` argument.
- ``methods`` (optional) — an explicit available-method override (tests, or a
  caller that knows the node); otherwise the target's capabilities are used.
- ``task_id`` (optional) — the owning task's id, carried into the loop and every
  event; defaults to the agent's own task id, then its agent id.
- ``principal`` (optional) — who the tool executions are attributed to (§15).

It returns a plain report — the model's answer, the fallback plan, why the loop
stopped and how much work it did — never a claim. Failure discipline mirrors the
other workers: a typed :class:`UltronError` is re-raised untouched, and anything
unexpected is wrapped in ``AgentError``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.agents.base import Agent
from app.agents.computer.fallback import (
    DEFAULT_PRIORITY,
    InteractionMethod,
    build_plan,
    methods_for_capabilities,
)
from app.agents.computer.gateway import NodeGateway
from app.agents.loop import ToolCallingLoop, ToolLoopResult
from app.config.settings import ComputerNodeSettings
from app.core.errors import AgentError, InvalidInputError, UltronError
from app.database.models import MessageRole
from app.events.bus import EventBus
from app.events.types import EventType
from app.models.base import ModelMessage, ToolDefinition
from app.models.router import ModelRouter
from app.tools.executor import ToolExecutor

__all__ = ["ComputerAgent"]

#: The default system prompt: what the worker is and which tools it may use.
_DEFAULT_SYSTEM_PROMPT = (
    "You are ULTRON's computer agent. Accomplish the user's goal on the target "
    "computer using only the provided computer.* tools. Observe first "
    "(computer.list_windows, computer.screenshot), prefer the most reliable "
    "interaction method available, and after each action verify the expected "
    "state actually changed. Prefer structured interaction over blind pointer "
    "movement. When the goal is met — or cannot be met — stop calling tools and "
    "answer with a concise report of what you did and what you observed."
)


class ComputerAgent(Agent):
    """Drive a model through the ``computer.*`` tools to complete a desktop goal (§13/§42)."""

    agent_type = "computer.agent"
    description = "Operate a computer node through the computer.* tools and report what it did."

    def __init__(
        self,
        *,
        router: ModelRouter,
        executor: ToolExecutor,
        tools: Sequence[ToolDefinition],
        gateway: NodeGateway | None = None,
        settings: ComputerNodeSettings | None = None,
        bus: EventBus | None = None,
        capability: str = "default",
        max_iterations: int = 12,
        max_tool_calls: int = 48,
        target: str | None = None,
        require_method: str | None = None,
        system_prompt: str | None = None,
        **agent_kwargs: Any,
    ) -> None:
        """Wire the agent to the model router and the §16 pipeline.

        ``tools`` are the ``computer.*`` tool *definitions* advertised to the
        model; a computer agent with none is a misconfiguration and is refused at
        construction. ``gateway`` is optional and used only to plan against a
        target node's live capabilities; without it the plan is built from the
        full configured method set, and the tools still dispatch.

        All §6 assignment kwargs (``name``, ``task_id``, ``model``, …) pass
        through to :class:`~app.agents.base.Agent`.
        """
        super().__init__(**agent_kwargs)
        definitions = tuple(tools)
        if not definitions:
            raise ValueError(
                "a computer agent needs at least one computer tool definition to advertise"
            )
        self._gateway = gateway
        self._settings = settings or ComputerNodeSettings()
        self._bus = bus
        self._target = target
        self._require_method = require_method
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
        """Perform one computer task over ``context`` and return its report."""
        try:
            return await self._run(context)
        except UltronError:
            raise
        except Exception as exc:
            raise AgentError(
                str(self.agent_id),
                "computer agent failed unexpectedly",
                details={"agent_type": self.agent_type},
                cause=exc,
            ) from exc

    # -- the run ------------------------------------------------------------ #
    async def _run(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        prompt = _require_text(context, "prompt")
        task_id = _task_id(context) or _own_task_id(self)
        principal = _optional_text(context, "principal") or str(self.agent_id)
        target = _optional_text(context, "target") or self._target
        require = _optional_text(context, "require_method") or self._require_method

        available, resolved = self._available_methods(context, target)
        plan = build_plan(
            prompt,
            available,
            priority=self._settings.capability_priority,
            require=require,
        )

        await self._progress(
            task_id,
            {
                "task_id": task_id,
                "phase": "plan",
                "target": resolved,
                "selected": plan.selected.value if plan.selected is not None else None,
                "methods": [method.value for method in plan.available_methods],
            },
        )

        message = ModelMessage(
            role=MessageRole.USER,
            content=_with_plan(prompt, plan),
        )
        result = await self._loop.run(
            (message,),
            principal=principal,
            client="agent",
            task_id=task_id,
            agent_id=str(self.agent_id),
        )

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
        return self._report(task_id, prompt, target, plan.to_dict(), result)

    # -- helpers ------------------------------------------------------------ #
    def _available_methods(
        self, context: Mapping[str, Any], target: str | None
    ) -> tuple[set[InteractionMethod], str | None]:
        """The methods to plan against, and the node id they came from (if any).

        An explicit ``methods`` list on the context wins (a caller that knows the
        node). Otherwise, when a gateway and a target node are present, the
        node's advertised capabilities are expanded into methods. When neither is
        available the full configured set is used: the plan is guidance, and the
        tools remain the authority on whether a method actually runs.
        """
        explicit = context.get("methods")
        resolved: str | None = None
        if self._gateway is not None and target:
            node = self._gateway.maybe(target)
            if node is None:
                for candidate in self._gateway.list_nodes():
                    if candidate.node_type.value == target:
                        node = candidate
                        break
            if node is not None:
                resolved = node.node_id
                if _has_items(explicit):
                    return _method_set(explicit), resolved
                return methods_for_capabilities(node.capabilities), resolved
        if _has_items(explicit):
            return _method_set(explicit), resolved
        return set(DEFAULT_PRIORITY), resolved

    def _report(
        self,
        task_id: str,
        prompt: str,
        target: str | None,
        fallback: Mapping[str, Any],
        result: ToolLoopResult,
    ) -> dict[str, Any]:
        return {
            "agent": self.agent_type,
            "task_id": task_id,
            "goal": prompt,
            "target": target,
            "fallback": dict(fallback),
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
# Context helpers (stateless, mirroring the other workers' split)
# --------------------------------------------------------------------------- #
def _require_text(context: Mapping[str, Any], key: str) -> str:
    value = context.get(key)
    if not isinstance(value, str) or not value.strip():
        raise InvalidInputError(
            f"a computer task needs a {key}",
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


def _has_items(value: Any) -> bool:
    return isinstance(value, (list, tuple, set, frozenset)) and len(value) > 0


def _method_set(value: Any) -> set[InteractionMethod]:
    """Coerce an explicit methods override into a method set (typed errors)."""
    methods: set[InteractionMethod] = set()
    for item in value:
        if isinstance(item, InteractionMethod):
            methods.add(item)
            continue
        if not isinstance(item, str):
            raise InvalidInputError(
                "methods must contain interaction method names",
                details={"method": repr(item)[:100]},
            )
        try:
            methods.add(InteractionMethod(item))
        except ValueError as exc:
            raise InvalidInputError(
                f"unknown interaction method '{item}'",
                details={"allowed": [method.value for method in InteractionMethod]},
            ) from exc
    return methods


def _with_plan(prompt: str, plan: Any) -> str:
    """Append the fallback plan to the user message as guidance (§66.10).

    The plan is advisory to the model — it names the preferred method and the
    order of fallbacks — because the *node* executes the chain (§64.6.5). The
    server states the preference; it never picks a pixel.
    """
    ordered = plan.available_methods
    if not ordered:
        return prompt
    lead = plan.selected.value if plan.selected is not None else ordered[0].value
    chain = " → ".join(method.value for method in ordered)
    return (
        f"{prompt}\n\nInteraction plan: prefer '{lead}'. "
        f"Fallback order: {chain}."
    )
