"""The agent type registry (T043): one place the manager spawns from.

§7 defines the runtime as "registry, manager, lifecycle" and demands
"create agents" without naming implementations; §40 forbids hard-coding the
agent types into the core — *the kind of agent is declared on the agent*. This
module is that contract made concrete, the analog of `ToolRegistry` (T035) for
agents, with one deliberate difference that mirrors the subject matter:

- Tools are behaviour without history, so the tool registry holds *instances*
  (`register(Tool())`), ready to execute on demand.
- Agents carry lifecycle state (§6: `status`, `task_id`, per-instance
  `tools`/`permissions`/assignment), so this registry holds **classes** — the
  concrete `Agent` subclasses that survive `app/agents/base.py`'s
  definition-time guards — and `create` is the spawn seam the manager (T044)
  uses to build a fresh, CREATED instance from one.

Rules, mirroring T035 for the same reasons:

- **`register`** refuses a non-`Agent` class and an *abstract* class (a type
  nobody can instantiate is not a spawnable agent), and a duplicate
  `agent_type` (`ConflictError`, 409): the type name is what appears in the
  agent row, in `AGENT_*` events and in audit history (§19), so a silent
  overwrite would rewrite the past — the exact argument §59.6 makes for tool
  names.
- **`lookup` / `__contains__` / `list`** — retrieval, `list()` sorted by
  `agent_type` for the stable ordering the agent router's prompts need.
  Unknown types raise `AgentTypeNotFoundError` (404) — never `KeyError`, which
  would surface as a 500 for a caller's typo.
- **`create(agent_type, **kwargs)`** — §7's "create agents" as the type-level
  operation: resolve the class and call its constructor with the §6 assignment
  kwargs (`name`, `task_id`, `model`, `tools`, `permissions`,
  `memory_namespace`, `agent_id`) that `Agent.__init__` accepts. The instance
  starts CREATED, exactly as §6's lifecycle begins.

Synchronous by design, like T035: wiring happens once at startup, lookups are
dict reads, and there is no I/O to await. No concrete agent is imported here —
nothing in this module mentions coding, browser, system or any other type; the
container wires in what §59.22's inventory is actually built with (T045+).
"""

from __future__ import annotations

import inspect
from typing import Any

from app.agents.base import Agent
from app.core.errors import AgentTypeNotFoundError, ConflictError

__all__ = ["AgentRegistry"]


class AgentRegistry:
    """In-memory registry of concrete agent classes, keyed by their §6
    `agent_type` declaration."""

    def __init__(self) -> None:
        self._types: dict[str, type[Agent]] = {}

    # ------------------------------------------------------------------ #
    # Registration
    # ------------------------------------------------------------------ #
    def register(self, agent_cls: type[Agent]) -> None:
        """Add one agent type. Idempotent per *identity* of the type name only:
        a second registration of the same name is a bug, not a reload."""
        if not inspect.isclass(agent_cls) or not issubclass(agent_cls, Agent):
            raise TypeError(
                f"{getattr(agent_cls, '__name__', repr(agent_cls))} is not an Agent subclass; "
                "register concrete agents that declare an agent_type (spec 6/40)"
            )
        if inspect.isabstract(agent_cls):
            raise TypeError(
                f"{agent_cls.__name__} is abstract and cannot be spawned; "
                "register a concrete agent with an implemented run (spec 6)"
            )
        if agent_cls.agent_type in self._types:
            raise ConflictError(
                f"agent type '{agent_cls.agent_type}' is already registered",
                details={"agent_type": agent_cls.agent_type},
            )
        self._types[agent_cls.agent_type] = agent_cls

    # ------------------------------------------------------------------ #
    # Retrieval
    # ------------------------------------------------------------------ #
    def lookup(self, agent_type: str) -> type[Agent]:
        """The registered agent class, or `AgentTypeNotFoundError` (§6: the
        type name is the contract — a miss is a 404 with the name, not a
        KeyError 500)."""
        try:
            return self._types[agent_type]
        except KeyError:
            raise AgentTypeNotFoundError(agent_type) from None

    def __contains__(self, agent_type: object) -> bool:
        return isinstance(agent_type, str) and agent_type in self._types

    def list(self) -> list[type[Agent]]:
        """Every registered agent type, sorted by `agent_type` for a stable
        inventory (the router's prompt, the API's type list)."""
        return [self._types[name] for name in sorted(self._types)]

    # ------------------------------------------------------------------ #
    # Spawning (§7's "create agents", at the type level)
    # ------------------------------------------------------------------ #
    def create(self, agent_type: str, **kwargs: Any) -> Agent:
        """Instantiate the registered type as a fresh, CREATED agent.

        ``kwargs`` are the §6 assignment attributes `Agent.__init__` accepts
        (``name``, ``task_id``, ``model``, ``tools``, ``permissions``,
        ``memory_namespace``, ``agent_id``). The instance's ``agent_type`` is
        the class's declaration — it is not a per-instance kwarg, and is never
        taken from the caller.
        """
        return self.lookup(agent_type)(**kwargs)
