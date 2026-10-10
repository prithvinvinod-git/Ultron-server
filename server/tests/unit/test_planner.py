"""Unit tests for the planner (T048).

§50 puts ``PLAN`` between ``INTENT`` and ``TASK GRAPH`` and §66.4 requires the
planner to build single-step, multi-step, dependent and parallel-where-safe
graphs *on the existing task DAG, not as an isolated agent*. These tests pin
that: a plan is validated when it is built (duplicate keys, dangling
dependencies and cycles are all refused, and cycles are caught by T040's one
detector), strategies are declared and picked deterministically (lowest
priority first, ties in registration order, a catch-all only when nothing else
matched), and a goal with no matching strategy plans as the honest single step.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from app.core.errors import ConflictError, ErrorCode, InvalidInputError
from app.core.planner import (
    Plan,
    Planner,
    PlanningRequest,
    PlanStep,
    PlanStrategy,
)
from app.tasks.graph import StepGraph

pytestmark = pytest.mark.unit


def _research() -> PlanStep:
    return PlanStep(key="research", name="Research the problem")


def _implement() -> PlanStep:
    return PlanStep(key="implement", name="Implement the fix", depends_on=("research",))


# ---------------------------------------------------------------------------
# PlanStep
# ---------------------------------------------------------------------------


def test_plan_step_defaults() -> None:
    step = PlanStep(key="a", name="do a")
    assert step.key == "a"
    assert step.name == "do a"
    assert step.description == ""
    assert step.depends_on == ()


def test_plan_step_coerces_dependencies_to_a_tuple() -> None:
    step = PlanStep(key="b", name="do b", depends_on=["a"])  # type: ignore[arg-type]
    assert step.depends_on == ("a",)
    assert isinstance(step.depends_on, tuple)


def test_plan_step_refuses_an_empty_key() -> None:
    with pytest.raises(InvalidInputError) as caught:
        PlanStep(key="   ", name="do a")
    assert caught.value.code is ErrorCode.INVALID_INPUT


def test_plan_step_refuses_a_non_string_key() -> None:
    with pytest.raises(InvalidInputError):
        PlanStep(key=5, name="do a")  # type: ignore[arg-type]


def test_plan_step_refuses_an_empty_name() -> None:
    with pytest.raises(InvalidInputError):
        PlanStep(key="a", name="  ")


def test_plan_step_refuses_a_non_string_description() -> None:
    with pytest.raises(InvalidInputError):
        PlanStep(key="a", name="do a", description=5)  # type: ignore[arg-type]


def test_plan_step_refuses_a_bare_string_depends_on() -> None:
    with pytest.raises(InvalidInputError):
        PlanStep(key="a", name="do a", depends_on="research")  # type: ignore[arg-type]


def test_plan_step_refuses_a_non_string_dependency() -> None:
    with pytest.raises(InvalidInputError):
        PlanStep(key="a", name="do a", depends_on=(5,))  # type: ignore[arg-type]


def test_plan_step_refuses_a_blank_dependency() -> None:
    with pytest.raises(InvalidInputError):
        PlanStep(key="a", name="do a", depends_on=("  ",))


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


def test_plan_holds_its_goal_and_steps() -> None:
    plan = Plan(goal="ship it", steps=(_research(), _implement()))
    assert plan.goal == "ship it"
    assert len(plan) == 2
    assert plan.keys() == ("research", "implement")


def test_plan_coerces_steps_to_a_tuple() -> None:
    plan = Plan(goal="ship it", steps=[_research()])  # type: ignore[arg-type]
    assert isinstance(plan.steps, tuple)


def test_plan_refuses_an_empty_goal() -> None:
    with pytest.raises(InvalidInputError) as caught:
        Plan(goal="   ", steps=(_research(),))
    assert caught.value.code is ErrorCode.INVALID_INPUT


def test_plan_refuses_no_steps() -> None:
    with pytest.raises(InvalidInputError, match="at least one step"):
        Plan(goal="ship it", steps=())


def test_plan_refuses_a_non_step() -> None:
    with pytest.raises(InvalidInputError, match="PlanStep"):
        Plan(goal="ship it", steps=("not a step",))  # type: ignore[arg-type]


def test_plan_refuses_a_duplicate_key() -> None:
    with pytest.raises(InvalidInputError) as caught:
        Plan(goal="ship it", steps=(_research(), _research()))
    assert caught.value.details["key"] == "research"


def test_plan_refuses_a_dependency_on_a_missing_key() -> None:
    with pytest.raises(InvalidInputError) as caught:
        Plan(goal="ship it", steps=(_implement(),))
    assert caught.value.details["missing"] == "research"


def test_plan_refuses_a_self_dependency() -> None:
    step = PlanStep(key="a", name="do a", depends_on=("a",))
    with pytest.raises(InvalidInputError, match="cycle"):
        Plan(goal="ship it", steps=(step,))


def test_plan_refuses_a_cycle_and_names_the_loop() -> None:
    with pytest.raises(InvalidInputError) as caught:
        Plan(
            goal="ship it",
            steps=(
                PlanStep(key="a", name="a", depends_on=("b",)),
                PlanStep(key="b", name="b", depends_on=("a",)),
            ),
        )
    assert "cycle" in caught.value.details


def test_plan_get_returns_a_step() -> None:
    plan = Plan(goal="ship it", steps=(_research(),))
    assert plan.get("research").name == "Research the problem"


def test_plan_get_refuses_an_unknown_key() -> None:
    plan = Plan(goal="ship it", steps=(_research(),))
    with pytest.raises(InvalidInputError, match="no such step"):
        plan.get("missing")


def test_plan_contains_and_length() -> None:
    plan = Plan(goal="ship it", steps=(_research(),))
    assert "research" in plan
    assert "missing" not in plan
    assert 5 not in plan
    assert len(plan) == 1


def test_plan_repr_reports_the_step_count() -> None:
    plan = Plan(goal="ship it", steps=(_research(),))
    assert repr(plan) == "Plan(goal='ship it', steps=1)"


def test_plan_is_frozen() -> None:
    plan = Plan(goal="ship it", steps=(_research(),))
    with pytest.raises(FrozenInstanceError):
        plan.goal = "other"  # type: ignore[misc]


def test_plan_topological_order_puts_dependencies_first() -> None:
    plan = Plan(goal="ship it", steps=(_implement(), _research()))
    assert plan.topological_order() == ["research", "implement"]


def test_plan_topological_order_ignores_declaration_order() -> None:
    # Declared dependents-first; the graph still orders prerequisites first.
    plan = Plan(goal="g", steps=(_implement(), _research()))
    assert plan.topological_order()[0] == "research"


def test_plan_waves_group_parallel_steps() -> None:
    plan = Plan(
        goal="g",
        steps=(
            PlanStep(key="a", name="a"),
            PlanStep(key="b", name="b"),
            PlanStep(key="c", name="c", depends_on=("a", "b")),
        ),
    )
    assert plan.waves() == [["a", "b"], ["c"]]


def test_plan_graph_is_a_step_graph() -> None:
    plan = Plan(goal="g", steps=(_implement(), _research()))
    graph = plan.graph()
    assert isinstance(graph, StepGraph)
    assert len(graph) == 2
    assert len(graph.topological_order()) == 2


# ---------------------------------------------------------------------------
# Planner — registration
# ---------------------------------------------------------------------------


def test_register_and_list_strategies() -> None:
    planner = Planner()
    planner.register(
        PlanStrategy(name="two-step", decompose=lambda request: (_research(), _implement()))
    )
    assert len(planner) == 1
    assert [s.name for s in planner.strategies()] == ["two-step"]


def test_duplicate_strategy_name_is_a_conflict() -> None:
    planner = Planner()
    planner.register(PlanStrategy(name="x", decompose=lambda request: (_research(),)))
    with pytest.raises(ConflictError) as caught:
        planner.register(PlanStrategy(name="x", decompose=lambda request: (_implement(),)))
    assert caught.value.code is ErrorCode.CONFLICT
    assert caught.value.http_status == 409


def test_unregister_removes_a_strategy() -> None:
    planner = Planner()
    planner.register(PlanStrategy(name="x", decompose=lambda request: (_research(),)))
    assert planner.unregister("x") is True
    assert planner.unregister("x") is False
    assert len(planner) == 0


def test_contains_and_length_and_repr() -> None:
    planner = Planner()
    planner.register(PlanStrategy(name="x", decompose=lambda request: (_research(),)))
    assert "x" in planner
    assert "absent" not in planner
    assert 5 not in planner
    assert len(planner) == 1
    assert repr(planner) == "Planner(strategies=1)"


def test_strategies_are_listed_in_evaluation_order() -> None:
    planner = Planner()
    planner.register(PlanStrategy(name="late", decompose=lambda r: (_research(),), priority=50))
    planner.register(PlanStrategy(name="early", decompose=lambda r: (_research(),), priority=1))
    assert [s.name for s in planner.strategies()] == ["early", "late"]


@pytest.mark.parametrize(
    "strategy",
    [
        PlanStrategy(name="  ", decompose=lambda r: ()),
        PlanStrategy(name="x", decompose=lambda r: (), priority="high"),  # type: ignore[arg-type]
    ],
)
def test_a_broken_strategy_is_refused_at_registration(strategy: PlanStrategy) -> None:
    planner = Planner()
    with pytest.raises(InvalidInputError) as caught:
        planner.register(strategy)
    assert caught.value.code is ErrorCode.INVALID_INPUT


def test_a_bool_priority_is_refused() -> None:
    planner = Planner()
    with pytest.raises(InvalidInputError):
        planner.register(PlanStrategy(name="x", decompose=lambda r: (), priority=True))


def test_a_non_callable_decompose_is_a_type_error() -> None:
    planner = Planner()
    strategy = PlanStrategy(name="x", decompose="not callable")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        planner.register(strategy)


def test_a_non_callable_matcher_is_a_type_error() -> None:
    planner = Planner()
    strategy = PlanStrategy(
        name="x",
        decompose=lambda r: (),
        matches="not callable",  # type: ignore[arg-type]
    )
    with pytest.raises(TypeError):
        planner.register(strategy)


# ---------------------------------------------------------------------------
# Planner — plan
# ---------------------------------------------------------------------------


def test_no_strategy_plans_the_goal_as_one_step() -> None:
    plan = Planner().plan(PlanningRequest("fix the bug"))
    assert plan.goal == "fix the bug"
    assert len(plan) == 1
    assert plan.get("step").name == "fix the bug"


def test_a_matching_strategy_decomposes_the_goal() -> None:
    planner = Planner()
    planner.register(
        PlanStrategy(
            name="fix",
            decompose=lambda r: (_research(), _implement()),
            matches=lambda r: "fix" in r.goal,
        )
    )
    plan = planner.plan(PlanningRequest("fix the login bug"))
    assert plan.keys() == ("research", "implement")


def test_lower_priority_strategy_runs_first() -> None:
    planner = Planner()
    planner.register(
        PlanStrategy(
            name="late",
            decompose=lambda r: (PlanStep(key="late", name="late"),),
            matches=lambda r: True,
            priority=50,
        )
    )
    planner.register(
        PlanStrategy(
            name="early",
            decompose=lambda r: (PlanStep(key="early", name="early"),),
            matches=lambda r: True,
            priority=1,
        )
    )
    assert planner.plan(PlanningRequest("g")).keys() == ("early",)


def test_registration_order_breaks_priority_ties() -> None:
    planner = Planner()
    planner.register(
        PlanStrategy(
            name="first",
            decompose=lambda r: (PlanStep(key="first", name="first"),),
            matches=lambda r: True,
        )
    )
    planner.register(
        PlanStrategy(
            name="second",
            decompose=lambda r: (PlanStep(key="second", name="second"),),
            matches=lambda r: True,
        )
    )
    assert planner.plan(PlanningRequest("g")).keys() == ("first",)


def test_a_catch_all_only_fires_when_nothing_else_matched() -> None:
    planner = Planner()
    planner.register(
        PlanStrategy(
            name="fix",
            decompose=lambda r: (PlanStep(key="fix", name="fix"),),
            matches=lambda r: "fix" in r.goal,
            priority=10,
        )
    )
    planner.register(
        PlanStrategy(
            name="fallback",
            decompose=lambda r: (PlanStep(key="fallback", name="fallback"),),
            priority=1000,
        )
    )
    assert planner.plan(PlanningRequest("fix it")).keys() == ("fix",)
    assert planner.plan(PlanningRequest("tell a joke")).keys() == ("fallback",)


def test_matcher_sees_the_goal_and_context() -> None:
    seen: list[PlanningRequest] = []

    def record(request: PlanningRequest) -> bool:
        seen.append(request)
        return True

    planner = Planner()
    planner.register(
        PlanStrategy(
            name="observer",
            decompose=lambda r: (PlanStep(key="a", name="a"),),
            matches=record,
        )
    )
    planner.plan(PlanningRequest("do it", {"intent": "task"}))
    assert len(seen) == 1
    assert seen[0].goal == "do it"
    assert seen[0].context == {"intent": "task"}


def test_decomposer_sees_the_request() -> None:
    seen: list[PlanningRequest] = []

    def record(request: PlanningRequest) -> tuple[PlanStep, ...]:
        seen.append(request)
        return (PlanStep(key="a", name="a"),)

    planner = Planner()
    planner.register(PlanStrategy(name="observer", decompose=record))
    planner.plan(PlanningRequest("do it"))
    assert seen[0].goal == "do it"


def test_a_raising_matcher_propagates() -> None:
    def boom(request: PlanningRequest) -> bool:
        raise RuntimeError("matcher exploded")

    planner = Planner()
    planner.register(
        PlanStrategy(name="boom", decompose=lambda r: (PlanStep(key="a", name="a"),), matches=boom)
    )
    with pytest.raises(RuntimeError, match="matcher exploded"):
        planner.plan(PlanningRequest("g"))


def test_a_raising_decomposer_propagates() -> None:
    def boom(request: PlanningRequest) -> tuple[PlanStep, ...]:
        raise RuntimeError("decomposer exploded")

    planner = Planner()
    planner.register(PlanStrategy(name="boom", decompose=boom))
    with pytest.raises(RuntimeError, match="decomposer exploded"):
        planner.plan(PlanningRequest("g"))


def test_a_decomposition_is_validated() -> None:
    planner = Planner()
    planner.register(
        PlanStrategy(
            name="dupes",
            decompose=lambda r: (PlanStep(key="a", name="a"), PlanStep(key="a", name="again")),
        )
    )
    with pytest.raises(InvalidInputError, match="twice"):
        planner.plan(PlanningRequest("g"))


def test_a_decomposer_may_return_a_generator() -> None:
    planner = Planner()
    planner.register(
        PlanStrategy(
            name="lazy",
            decompose=lambda r: (step for step in (_research(), _implement())),
        )
    )
    plan = planner.plan(PlanningRequest("g"))
    assert plan.keys() == ("research", "implement")
    assert plan.waves() == [["research"], ["implement"]]


# ---------------------------------------------------------------------------
# PlanningRequest
# ---------------------------------------------------------------------------


def test_planning_request_defaults_to_empty_context() -> None:
    request = PlanningRequest("do it")
    assert request.goal == "do it"
    assert request.context == {}


def test_planning_request_refuses_an_empty_goal() -> None:
    with pytest.raises(InvalidInputError) as caught:
        PlanningRequest("   ")
    assert caught.value.code is ErrorCode.INVALID_INPUT


def test_planning_request_refuses_a_non_string_goal() -> None:
    with pytest.raises(InvalidInputError):
        PlanningRequest(5)  # type: ignore[arg-type]
