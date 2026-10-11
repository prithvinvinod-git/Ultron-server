"""Coding-engine interface tests (T081, spec sections 8 and 9).

The engine ABC is a contract, so the tests are pure: the task/event/result
value objects, the callback dispatch, and the definition-time guards (a
concrete engine that ships undeclared, or with a sync ``run``, fails at
import — the same wall T042 builds for agents). A minimal concrete engine
defined here proves the interface is actually implementable; the *behaviour*
over a real subprocess belongs to T082/T086.
"""

from __future__ import annotations

import asyncio
from os import sep
from pathlib import Path
from typing import Any, ClassVar

import pytest

from app.agents.coding import (
    CodingEngine,
    CodingEvent,
    CodingEventCallback,
    CodingEventKind,
    CodingRunResult,
    CodingTask,
)
from app.agents.coding.engine import dispatch_event
from app.core.errors import InvalidInputError

pytestmark = pytest.mark.unit


class EchoEngine(CodingEngine):
    """The minimal honest concrete engine: declares its name and returns."""

    name: ClassVar[str] = "echo"

    async def run(
        self,
        task: CodingTask,
        *,
        on_event: CodingEventCallback | None = None,
        stop: asyncio.Event | None = None,
    ) -> CodingRunResult:
        del on_event, stop
        return CodingRunResult(
            task_id=task.task_id,
            engine=self.name,
            exit_code=0,
            output=(),
            truncated=False,
            ran_seconds=0.0,
        )


class MidEngine(CodingEngine):
    """An abstract intermediate; declarations not yet due."""


def task(**overrides: Any) -> CodingTask:
    """A well-formed default task for the value-object tests."""
    values: dict[str, Any] = {
        "task_id": "t-1",
        "prompt": "add a test",
        "workspace": "/ws/sample",
        "files": ("tests/test_one.py",),
    }
    values.update(overrides)
    return CodingTask(**values)


# --------------------------------------------------------------------------- #
# CodingTask validation
# --------------------------------------------------------------------------- #
def test_task_requires_a_task_id() -> None:
    with pytest.raises(InvalidInputError):
        task(task_id="")

    with pytest.raises(InvalidInputError):
        task(task_id="   ")


def test_task_requires_a_prompt() -> None:
    with pytest.raises(InvalidInputError):
        task(prompt="")

    with pytest.raises(InvalidInputError):
        task(prompt=42)


def test_task_requires_the_workspace_field() -> None:
    # The workspace is mandatory and explicit (§9: every coding task receives
    # one) — there is no default cwd for an engine to fall back to.
    with pytest.raises(TypeError):
        CodingTask(task_id="t-1", prompt="add a test")  # type: ignore[call-arg]


def test_task_accepts_no_expected_files() -> None:
    assert task(files=()).files == ()


def test_task_rejects_an_absolute_expected_file() -> None:
    # A filesystem-native absolute path (drive root on Windows, "/" on POSIX)
    # must not be accepted as a workspace-relative expectation.
    native_absolute = Path(sep).resolve() / "escape.py"
    with pytest.raises(InvalidInputError) as excinfo:
        task(files=(str(native_absolute),))

    assert "relative" in excinfo.value.message


def test_task_rejects_a_windows_absolute_expected_file() -> None:
    with pytest.raises(InvalidInputError):
        task(files=("C:\\Users\\x\\evil.py",))


def test_task_rejects_a_parent_escape_expected_file() -> None:
    with pytest.raises(InvalidInputError) as excinfo:
        task(files=("../../escape.py",))

    assert "outside the workspace" in excinfo.value.message


def test_task_rejects_an_empty_expected_file() -> None:
    with pytest.raises(InvalidInputError):
        task(files=("",))


def test_task_normalises_whitespace_on_text_fields() -> None:
    built = task(task_id="  t-9  ", prompt="   work  ")
    assert built.task_id == "t-9"
    assert built.prompt == "work"


# --------------------------------------------------------------------------- #
# CodingEvent validation
# --------------------------------------------------------------------------- #
def test_event_requires_a_kind() -> None:
    bad_kind: Any = None
    with pytest.raises(InvalidInputError):
        CodingEvent(task_id="t-1", kind=bad_kind)

    not_a_kind: Any = "not-a-kind"
    with pytest.raises(InvalidInputError):
        CodingEvent(task_id="t-1", kind=not_a_kind)


