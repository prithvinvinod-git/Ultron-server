"""Unit tests for the task step DAG (T040).

`TaskStep.depends_on` is caller-supplied JSON and the model defers cycle
detection to `app/tasks/graph.py` (its docstring promises that split), so
these tests pin what the module claims: refusals that name the offence (a
cycle reports the *closed loop*, a bad status map reports the stray key), an
order that is reproducible and dependency-respecting, waves as §66.4's
parallel-where-safe batches, and the three readiness answers an executor
asks — runnable now, stranded by a dead dependency, still waiting on — with
statuses held outside the graph so a restart cannot strand stale state in
the structure itself.
"""

from __future__ import annotations

import uuid

import pytest

from app.core.errors import InvalidInputError
from app.database.models import StepStatus, TaskStep
from app.tasks.graph import StepGraph, StepNode

pytestmark = pytest.mark.unit

A, B, C, D, Z = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
TASK = uuid.uuid4()


def node(step_id: uuid.UUID, *deps: uuid.UUID) -> StepNode:
    return StepNode(id=step_id, depends_on=deps)


def chain_graph() -> StepGraph:
    return StepGraph([node(A), node(B, A), node(C, B)])


def diamond_graph() -> StepGraph:
    return StepGraph([node(A), node(B, A), node(C, A), node(D, B, C)])


def pending(*steps: uuid.UUID) -> dict[uuid.UUID, StepStatus]:
    return dict.fromkeys(steps, StepStatus.PENDING)


# --------------------------------------------------------------------------- #
# Construction and refusals
# --------------------------------------------------------------------------- #
def test_an_empty_graph_is_valid() -> None:
    graph = StepGraph([])
    assert len(graph) == 0
    assert graph.steps == ()
    assert graph.topological_order() == []
    assert graph.waves() == []


def test_independent_steps_keep_construction_order() -> None:
    graph = StepGraph([node(C), node(A), node(B)])
    assert graph.steps == (C, A, B)
    assert graph.topological_order() == [C, A, B]


def test_a_duplicate_step_is_refused() -> None:
    with pytest.raises(InvalidInputError) as raised:
        StepGraph([node(A), node(A)])
    assert raised.value.details["node"] == str(A)


def test_a_dependency_missing_from_the_graph_is_refused() -> None:
    with pytest.raises(InvalidInputError) as raised:
        StepGraph([node(A, Z)])
    assert raised.value.details == {"step": str(A), "missing": str(Z)}


def test_a_self_dependency_is_a_cycle() -> None:
    with pytest.raises(InvalidInputError) as raised:
        StepGraph([node(A, A)])
    assert raised.value.details["cycle"] == [str(A), str(A)]


def test_a_two_step_cycle_reports_its_closed_loop() -> None:
    with pytest.raises(InvalidInputError) as raised:
        StepGraph([node(A, B), node(B, A)])
    cycle = raised.value.details["cycle"]
    assert cycle[0] == cycle[-1]
    assert set(cycle) == {str(A), str(B)}
    assert len(cycle) == 3


def test_a_three_step_cycle_reports_its_closed_loop() -> None:
    with pytest.raises(InvalidInputError) as raised:
        StepGraph([node(A, C), node(B, A), node(C, B)])
    cycle = raised.value.details["cycle"]
    assert cycle[0] == cycle[-1]
    assert len(cycle) == 4
    assert set(cycle) == {str(A), str(B), str(C)}


def test_a_cycle_behind_valid_work_blames_only_the_loop() -> None:
    with pytest.raises(InvalidInputError) as raised:
        StepGraph([node(D, A), node(A, B), node(B, A)])
    cycle = raised.value.details["cycle"]
    assert str(D) not in cycle
    assert set(cycle) == {str(A), str(B)}


def test_a_node_id_that_is_not_a_uuid_is_refused() -> None:
    with pytest.raises(InvalidInputError) as raised:
        StepGraph([StepNode(id="not-a-uuid")])  # type: ignore[arg-type]
    assert raised.value.details["node"] == "not-a-uuid"


def test_a_dependency_that_is_not_a_uuid_is_refused() -> None:
    with pytest.raises(InvalidInputError) as raised:
        StepGraph([StepNode(id=A, depends_on=("x",))])  # type: ignore[arg-type]
    assert raised.value.details == {"node": str(A), "dependency": "x"}


# --------------------------------------------------------------------------- #
# Building from task_steps rows
# --------------------------------------------------------------------------- #
def test_from_task_steps_builds_the_edges() -> None:
    rows = [
        TaskStep(id=A, task_id=TASK, name="first"),
        TaskStep(id=B, task_id=TASK, name="second", depends_on=[str(A)]),
    ]
    graph = StepGraph.from_task_steps(rows)
    assert len(graph) == 2
    assert graph.topological_order() == [A, B]


