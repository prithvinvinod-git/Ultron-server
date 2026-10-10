"""The Planner (T048): decompose a goal into a validated task graph.

Spec §5 lists "create tasks" among the Core's responsibilities and §50 draws the
pipeline as ``… → ULTRON CORE → INTENT → PLAN → TASK GRAPH → …``, so between
routing a request (T047) and handing work to an agent there is a PLAN stage that
turns a goal into the steps that reach it. §18 is the shape those steps take —
*"support task graphs"*, the worked Research → Implementation → Testing →
Verification → Report — and §66.4 (formalising §1/§17/§18) is the constraint
this module is written to: the planner supports single-step, multi-step,
dependent and parallel-where-safe steps, retries, replanning, verification,
human confirmation, cancellation and pause/resume **as graph operations on the
existing task DAG (T040/T041), not as an isolated planner agent.** The last
clause decides the whole design — this is a *builder of graphs*, not a
participant in them.

What it is:

*   **Pure.** No session, no bus, no `asyncio`. It builds a :class:`Plan` — the
    goal plus a DAG of :class:`PlanStep`\\ s — from values the caller hands it,
    the same arrangement as `app/tasks/graph.py`, `app/tasks/state.py` and
    `app/security/permissions.py`. Persisting the plan (a `tasks` row and
    `task_steps` rows) is orchestration, not planning: the orchestrator (T049)
    materialises the plan through `TaskManager` (T039), exactly as the graph
    docstring records (*"constructed (T048's planner)"*, then loaded back by
    T041). This module never imports a repository or a session.
*   **Rules are declared, not invented.** Phase 2 has no model to decompose
    with, so exactly as T047 declares *routing* rules rather than classifying
    with an LLM, a :class:`Planner` holds ordered caller-supplied
    :class:`PlanStrategy`\\ s. A strategy says *when* it applies (``matches``)
    and *how* it decomposes (`decompose`); the planner contributes order, the
    fallback, and validation — never a hidden heuristic.
*   **Deterministic.** Strategies run lowest `priority` first, ties in
    registration order (a stable sort), and the first match decomposes the goal;
    a strategy with no matcher always matches (the declared catch-all). The same
    declarations always yield the same plan.
*   **Validated once, by the one detector.** A plan is checked when it is built:
    a malformed step, a duplicate key, a dependency on a key that is not in the
    plan, and any cycle are refused as `InvalidInputError` (422). The cycle
    check is **T040's**: the plan's keys are mapped to UUIDs and handed to
    :class:`~app.tasks.graph.StepGraph`, so there is a single cycle detector in
    the codebase and the planner cannot disagree with the executor about what a
    legal graph is. `Plan.graph()` hands back that very `StepGraph`, which is
    also what gives `topological_order()` and `waves()` (§66.4's
    "parallel-where-safe" batches) without re-deriving them.

What it deliberately does not do:

*   **No decomposition intelligence.** There is no model in Phase 2 (T066), so
    planning is declarations all the way down — exactly as T047 says of intents.
    The trivial case is built in: when no strategy matches, a goal is at least
    one step, so the plan is that single step (the identity decomposition). The
    planner does not invent structure it cannot justify.
*   **No agent or model selection** — §66.4 puts that *after* the task graph,
    and it is T049's job over the T043 registry.
*   **No retry/replan policy, verification or confirmation.** §66.4 lists them,
    but they act *on* a live graph and its rows (§66.11); that is T041's
    executor and T050's Core-level policy, not the builder.
*   **No persistence.** Materialising a plan into `tasks`/`task_steps` rows is
    orchestration (T049) through `TaskManager`, which is where ids, positions
    and `depends_on` strings are assigned as rows.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from app.core.errors import ConflictError, InvalidInputError
from app.tasks.graph import StepGraph, StepNode

__all__ = [
    "Plan",
    "PlanMatcher",
    "PlanStep",
    "PlanStrategy",
    "Planner",
    "PlanningRequest",
]

#: A fixed namespace so a plan-local key always maps to the same UUID across
#: runs: the plan's own keys ("research", "verify") are what a caller writes,
#: and the graph (T040) speaks UUIDs — this is the one bridge between them.
_PLAN_NAMESPACE = uuid.UUID("5f0e6b9a-1c2d-4e3f-8a7b-9c0d1e2f3a4b")

#: The step name a strategy's plan gets when no strategy matches at all: the
#: goal itself as a single unit of work (the identity decomposition).
_TRIVIAL_KEY = "step"


@dataclass(frozen=True, slots=True)
class PlanStep:
    """One planned step: a plan-local key, a name, and the keys it waits for.

    The key is the identity *inside the plan* and what `depends_on` refers to;
    it is not a database id (the materialiser assigns those). Kept deliberately
    free of agent, tool and model fields: choosing those is §66.4's *next* stage
    (T049), and a hint nothing consumes is a promise the planner cannot keep.
    """

    key: str
    name: str
    description: str = ""
    depends_on: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or not self.key.strip():
            raise InvalidInputError(
                "a plan step needs a non-empty key",
                details={"key": str(self.key)[:100]},
            )
        if not isinstance(self.name, str) or not self.name.strip():
            raise InvalidInputError(
                "a plan step needs a non-empty name",
                details={"key": self.key, "name": str(self.name)[:100]},
            )
        if not isinstance(self.description, str):
            raise InvalidInputError(
                "a plan step's description must be text",
                details={"key": self.key, "description": type(self.description).__name__},
            )
        if isinstance(self.depends_on, str):
            # A bare string would iterate into characters and look like a
            # dependency on "r", "e", … — a silent misreading, not a value.
            raise InvalidInputError(
                "depends_on must be a sequence of step keys, not a string",
                details={"key": self.key},
            )
        try:
            dependencies = tuple(self.depends_on)
        except TypeError as error:
            raise InvalidInputError(
                "a plan step's dependencies must be a sequence of keys",
                details={"key": self.key, "depends_on": type(self.depends_on).__name__},
            ) from error
        for dep in dependencies:
            if not isinstance(dep, str) or not dep.strip():
                raise InvalidInputError(
                    "a plan step's dependencies are step keys",
                    details={"key": self.key, "dependency": str(dep)[:100]},
                )
        object.__setattr__(self, "depends_on", dependencies)


@dataclass(frozen=True, slots=True)
class Plan:
    """A completed decomposition: the goal and the step DAG that reaches it.

    Construction is the validation point (see the module docstring). `steps`
    keep the order the producing strategy gave them, and every query that needs
    dependency order (`topological_order`, `waves`) derives it from the shared
    graph rather than trusting that order — a plan written dependents-first is
    still a correct plan.
    """

    goal: str
    steps: tuple[PlanStep, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.goal, str) or not self.goal.strip():
            raise InvalidInputError(
                "a plan needs a non-empty goal",
                details={"goal": str(self.goal)[:100]},
            )
        steps = tuple(self.steps)
        object.__setattr__(self, "steps", steps)
        if not steps:
            raise InvalidInputError("a plan needs at least one step")
        keys: set[str] = set()
        for index, step in enumerate(steps):
            if not isinstance(step, PlanStep):
                raise InvalidInputError(
                    "a plan's steps must be PlanStep values",
                    details={"index": index, "type": type(step).__name__},
                )
            if step.key in keys:
                raise InvalidInputError(
                    "a plan contains the same step key twice",
                    details={"key": step.key},
                )
            keys.add(step.key)
        for step in steps:
            for dep in step.depends_on:
                if dep not in keys:
                    raise InvalidInputError(
                        "a step depends on a key that is not in the plan",
                        details={"step": step.key, "missing": dep[:100]},
                    )
        # Delegated to T040: the one cycle detector. Raises 422 with the closed
        # loop in details["cycle"].
        self.graph()

    # ------------------------------------------------------------------ #
    # Structure
    # ------------------------------------------------------------------ #

    def keys(self) -> tuple[str, ...]:
        """Every step key, in the order the plan declared its steps."""
        return tuple(step.key for step in self.steps)

    def get(self, key: str) -> PlanStep:
        """Return the step named ``key``, or refuse an unknown key (422)."""
        for step in self.steps:
            if step.key == key:
                return step
        raise InvalidInputError(
            "the plan has no such step",
            details={"key": str(key)[:100]},
        )

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and any(step.key == key for step in self.steps)

    def __len__(self) -> int:
        return len(self.steps)

    def __repr__(self) -> str:
        return f"Plan(goal={self.goal[:40]!r}, steps={len(self.steps)})"

    # ------------------------------------------------------------------ #
    # Graph views (built on T040, never re-derived)
    # ------------------------------------------------------------------ #

    def graph(self) -> StepGraph:
        """Return the plan as a T040 :class:`StepGraph` (validated).

        The mapping is deterministic (`uuid5`) and one-way: keys become stable
        node ids, `depends_on` keys become edges. This is what makes the plan
        and the executor's view of a materialised task the *same* graph.
        """
        return StepGraph(
            StepNode(
                id=_node_id(step.key),
                depends_on=tuple(_node_id(dep) for dep in step.depends_on),
            )
            for step in self.steps
        )

    def topological_order(self) -> list[str]:
        """Return step keys, each after every key it depends on."""
        by_id = self._keys_by_node()
        return [by_id[node_id] for node_id in self.graph().topological_order()]

    def waves(self) -> list[list[str]]:
        """Return step keys grouped into dependency levels (§66.4's parallel waves).

        Keys in one wave share no edges, so a caller may run a wave in parallel
        and advance only when it drains.
        """
        by_id = self._keys_by_node()
        return [[by_id[node_id] for node_id in wave] for wave in self.graph().waves()]

    def _keys_by_node(self) -> dict[uuid.UUID, str]:
        return {_node_id(step.key): step.key for step in self.steps}


PlanMatcher = Callable[["PlanningRequest"], bool]


@dataclass(frozen=True, slots=True)
class PlanningRequest:
    """What a strategy's matcher and decomposer see: the goal and context.

    ``context`` is an open mapping, exactly as :class:`~app.core.router.RoutingRequest`
    keeps its own — a strategy may key off the routing intent, a project id or a
    flag without this module taking a dependency on the router or the context
    layer.
    """

    goal: str
    context: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.goal, str) or not self.goal.strip():
            raise InvalidInputError(
                "a planning request needs a non-empty goal",
                details={"goal": str(self.goal)[:100]},
            )


@dataclass(frozen=True, slots=True)
class PlanStrategy:
    """One declared decomposition: "if ``matches``, the goal becomes these steps".

    ``matches`` of ``None`` always matches — the way a caller writes a catch-all,
    given the highest ``priority`` number so it runs only after every specific
    strategy has declined. ``decompose`` returns the plan's steps; the planner
    validates them, so a strategy may return a list, a generator or any iterable.
    """

    name: str
    decompose: Callable[[PlanningRequest], Iterable[PlanStep]]
    matches: PlanMatcher | None = None
    priority: int = 100
    description: str = ""


class Planner:
    """Turns a goal into a validated :class:`Plan` using declared strategies.

    Constructed empty and populated with :meth:`register`, like the T047 router:
    validation happens at registration (a broken strategy is a startup mistake,
    not a silent miss), and the strategy that matches is the *first* in priority
    order, so the same declarations always plan the same way.
    """

    def __init__(self) -> None:
        self._strategies: list[PlanStrategy] = []
        self._names: set[str] = set()

    def register(self, strategy: PlanStrategy) -> None:
        """Add a strategy, refusing a name already taken (409).

        A name is the strategy's identity, so a second with the same name would
        silently shadow the first. The strategy is validated before it is stored:
        an empty name or a non-integer priority is the caller's mistake (422),
        and a non-callable ``decompose``/``matches`` is a wiring bug
        (``TypeError``), the same split the router makes.
        """
        _validate_strategy(strategy)
        if strategy.name in self._names:
            raise ConflictError(
                f"a plan strategy named '{strategy.name}' is already registered",
                details={"strategy": strategy.name},
            )
        self._strategies.append(strategy)
        self._names.add(strategy.name)

    def unregister(self, name: str) -> bool:
        """Remove the strategy named ``name``; return whether it was present."""
        if name not in self._names:
            return False
        self._strategies = [s for s in self._strategies if s.name != name]
        self._names.discard(name)
        return True

    def plan(self, request: PlanningRequest) -> Plan:
        """Decompose ``request`` into a validated plan.

        The first matching strategy in evaluation order decomposes the goal; a
        strategy's matcher or decomposer that raises propagates rather than being
        swallowed. With no match, the goal is planned as a single step — a goal
        is at least one step, and the planner invents no structure it cannot
        justify.
        """
        for strategy in self._ordered():
            if strategy.matches is None or strategy.matches(request):
                return Plan(goal=request.goal, steps=tuple(strategy.decompose(request)))
        return Plan(
            goal=request.goal,
            steps=(PlanStep(key=_TRIVIAL_KEY, name=request.goal),),
        )

    def strategies(self) -> list[PlanStrategy]:
        """Every strategy in evaluation order (priority, then registration)."""
        return self._ordered()

    def __len__(self) -> int:
        return len(self._strategies)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._names

    def __repr__(self) -> str:
        return f"Planner(strategies={len(self._strategies)})"

    def _ordered(self) -> list[PlanStrategy]:
        # A stable sort: equal priorities keep registration order.
        return sorted(self._strategies, key=lambda strategy: strategy.priority)


# -------------------------------------------------------------------------- #
# Pure helpers (stateless, module-level like the router's)
# -------------------------------------------------------------------------- #


def _node_id(key: str) -> uuid.UUID:
    """Map a plan-local key to its stable node id (see ``_PLAN_NAMESPACE``)."""
    return uuid.uuid5(_PLAN_NAMESPACE, key)


def _validate_strategy(strategy: PlanStrategy) -> None:
    """Refuse a structurally broken strategy at registration time."""
    if not isinstance(strategy.name, str) or not strategy.name.strip():
        raise InvalidInputError(
            "a plan strategy needs a non-empty name",
            details={"name": str(strategy.name)[:100]},
        )
    if not callable(strategy.decompose):
        raise TypeError(f"plan strategy '{strategy.name}' has a non-callable decompose")
    if strategy.matches is not None and not callable(strategy.matches):
        raise TypeError(f"plan strategy '{strategy.name}' has a non-callable matcher")
    if isinstance(strategy.priority, bool) or not isinstance(strategy.priority, int):
        raise InvalidInputError(
            "a plan strategy's priority must be an integer",
            details={"strategy": strategy.name, "priority": str(strategy.priority)[:100]},
        )
