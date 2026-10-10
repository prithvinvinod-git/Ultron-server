"""The task step DAG (T040): nodes, edges, order, readiness, cycles.

Section 18 shows a task branching into Research → Implementation → Testing →
Verification → Report, and the `tasks` model turns that into `task_steps`
rows whose `depends_on` carries *"UUID strings of sibling steps that must
complete first"* — while deferring the one thing a relational constraint
cannot express: *"Cycle detection lives in `app/tasks/graph.py`"*. This
module is that graph.

Two graphs exist around §18 and this is deliberately only one of them. The
*task tree* (`parent_task_id`) is a hierarchy of separate tasks, queried
through `TaskManager.list_children` and kept cycle-free by construction (a
new row cannot be its own ancestor). The *step DAG* is what an executor
walks **inside** one task, and it can absolutely contain a cycle, because
`depends_on` is caller-supplied JSON — and an executor handed a cyclic graph
deadlocks silently, which is why detection refuses at the door instead.

What it is:

*   **Pure.** No session, no bus, no `asyncio` — the structure is built from
    rows the caller already loaded (T041) or constructed (T048's planner),
    the same arrangement as `app/tasks/state.py` and
    `app/security/permissions.py`. Statuses are **never stored here**: they
    live on the rows, so there is one source of truth and the graph cannot
    disagree with the database after a restart. Every status-aware query
    takes a mapping as an argument.
*   **Validated at construction.** A duplicate step id, a dependency on a
    step that is not in the graph, and any cycle are refused as
    `InvalidInputError` (422) with the offending values in `details`; a
    cycle names the *closed loop* (`details["cycle"]` ends where it began)
    so the planner can show which steps deadlock rather than "the graph is
    bad". Cycle detection is this module's delegated job — see the model's
    docstring.
*   **Deterministic.** Nodes keep construction order, and Kahn's algorithm
    pops a FIFO queue seeded in that order, so the same steps in the same
    order always yield the same `topological_order()` and `waves()`. Order
    that shuffles between runs is a flaky test and an unsortable UI.

What each query answers, and who wants it:

*   `topological_order()` — every dependency before its dependent: the
    planner's validation view and the executor's replay order.
*   `waves()` — levels whose members depend only on earlier waves:
    §66.4's "parallel-where-safe steps" as data (one wave may run
    concurrently, waves run in sequence).
*   `runnable(statuses)` — PENDING steps whose prerequisites are all
    COMPLETED: what may start now.
*   `blocked(statuses)` — PENDING steps with a prerequisite that FAILED or
    was SKIPPED: they cannot run as planned. *Which* outcomes cascade, and
    whether a blocked step becomes SKIPPED or FAILED, is executor policy
    (T041); this module answers only "can it proceed" — no.
*   `waiting_on(node, statuses)` — one step's unfinished prerequisites:
    literally what a worker parks on. The parking itself (events, timeouts,
    cancellation) is T041's runtime: a graph with no loop cannot wait and
    does not pretend to.
*   `dependents_of()` / `descendants()` — who a step's outcome affects, for
    propagating results or failure downstream.

Deliberately absent: incremental edge insertion (`would_break_cycle` for a
graph that grows) — graphs arrive complete, from rows or from the planner,
and are validated once; and any `TaskStatus` logic — a graph spans one
task's steps, never several tasks (that composition is T041's).
"""

from __future__ import annotations

import uuid
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from app.core.errors import InvalidInputError
from app.database.models import StepStatus, TaskStep

__all__ = ["StepGraph", "StepNode"]


@dataclass(frozen=True, slots=True)
class StepNode:
    """One vertex: a step id and the sibling steps it waits for.

    ``depends_on`` holds resolved UUIDs — `StepGraph.from_task_steps` parses
    the rows' strings, and the constructor refuses anything else rather than
    mixing vocabularies inside one graph.
    """

    id: uuid.UUID
    depends_on: tuple[uuid.UUID, ...] = ()


