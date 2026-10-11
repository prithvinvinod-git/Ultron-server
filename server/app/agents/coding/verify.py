"""Coding verification (T084): turn a finished run into a typed outcome (§9, §17).

§17 is blunt — "never assume success" — and §9 says what success means
concretely: the process exited zero and the changes it claimed are actually
there. This module is that judgement, and nothing else: a pure function over
the engine's :class:`CodingRunResult` and the :class:`CodingTask` that was
asked for, returning a :class:`VerificationOutcome` (the enum is verbatim from
§17, ``enums.py:162``).

The mapping, and the spec each line answers:

- **Non-zero exit → FAILED.** §9: "if the exit code is non-zero, this is a
  work failure." The engine reported the fact; this is where it becomes a
  verdict.
- **Exit 0, no expected files → UNVERIFIED.** A clean exit with nothing to
  check is *not* success (§17 forbids assuming it): the run may have been a
  no-op, a question, or a change nobody declared. Honest answer: unverified.
- **Exit 0, every expected file present → SUCCESS.**
- **Exit 0, only some present → PARTIAL.** A partial edit is a real, common
  outcome; flattening it to FAILED would lose the fact that work happened, and
  to SUCCESS would be the lie §17 warns about.

"Present" means the file exists in the workspace, is a regular file, and has
content (>0 bytes): an empty file usually means the write did not land, and a
directory where a file was expected is not the change that was promised.
"""

from __future__ import annotations

from app.database.models import VerificationOutcome

from .engine import CodingRunResult, CodingTask

__all__ = ["expected_files_present", "verify_coding_result"]


def expected_files_present(task: CodingTask) -> tuple[str, ...]:
    """The task's expected files that actually exist in the workspace with content.

    Returned in the task's own order so a report reads back the ask, not a
    filesystem listing. Files that are missing, directories, empty, or
    unreadable are simply absent from the result — verification counts what it
    can prove, never what it hopes.
    """
    present: list[str] = []
    for relative in task.files:
        candidate = task.workspace / relative
        try:
            if candidate.is_file() and candidate.stat().st_size > 0:
                present.append(relative)
        except OSError:
            # A path the process cannot stat is not a path we can verify.
            continue
    return tuple(present)


def verify_coding_result(result: CodingRunResult, task: CodingTask) -> VerificationOutcome:
    """Decide what a finished coding run means (spec section 9 and 17).

    Pure and total: any :class:`CodingRunResult` maps to exactly one outcome.
    """
    if result.exit_code != 0:
        return VerificationOutcome.FAILED
    if not task.files:
        return VerificationOutcome.UNVERIFIED
    present = expected_files_present(task)
    if len(present) == len(task.files):
        return VerificationOutcome.SUCCESS
    return VerificationOutcome.PARTIAL
