"""Coding verification tests (T084, spec sections 9 and 17).

Pure and exhaustive: :func:`verify_coding_result` is a total function, so every
arm of §9's exit-code-plus-files rule is one test. No engine, no subprocess — a
:class:`CodingRunResult` is built directly and a small workspace on ``tmp_path``
stands in for what the engine would have produced.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.coding import CodingRunResult, CodingTask, expected_files_present
from app.agents.coding.verify import verify_coding_result
from app.database.models import VerificationOutcome

pytestmark = pytest.mark.unit


def result(*, exit_code: int = 0) -> CodingRunResult:
    return CodingRunResult(
        task_id="t-1",
        engine="fake",
        exit_code=exit_code,
        output=(),
        truncated=False,
        ran_seconds=0.1,
    )


def task(workspace: Path, files: tuple[str, ...]) -> CodingTask:
    return CodingTask(task_id="t-1", prompt="do it", workspace=workspace, files=files)


def test_a_non_zero_exit_is_failed(tmp_path: Path) -> None:
    assert (
        verify_coding_result(result(exit_code=1), task(tmp_path, ())) is VerificationOutcome.FAILED
    )


def test_a_clean_exit_with_nothing_to_check_is_unverified(tmp_path: Path) -> None:
    assert verify_coding_result(result(), task(tmp_path, ())) is VerificationOutcome.UNVERIFIED


def test_all_expected_files_present_is_success(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("y = 2\n", encoding="utf-8")
    outcome = verify_coding_result(result(), task(tmp_path, ("a.py", "b.py")))
    assert outcome is VerificationOutcome.SUCCESS


def test_some_expected_files_present_is_partial(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    outcome = verify_coding_result(result(), task(tmp_path, ("a.py", "b.py")))
    assert outcome is VerificationOutcome.PARTIAL


def test_no_expected_files_present_is_partial(tmp_path: Path) -> None:
    outcome = verify_coding_result(result(), task(tmp_path, ("a.py", "b.py")))
    assert outcome is VerificationOutcome.PARTIAL


def test_an_empty_file_is_not_considered_present(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("", encoding="utf-8")
    assert verify_coding_result(result(), task(tmp_path, ("a.py",))) is VerificationOutcome.PARTIAL


def test_a_directory_where_a_file_is_expected_is_not_present(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    assert verify_coding_result(result(), task(tmp_path, ("pkg",))) is VerificationOutcome.PARTIAL


def test_expected_files_present_keeps_the_task_order(tmp_path: Path) -> None:
    (tmp_path / "b.py").write_text("b\n", encoding="utf-8")
    (tmp_path / "a.py").write_text("a\n", encoding="utf-8")
    present = expected_files_present(task(tmp_path, ("a.py", "missing.py", "b.py")))
    assert present == ("a.py", "b.py")