class StepGraph:
    """An acyclic graph over one task's steps. See the module docstring."""

    def __init__(self, nodes: Iterable[StepNode]) -> None:
        """Build the graph, preserving construction order, or refuse it.

        Validation runs in increasing order of blame — a malformed id, then
        a duplicate, then a dependency pointing outside the graph, and only
        then cycles — so the first refusal names the most fundamental
        problem with the request.
        """
        self._nodes: dict[uuid.UUID, StepNode] = {}
        for node in nodes:
            if not isinstance(node.id, uuid.UUID):
                raise InvalidInputError(
                    "graph node ids must be UUIDs",
                    details={"node": str(node.id)[:100]},
                )
            for dep in node.depends_on:
                if not isinstance(dep, uuid.UUID):
                    raise InvalidInputError(
                        "graph dependencies must be UUIDs",
                        details={"node": str(node.id), "dependency": str(dep)[:100]},
                    )
            if node.id in self._nodes:
                raise InvalidInputError(
                    "the graph contains the same step twice",
                    details={"node": str(node.id)},
                )
            self._nodes[node.id] = node

        # Deduplicated, in the order each node declared them: queries that
        # list a node's dependencies read this, not the raw tuple (a repeated
        # edge would otherwise count twice in topological indegrees).
        self._deps: dict[uuid.UUID, tuple[uuid.UUID, ...]] = {
            node_id: tuple(dict.fromkeys(node.depends_on)) for node_id, node in self._nodes.items()
        }
        self._dependents: dict[uuid.UUID, list[uuid.UUID]] = {
            node_id: [] for node_id in self._nodes
        }
        for node_id, deps in self._deps.items():
            for dep in deps:
                if dep not in self._nodes:
                    raise InvalidInputError(
                        "a step depends on a step that is not in the graph",
                        details={"step": str(node_id), "missing": str(dep)},
                    )
                self._dependents[dep].append(node_id)

        cycle = self._find_cycle()
        if cycle is not None:
            raise InvalidInputError(
                "the graph contains a dependency cycle",
                details={"cycle": [str(step) for step in cycle]},
            )

    @classmethod
    def from_task_steps(cls, steps: Iterable[TaskStep]) -> StepGraph:
        """Build from `task_steps` rows, parsing each row's `depends_on`.

        A value that is not a UUID string is refused by position rather than
        skipped: a silently dropped edge would run a step before the work it
        waits for, turning corrupt data into an ordering bug nobody can see.
        """
        nodes: list[StepNode] = []
        for step in steps:
            if step.id is None:
                raise InvalidInputError(
                    "steps must have ids",
                    details={"index": len(nodes)},
                )
            dependencies: list[uuid.UUID] = []
            # `default=list` fires at flush, so a transient row (the planner's
            # own) has None until then; a *loaded* row is `nullable=False`.
            # None therefore means "unset", which is the declared empty list.
            for index, raw in enumerate(step.depends_on or ()):
                try:
                    dependencies.append(uuid.UUID(str(raw)))
                except (ValueError, AttributeError, TypeError) as error:
                    raise InvalidInputError(
                        "depends_on must hold UUID strings",
                        details={
                            "step": str(step.id),
                            "index": index,
                            "value": str(raw)[:100],
                        },
                    ) from error
            nodes.append(StepNode(id=step.id, depends_on=tuple(dependencies)))
        return cls(nodes)

    # ------------------------------------------------------------------ #
    # Structure
    # ------------------------------------------------------------------ #

    @property
    def steps(self) -> tuple[uuid.UUID, ...]:
        """Every step id, in construction order."""
        return tuple(self._nodes)

    def __contains__(self, item: object) -> bool:
        """Membership by UUID or its string form; anything else is simply absent.

        Total rather than raising: `in` is the cheap probe (`is this step
        still here?`), and a malformed or foreign id answering "no" is the
        same truth as a step that was never in the graph.
        """
        if isinstance(item, uuid.UUID):
            return item in self._nodes
        if isinstance(item, str):
            try:
                return uuid.UUID(item) in self._nodes
            except ValueError:
                return False
        return False

    def __len__(self) -> int:
        return len(self._nodes)

    def topological_order(self) -> list[uuid.UUID]:
        """Return all steps, each after every step it depends on.

        Kahn's algorithm over the dependency direction, with a FIFO queue
        seeded in construction order (see the module docstring on
        determinism). The graph is acyclic by construction, so the order
        covers every node — there is no partial result to handle.
        """
        indegree = {node_id: len(deps) for node_id, deps in self._deps.items()}
        queue: deque[uuid.UUID] = deque(
            node_id for node_id in self._nodes if indegree[node_id] == 0
        )
        order: list[uuid.UUID] = []
        while queue:
            node_id = queue.popleft()
            order.append(node_id)
            for dependent in self._dependents[node_id]:
                indegree[dependent] -= 1
                if indegree[dependent] == 0:
                    queue.append(dependent)
        return order

    def waves(self) -> list[list[uuid.UUID]]:
        """Group steps into dependency levels: wave *n* waits only on *< n*.

        The parallel-safe batches of §66.4 as data — steps inside one wave
        share no edges, so an executor may run a wave concurrently and move
        to the next only when the wave drains.
        """
        order = self.topological_order()
        depth: dict[uuid.UUID, int] = {}
        for node_id in order:
            depth[node_id] = max((depth[dep] for dep in self._deps[node_id]), default=-1) + 1
        grouped: list[list[uuid.UUID]] = []
        for node_id in order:
            level = depth[node_id]
            while len(grouped) <= level:
                grouped.append([])
            grouped[level].append(node_id)
        return grouped

    def dependents_of(self, node: uuid.UUID | str) -> tuple[uuid.UUID, ...]:
        """Return the steps that depend directly on ``node``, in graph order."""
        return tuple(self._dependents[self._require_node(node)])

    def descendants(self, node: uuid.UUID | str) -> frozenset[uuid.UUID]:
        """Return every step transitively downstream of ``node`` (not ``node``).

        The propagation set: when a step's outcome invalidates the rest of
        the plan, this is *whom* it touches — what happens to them is T041's
        policy to apply.
        """
        start = self._require_node(node)
        seen: set[uuid.UUID] = set()
        stack = [start]
        while stack:
            current = stack.pop()
            for dependent in self._dependents[current]:
                if dependent not in seen:
                    seen.add(dependent)
                    stack.append(dependent)
        return frozenset(seen)

    # ------------------------------------------------------------------ #
    # Status-aware queries (statuses stay on the rows — see docstring)
    # ------------------------------------------------------------------ #

    def runnable(self, statuses: Mapping[uuid.UUID, StepStatus]) -> list[uuid.UUID]:
        """Return PENDING steps whose prerequisites are all COMPLETED."""
        self._validate_statuses(statuses)
        return [
            node_id
            for node_id in self._nodes
            if statuses[node_id] is StepStatus.PENDING
            and all(statuses[dep] is StepStatus.COMPLETED for dep in self._deps[node_id])
        ]

    def blocked(self, statuses: Mapping[uuid.UUID, StepStatus]) -> list[uuid.UUID]:
        """Return PENDING steps a FAILED or SKIPPED prerequisite has stranded.

        Excluded: steps that already finished themselves, and steps whose
        dead dependency is only *running* elsewhere — blocked is about the
        dependency being *unfinishable*, not merely unfinished (that is
        `waiting_on`).
        """
        self._validate_statuses(statuses)
        dead = (StepStatus.FAILED, StepStatus.SKIPPED)
        return [
            node_id
            for node_id in self._nodes
            if statuses[node_id] is StepStatus.PENDING
            and any(statuses[dep] in dead for dep in self._deps[node_id])
        ]

    def waiting_on(
        self,
        node: uuid.UUID | str,
        statuses: Mapping[uuid.UUID, StepStatus],
    ) -> tuple[uuid.UUID, ...]:
        """Return ``node``'s prerequisites that are not COMPLETED yet.

        In the node's declared dependency order, so the caller sees a stable
        list to park on (T041 decides what wakes it).
        """
        identifier = self._require_node(node)
        self._validate_statuses(statuses)
        return tuple(
            dep for dep in self._deps[identifier] if statuses[dep] is not StepStatus.COMPLETED
        )

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _require_node(self, node: uuid.UUID | str) -> uuid.UUID:
        """Resolve a caller-supplied id against the graph or refuse it (422).

        Not a 404: the graph is a value the caller holds, not a server
        resource they could miss — naming it their input keeps the refusal
        where the mistake is.
        """
        if isinstance(node, uuid.UUID):
            identifier = node
        else:
            try:
                identifier = uuid.UUID(str(node))
            except (ValueError, AttributeError, TypeError) as error:
                raise InvalidInputError(
                    "node must be a UUID",
                    details={"node": str(node)[:100]},
                ) from error
        if identifier not in self._nodes:
            raise InvalidInputError(
                "the graph has no such step",
                details={"node": str(identifier)},
            )
        return identifier

    def _validate_statuses(self, statuses: Mapping[uuid.UUID, StepStatus]) -> None:
        """Refuse a status map that does not describe exactly this graph.

        Exactly — neither missing nor extra entries — because a query
        answered from a map assembled for a different graph (a stale step
        list after a restart) would silently return *someone else's* answer.
        """
        missing = [str(node_id) for node_id in self._nodes if node_id not in statuses]
        unknown = [str(key) for key in statuses if key not in self._nodes]
        if missing or unknown:
            raise InvalidInputError(
                "statuses must cover exactly the graph's steps",
                details={"missing": missing, "unknown": unknown},
            )
        wrong_type = {
            str(key): type(value).__name__
            for key, value in statuses.items()
            if not isinstance(value, StepStatus)
        }
        if wrong_type:
            raise InvalidInputError(
                "statuses must hold StepStatus members",
                details={"steps": wrong_type},
            )

    def _find_cycle(self) -> list[uuid.UUID] | None:
        """Return one closed loop as a node path, or ``None`` if acyclic.

        Depth-first search following dependencies, colouring nodes as they
        are entered and left. A back edge to a node still on the path is a
        cycle; slicing the path from that node gives the loop itself, not
        every node the search happened to visit first — reporting the whole
        traversal would blame innocent steps for someone else's deadlock.
        """
        entering = 1
        left = 2
        colour: dict[uuid.UUID, int] = {}
        path: list[uuid.UUID] = []

        def visit(node_id: uuid.UUID) -> list[uuid.UUID] | None:
            colour[node_id] = entering
            path.append(node_id)
            for dep in self._deps[node_id]:
                state = colour.get(dep, 0)
                if state == entering:
                    start = path.index(dep)
                    return [*path[start:], dep]
                if state == 0:
                    found = visit(dep)
                    if found is not None:
                        return found
            colour[node_id] = left
            path.pop()
            return None

        for node_id in self._nodes:
            if colour.get(node_id, 0) == 0:
                found = visit(node_id)
                if found is not None:
                    return found
        return None
