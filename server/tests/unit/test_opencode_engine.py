"""OpenCode engine tests (T082, spec sections 8 and 9).

These drive the engine over a *real* subprocess, but a harmless one: the
configured executable is the test interpreter (``sys.executable``) and the
"engine" is a one-line ``-c`` script, so every §9 rule is checked for real
without needing the OpenCode CLI installed. The script prints its ``sys.argv``,
so the literal-argv construction (model, provider, extra args, prompt last) is
observable rather than mocked.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

import pytest

from app.agents.coding import (
    CodingEvent,
    CodingEventKind,
    CodingTask,
    OpenCodeEngine,
)
from app.config.settings import OpenCodeSettings
from app.core.errors import (
    DependencyUnavailableError,
    InvalidInputError,
    OperationCancelledError,
    OperationTimeoutError,
    WorkspaceError,
)

pytestmark = pytest.mark.unit

# Echo the argv as JSON on one line, then the prompt as the last line.
ECHO_ARGV = "import json,sys;print(json.dumps(sys.argv));print('PROMPT=' + sys.argv[-1])"
# Print ten numbered lines (for the capture cap).
MANY_LINES = "[print(i) for i in range(10)]"
# Print one line, flush, then sleep long enough to be timed out or cancelled.
SLEEPY = "import time;print('start', flush=True);time.sleep(20)"
# Report an environment variable (for the isolation checks).
SHOW_PYTHONPATH = "import os;print('PYTHONPATH=' + os.environ.get('PYTHONPATH', 'MISSING'))"
SHOW_FOO = "import os;print('FOO=' + os.environ.get('FOO', 'MISSING'))"


def python_engine(
    script: str,
    **kwargs: Any,
) -> OpenCodeEngine:
    """An engine whose executable is this interpreter running ``script``."""
    return OpenCodeEngine(
        executable=sys.executable,
        subcommand="",
        extra_args=("-c", script),
        **kwargs,
    )


def task(workspace: Any = None, prompt: str = "do the thing") -> CodingTask:
    return CodingTask(
        task_id="t-1",
        prompt=prompt,
        workspace=workspace,
        files=(),
    )


class Recorder:
    """A synchronous event sink that records what it saw."""

    def __init__(self) -> None:
        self.events: list[CodingEvent] = []

    def __call__(self, event: CodingEvent) -> None:
        self.events.append(event)

    def kinds(self) -> list[CodingEventKind]:
        return [event.kind for event in self.events]

    def outputs(self) -> list[str]:
        return [event.line for event in self.events if event.kind is CodingEventKind.OUTPUT]


# --------------------------------------------------------------------------- #
# Constructor validation
# --------------------------------------------------------------------------- #
def test_engine_needs_an_executable() -> None:
    with pytest.raises(InvalidInputError):
        OpenCodeEngine(executable="")


def test_engine_needs_a_positive_timeout() -> None:
    with pytest.raises(InvalidInputError):
        OpenCodeEngine(timeout_seconds=0)


def test_engine_needs_a_non_empty_capture() -> None:
    with pytest.raises(InvalidInputError):
        OpenCodeEngine(max_output_lines=0)


# --------------------------------------------------------------------------- #
# argv construction (the prompt comes last; §9 "input is the prompt")
# --------------------------------------------------------------------------- #
async def test_the_prompt_is_the_last_argv_argument(tmp_path: Any) -> None:
    engine = python_engine(ECHO_ARGV)
    result = await engine.run(task(tmp_path, prompt="fix the bug"))

    assert result.exit_code == 0
    assert json.loads(result.output[0])[-1] == "fix the bug"
    assert result.output[-1] == "PROMPT=fix the bug"


def test_argv_carries_model_provider_and_extra_args() -> None:
    engine = OpenCodeEngine(
        executable="opencode",
        model="m-1",
        provider="p-1",
        extra_args=("--flag",),
    )
    assert engine._argv("do it") == [
        "opencode",
        "run",
        "--model",
        "m-1",
        "--provider",
        "p-1",
        "--flag",
        "do it",
    ]


def test_argv_omits_blank_model_and_provider() -> None:
    engine = python_engine(ECHO_ARGV, model="  ", provider="")
    argv = engine._argv("p")
    assert "--model" not in argv
    assert "--provider" not in argv
    assert argv[-1] == "p"


def test_from_settings_builds_the_configured_engine() -> None:
    settings = OpenCodeSettings(
        path="/usr/local/bin/opencode",
        model="llama3",
        provider="ollama",
        timeout=120,
        max_output_lines=100,
        extra_args="--verbose --color never",
    )
    engine = OpenCodeEngine.from_settings(settings)
    argv = engine._argv("do it")

    assert argv == [
        "/usr/local/bin/opencode",
        "run",
        "--model",
        "llama3",
        "--provider",
        "ollama",
        "--verbose",
        "--color",
        "never",
        "do it",
    ]


# --------------------------------------------------------------------------- #
# Streaming, capture, exit codes
# --------------------------------------------------------------------------- #
async def test_events_stream_lines_and_bracket_the_run(tmp_path: Any) -> None:
    recorder = Recorder()
    result = await python_engine(MANY_LINES).run(task(tmp_path), on_event=recorder)

    assert recorder.kinds()[0] is CodingEventKind.STARTED
    assert recorder.kinds()[-1] is CodingEventKind.COMPLETED
    assert recorder.kinds().count(CodingEventKind.OUTPUT) == 10
    assert recorder.outputs() == [str(i) for i in range(10)]
    assert result.output == tuple(str(i) for i in range(10))
    assert result.truncated is False


async def test_a_non_zero_exit_is_returned_not_raised(tmp_path: Any) -> None:
    recorder = Recorder()
    result = await python_engine("import sys;sys.exit(3)").run(task(tmp_path), on_event=recorder)

    assert result.exit_code == 3
    assert recorder.kinds()[-1] is CodingEventKind.FAILED
    assert recorder.events[-1].exit_code == 3


async def test_capture_is_capped_and_marked_truncated(tmp_path: Any) -> None:
    recorder = Recorder()
    result = await python_engine(MANY_LINES, max_output_lines=3).run(
        task(tmp_path), on_event=recorder
    )

    assert result.output == ("0", "1", "2")
    assert result.truncated is True
    # Every line was still streamed, even though only three were captured.
    assert len(recorder.outputs()) == 10


async def test_run_without_a_listener_still_returns_output(tmp_path: Any) -> None:
    result = await python_engine(ECHO_ARGV).run(task(tmp_path, prompt="quiet"))
    assert "PROMPT=quiet" in result.output
    assert result.engine == "opencode"
    assert result.ran_seconds >= 0.0


# --------------------------------------------------------------------------- #
# Preconditions and failure translation
# --------------------------------------------------------------------------- #
async def test_a_missing_workspace_is_refused(tmp_path: Any) -> None:
    missing = tmp_path / "nope"
    with pytest.raises(WorkspaceError):
        await python_engine(MANY_LINES).run(task(missing))


async def test_a_file_as_workspace_is_refused(tmp_path: Any) -> None:
    a_file = tmp_path / "a-file"
    a_file.write_text("not a directory", encoding="utf-8")
    with pytest.raises(WorkspaceError):
        await python_engine(MANY_LINES).run(task(a_file))


async def test_a_missing_executable_is_a_dependency_error(tmp_path: Any) -> None:
    engine = OpenCodeEngine(executable="definitely-not-a-real-binary-xyz")
    with pytest.raises(DependencyUnavailableError):
        await engine.run(task(tmp_path))


# --------------------------------------------------------------------------- #
# Timeout and cancellation (both must reap the child)
# --------------------------------------------------------------------------- #
async def test_a_timeout_raises_and_settles_the_process(tmp_path: Any) -> None:
    engine = python_engine(SLEEPY, timeout_seconds=0.3)
    with pytest.raises(OperationTimeoutError) as excinfo:
        await engine.run(task(tmp_path))

    assert excinfo.value.details["engine"] == "opencode"


async def test_a_stop_event_cancels_once_a_line_arrives(tmp_path: Any) -> None:
    engine = python_engine(SLEEPY, timeout_seconds=30)
    stop = asyncio.Event()
    recorder = Recorder()

    def on_event(event: CodingEvent) -> None:
        recorder.events.append(event)
        if event.kind is CodingEventKind.OUTPUT:
            stop.set()

    with pytest.raises(OperationCancelledError):
        await engine.run(task(tmp_path), on_event=on_event, stop=stop)

    assert recorder.kinds()[-1] is CodingEventKind.CANCELLED


async def test_caller_cancellation_propagates_and_reaps(tmp_path: Any) -> None:
    engine = python_engine(SLEEPY, timeout_seconds=30)
    run_task = asyncio.create_task(engine.run(task(tmp_path)))
    await asyncio.sleep(0.4)  # let the child start and print its first line
    run_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run_task


# --------------------------------------------------------------------------- #
# Isolation: the child never inherits the server's directories (§9)
# --------------------------------------------------------------------------- #
async def test_pythonpath_is_scrubbed_even_when_supplied(tmp_path: Any) -> None:
    engine = python_engine(
        SHOW_PYTHONPATH,
        env={"PYTHONPATH": "/server/leak"},
    )
    result = await engine.run(task(tmp_path))
    assert result.output[-1] == "PYTHONPATH=MISSING"


async def test_the_supplied_environment_overlays_the_parent(tmp_path: Any) -> None:
    # ``env`` adds/overrides variables; it must never *replace* the parent
    # environment, or PATH/SystemRoot could be lost and the child never start.
    engine = python_engine(SHOW_FOO, env={"FOO": "bar"})
    result = await engine.run(task(tmp_path))
    assert result.output[-1] == "FOO=bar"