def test_from_task_steps_refuses_a_corrupt_dependency() -> None:
    rows = [TaskStep(id=A, task_id=TASK, name="first", depends_on=["nope"])]
    with pytest.raises(InvalidInputError) as raised:
        StepGraph.from_task_steps(rows)
    assert raised.value.details == {"step": str(A), "index": 0, "value": "nope"}


def test_from_task_steps_refuses_a_row_without_an_id() -> None:
    rows = [TaskStep(task_id=TASK, name="first")]
    with pytest.raises(InvalidInputError) as raised:
        StepGraph.from_task_steps(rows)
    assert raised.value.details == {"index": 0}


def test_from_task_steps_refuses_a_dependency_on_a_stranger_step() -> None:
    rows = [TaskStep(id=A, task_id=TASK, name="first", depends_on=[str(Z)])]
    with pytest.raises(InvalidInputError) as raised:
        StepGraph.from_task_steps(rows)
    assert raised.value.details == {"step": str(A), "missing": str(Z)}


def test_from_task_steps_collapses_a_repeated_edge() -> None:
    rows = [
        TaskStep(id=A, task_id=TASK, name="first"),
        TaskStep(id=B, task_id=TASK, name="second", depends_on=[str(A), str(A)]),
    ]
    graph = StepGraph.from_task_steps(rows)
    assert graph.topological_order() == [A, B]


def test_from_task_steps_accepts_a_step_set_without_rows() -> None:
    assert len(StepGraph.from_task_steps([])) == 0


# --------------------------------------------------------------------------- #
# Order and waves
# --------------------------------------------------------------------------- #
def test_a_chain_orders_every_dependency_first() -> None:
    assert chain_graph().topological_order() == [A, B, C]


def test_every_dependency_precedes_its_dependent() -> None:
    order = diamond_graph().topological_order()
    position = {step: index for index, step in enumerate(order)}
    assert position[A] < position[B] < position[D]
    assert position[A] < position[C] < position[D]


def test_topological_order_covers_each_step_exactly_once() -> None:
    graph = diamond_graph()
    order = graph.topological_order()
    assert sorted(order) == sorted(graph.steps)
    assert len(order) == len(set(order))


def test_the_same_construction_always_orders_the_same_way() -> None:
    assert diamond_graph().topological_order() == diamond_graph().topological_order()


def test_waves_group_the_parallel_safe_steps_of_a_diamond() -> None:
    assert diamond_graph().waves() == [[A], [B, C], [D]]


def test_a_linear_chain_makes_one_step_per_wave() -> None:
    assert chain_graph().waves() == [[A], [B], [C]]


def test_independent_steps_share_a_single_wave() -> None:
    assert StepGraph([node(C), node(A), node(B)]).waves() == [[C, A, B]]


# --------------------------------------------------------------------------- #
# Readiness: runnable and blocked
# --------------------------------------------------------------------------- #
def test_runnable_opens_with_the_steps_that_wait_for_nothing() -> None:
    assert diamond_graph().runnable(pending(A, B, C, D)) == [A]


def test_runnable_advances_as_the_diamond_drains() -> None:
    graph = diamond_graph()
    statuses = pending(A, B, C, D)
    statuses[A] = StepStatus.COMPLETED
    assert graph.runnable(statuses) == [B, C]
    statuses[B] = StepStatus.COMPLETED
    statuses[C] = StepStatus.COMPLETED
    assert graph.runnable(statuses) == [D]
    statuses[D] = StepStatus.COMPLETED
    assert graph.runnable(statuses) == []


def test_a_running_dependency_does_not_yet_free_its_dependents() -> None:
    graph = diamond_graph()
    statuses = pending(A, B, C, D)
    statuses[A] = StepStatus.COMPLETED
    statuses[B] = StepStatus.RUNNING
    assert graph.runnable(statuses) == [C]


@pytest.mark.parametrize("outcome", [StepStatus.FAILED, StepStatus.SKIPPED])
def test_a_dead_dependency_strands_its_dependents(outcome: StepStatus) -> None:
    graph = diamond_graph()
    statuses = pending(A, B, C, D)
    statuses[A] = StepStatus.COMPLETED
    statuses[B] = outcome
    assert graph.blocked(statuses) == [D]


def test_a_step_that_already_finished_is_never_blocked() -> None:
    graph = diamond_graph()
    statuses = pending(A, B, C, D)
    statuses[A] = StepStatus.COMPLETED
    statuses[B] = StepStatus.FAILED
    statuses[C] = StepStatus.COMPLETED
    statuses[D] = StepStatus.COMPLETED
    assert graph.blocked(statuses) == []