def test_event_coerces_a_non_string_line() -> None:
    noisy_line: Any = 7
    event = CodingEvent(task_id="t-1", kind=CodingEventKind.OUTPUT, line=noisy_line)
    assert event.line == "7"


def test_event_defaults_its_timestamp() -> None:
    event = CodingEvent(task_id="t-1", kind=CodingEventKind.STARTED)
    assert event.occurred_at is not None


# --------------------------------------------------------------------------- #
# CodingRunResult
# --------------------------------------------------------------------------- #
def test_result_output_text_joins_lines() -> None:
    result = CodingRunResult(
        task_id="t-1",
        engine="echo",
        exit_code=0,
        output=("one", "two"),
        truncated=False,
        ran_seconds=1.5,
    )
    assert result.output_text == "one\ntwo\n"


def test_result_output_text_is_empty_for_no_output() -> None:
    result = CodingRunResult(
        task_id="t-1",
        engine="echo",
        exit_code=0,
        output=(),
        truncated=False,
        ran_seconds=0.0,
    )
    assert result.output_text == ""


# --------------------------------------------------------------------------- #
# dispatch_event: sync and async callbacks
# --------------------------------------------------------------------------- #
def test_dispatch_event_none_callback_is_a_noop() -> None:
    asyncio.run(dispatch_event(None, CodingEvent(task_id="t-1", kind=CodingEventKind.STARTED)))


def test_dispatch_event_awaits_a_sync_callback() -> None:
    seen: list[CodingEvent] = []

    def callback(event: CodingEvent) -> None:
        seen.append(event)

    asyncio.run(dispatch_event(callback, CodingEvent(task_id="t-1", kind=CodingEventKind.STARTED)))

    assert [event.kind for event in seen] == [CodingEventKind.STARTED]


def test_dispatch_event_awaits_an_async_callback() -> None:
    seen: list[CodingEvent] = []

    async def callback(event: CodingEvent) -> None:
        await asyncio.sleep(0)
        seen.append(event)

    asyncio.run(dispatch_event(callback, CodingEvent(task_id="t-1", kind=CodingEventKind.OUTPUT)))

    assert [event.kind for event in seen] == [CodingEventKind.OUTPUT]


# --------------------------------------------------------------------------- #
# Definition-time guards (the wall a concrete engine must clear)
# --------------------------------------------------------------------------- #
def test_an_undeclared_concrete_engine_is_refused() -> None:
    with pytest.raises(TypeError):

        class NoName(CodingEngine):
            async def run(
                self,
                task: CodingTask,
                *,
                on_event: CodingEventCallback | None = None,
                stop: asyncio.Event | None = None,
            ) -> CodingRunResult:
                del on_event, stop
                raise NotImplementedError


def test_a_sync_run_engine_is_refused() -> None:
    with pytest.raises(TypeError):

        class SyncRun(CodingEngine):
            name: ClassVar[str] = "sync"

            def run(self, task: CodingTask) -> CodingRunResult:  # type: ignore[override]
                raise NotImplementedError


def test_an_empty_name_engine_is_refused() -> None:
    with pytest.raises(TypeError):

        class BlankName(CodingEngine):
            name: ClassVar[str] = "  "

            async def run(
                self,
                task: CodingTask,
                *,
                on_event: CodingEventCallback | None = None,
                stop: asyncio.Event | None = None,
            ) -> CodingRunResult:
                del on_event, stop
                raise NotImplementedError


def test_an_abstract_intermediate_is_exempt() -> None:
    # MidEngine implements no `name` and no `run`; it stayed abstract, so the
    # declarations are not yet due (the same rule as T042's MidAgent).
    import inspect as _inspect

    assert _inspect.isabstract(MidEngine)


def test_the_declared_concrete_engine_is_usable() -> None:
    engine = EchoEngine()
    assert engine.name == "echo"
    result = asyncio.run(engine.run(task()))
    assert result.task_id == "t-1"
    assert result.exit_code == 0


async def test_engine_run_signature_allows_the_stop_event() -> None:
    stop = asyncio.Event()
    result = await EchoEngine().run(task(), stop=stop)
    assert result.engine == "echo"


def test_package_exports_the_contract() -> None:
    from app.agents import coding

    for exported in (CodingEngine, CodingEvent, CodingEventKind, CodingRunResult, CodingTask):
        assert getattr(coding, exported.__name__) is exported
