"""Shared plumbing for the git tools (T092, spec §59.3).

§59.3 requires git to be reachable only as structured tools — "the tool runtime
must not be able to reach a general shell to do this" — and requires the
permission tier to be a *declaration on the tool*, not a check inside the
handler. This base holds the two shared dependencies (a :class:`GitRunner` and
the filesystem sandbox) and the two shared guards:

- every tool names its repository by an absolute path, resolved through the
  sandbox so a git command cannot be pointed outside the workspace; and
- ref/revision arguments reject a leading ``-`` so a crafted name can never
  become a git *option* (the argument-injection form of "no shell").

The real work is done by the same :class:`~app.workspaces.manager.SubprocessGitRunner`
the workspace manager uses, so git runs with literal argv and never a shell.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from app.core.errors import InvalidInputError
from app.database.models import VerificationOutcome
from app.tools.arguments import as_str
from app.tools.base import Tool
from app.tools.filesystem.sandbox import FilesystemSandbox
from app.workspaces.manager import GitResult, GitRunner

__all__ = ["GitTool", "require_ref", "require_revision"]

PATH_PROPERTY: dict[str, Any] = {"type": "string", "minLength": 1}

_FORBIDDEN_REF_CHARS = frozenset(" \t\n\r~^:?*[\\")
_FORBIDDEN_REVISION_CHARS = frozenset(" \t\n\r\0")


def require_ref(value: Any, name: str) -> str:
    """Validate a git branch/reference name (git's own ref rules, tightened)."""
    ref = as_str(value, name)
    if ref.startswith("-") or ref.startswith("/") or ref.startswith("."):
        raise InvalidInputError(
            f"{name} must not start with '-', '/' or '.'", details={name: ref[:100]}
        )
    if ref.endswith("/") or ref.endswith(".lock") or ".." in ref or "@{" in ref:
        raise InvalidInputError(f"{name} is not a valid git reference", details={name: ref[:100]})
    if any(char in _FORBIDDEN_REF_CHARS for char in ref):
        raise InvalidInputError(f"{name} is not a valid git reference", details={name: ref[:100]})
    return ref


def require_revision(value: Any, name: str) -> str:
    """Validate a git revision or range (allows ``..``, ``^``, ``~``)."""
    revision = as_str(value, name)
    if revision.startswith("-"):
        raise InvalidInputError(f"{name} must not start with '-'", details={name: revision[:100]})
    if any(char in _FORBIDDEN_REVISION_CHARS for char in revision):
        raise InvalidInputError(
            f"{name} is not a valid git revision", details={name: revision[:100]}
        )
    return revision


class GitTool(Tool):
    """Base for git tools; carries the runner and the workspace boundary."""

    def __init__(self, runner: GitRunner, sandbox: FilesystemSandbox) -> None:
        """Keep git and the filesystem boundary explicit, not global."""
        self._runner = runner
        self._sandbox = sandbox

    @property
    def sandbox(self) -> FilesystemSandbox:
        """The sandbox every repository path is resolved through."""
        return self._sandbox

    def _repo(self, arguments: Mapping[str, Any]) -> Path:
        """Resolve the ``path`` argument to an existing directory in the workspace."""
        return self._sandbox.resolve_directory(
            as_str(arguments.get("path"), "path"), what="repository"
        )

    def _workspace_path(self, value: Any, name: str) -> str:
        """Resolve a to-be-created path (e.g. a worktree) inside the workspace."""
        return str(self._sandbox.resolve(as_str(value, name), what=name))

    async def _git(
        self, cwd: Path, argv: Sequence[str], *, timeout_seconds: float | None = None
    ) -> dict[str, Any]:
        """Run one literal git command and return its structured result."""
        result: GitResult = await self._runner.run(
            tuple(argv), cwd=cwd, timeout_seconds=timeout_seconds or self.timeout
        )
        return {
            "cwd": str(cwd),
            "argv": tuple(result.argv),
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    async def verify(self, result: Any, arguments: Mapping[str, Any]) -> VerificationOutcome:
        """The post-condition is simply that git accepted the command."""
        exit_code = result.get("exit_code") if isinstance(result, Mapping) else None
        return VerificationOutcome.SUCCESS if exit_code == 0 else VerificationOutcome.FAILED