def test_nothing_is_blocked_while_the_work_is_only_in_flight() -> None:
    graph = diamond_graph()
    statuses = pending(A, B, C, D)
    statuses[A] = StepStatus.COMPLETED
    statuses[B] = StepStatus.RUNNING
    assert graph.blocked(statuses) == []


# --------------------------------------------------------------------------- #
# The status map must describe this graph, and only this graph
# --------------------------------------------------------------------------- #
def test_a_missing_status_is_refused() -> None:
    statuses = {A: StepStatus.PENDING, B: StepStatus.PENDING, D: StepStatus.PENDING}
    with pytest.raises(InvalidInputError) as raised:
        diamond_graph().runnable(statuses)
    assert raised.value.details["missing"] == [str(C)]
    assert raised.value.details["unknown"] == []


def test_a_status_belonging_to_another_graph_is_refused() -> None:
    statuses = pending(A, B, C, D)
    statuses[Z] = StepStatus.PENDING
    with pytest.raises(InvalidInputError) as raised:
        diamond_graph().runnable(statuses)
    assert raised.value.details["unknown"] == [str(Z)]
    assert raised.value.details["missing"] == []


def test_a_status_must_be_a_step_status_member() -> None:
    statuses = {A: "pending", B: "pending", C: "pending", D: "pending"}
    with pytest.raises(InvalidInputError) as raised:
        diamond_graph().runnable(statuses)  # type: ignore[arg-type]
    assert raised.value.details["steps"] == {
        str(A): "str",
        str(B): "str",
        str(C): "str",
        str(D): "str",
    }


def test_every_status_aware_query_validates_the_map() -> None:
    incomplete = pending(A)
    graph = diamond_graph()
    with pytest.raises(InvalidInputError):
        graph.runnable(incomplete)
    with pytest.raises(InvalidInputError):
        graph.blocked(incomplete)
    with pytest.raises(InvalidInputError):
        graph.waiting_on(D, incomplete)


# --------------------------------------------------------------------------- #
# waiting_on: what a worker parks on
# --------------------------------------------------------------------------- #
def test_waiting_on_lists_the_prerequisites_still_open() -> None:
    graph = diamond_graph()
    statuses = pending(A, B, C, D)
    assert graph.waiting_on(D, statuses) == (B, C)
    statuses[A] = StepStatus.COMPLETED
    assert graph.waiting_on(D, statuses) == (B, C)
    statuses[B] = StepStatus.COMPLETED
    assert graph.waiting_on(D, statuses) == (C,)
    statuses[C] = StepStatus.COMPLETED
    assert graph.waiting_on(D, statuses) == ()


def test_waiting_on_accepts_a_string_id() -> None:
    graph = diamond_graph()
    statuses = pending(A, B, C, D)
    assert graph.waiting_on(str(D), statuses) == (B, C)


def test_waiting_on_with_an_unknown_step_is_refused() -> None:
    statuses = pending(A, B, C, D)
    with pytest.raises(InvalidInputError) as raised:
        diamond_graph().waiting_on(Z, statuses)
    assert raised.value.details["node"] == str(Z)


def test_waiting_on_with_a_malformed_id_is_refused() -> None:
    statuses = pending(A, B, C, D)
    with pytest.raises(InvalidInputError) as raised:
        diamond_graph().waiting_on("not-a-uuid", statuses)
    assert raised.value.details["node"] == "not-a-uuid"


# --------------------------------------------------------------------------- #
# Propagation: who a step's outcome touches
# --------------------------------------------------------------------------- #
def test_dependents_are_the_direct_consumers_only() -> None:
    graph = diamond_graph()
    assert graph.dependents_of(A) == (B, C)
    assert graph.dependents_of(B) == (D,)
    assert graph.dependents_of(D) == ()


def test_dependents_follow_construction_order() -> None:
    graph = StepGraph([node(A), node(C, A), node(B, A)])
    assert graph.dependents_of(A) == (C, B)


def test_descendants_reach_everything_downstream() -> None:
    graph = diamond_graph()
    assert graph.descendants(A) == frozenset({B, C, D})
    assert graph.descendants(B) == frozenset({D})
    assert graph.descendants(D) == frozenset()


def test_an_unknown_step_cannot_be_queried() -> None:
    graph = diamond_graph()
    with pytest.raises(InvalidInputError):
        graph.dependents_of(Z)
    with pytest.raises(InvalidInputError):
        graph.descendants(Z)


def test_membership_accepts_uuids_and_their_string_form() -> None:
    graph = diamond_graph()
    assert A in graph
    assert str(A) in graph
    assert Z not in graph
    assert "not-a-uuid" not in graph
    assert 7 not in graph
