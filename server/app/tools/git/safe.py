"""SAFE git tools (T092, spec §59.3) — read-only inspection.

Everything here only reads the repository, so each tool declares
``READ_ONLY``/``REVERSIBLE``; §16 sees that declaration and the permission
layer allows it without a prompt. The repository is always named by an absolute
``path`` resolved through the sandbox, and refs/revisions are validated so a
name can never turn into a git option (§59.3 "no shell fallback").
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from app.database.models import PermissionLevel
from app.tools.arguments import as_bool, as_int
from app.tools.base import Reversibility
from app.tools.git.base import PATH_PROPERTY, GitTool, require_revision


class GitStatusTool(GitTool):
    """``git status`` — working-tree state for a repository."""

    name = "git.status"
    description = "Report the working-tree status of a repository in the workspace."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"path": PATH_PROPERTY, "porcelain": {"type": "boolean", "default": True}},
        "required": ["path"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.READ_ONLY
    timeout = 30.0
    reversibility = Reversibility.REVERSIBLE

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        """Run ``git status`` (porcelain by default)."""
        cwd = self._repo(arguments)
        argv = ["status"]
        if as_bool(arguments.get("porcelain"), "porcelain", default=True):
            argv.append("--porcelain=v1")
        return await self._git(cwd, argv)


class GitDiffTool(GitTool):
    """``git diff`` — unstaged or staged changes."""

    name = "git.diff"
    description = "Show changes in a repository, optionally staged or against a revision."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": PATH_PROPERTY,
            "staged": {"type": "boolean", "default": False},
            "revision": {"type": "string"},
        },
        "required": ["path"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.READ_ONLY
    timeout = 30.0
    reversibility = Reversibility.REVERSIBLE

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        """Run ``git diff`` with optional ``--cached`` and a revision range."""
        cwd = self._repo(arguments)
        argv = ["diff"]
        if as_bool(arguments.get("staged"), "staged"):
            argv.append("--cached")
        if arguments.get("revision") is not None:
            argv.append(require_revision(arguments["revision"], "revision"))
        return await self._git(cwd, argv)


class GitLogTool(GitTool):
    """``git log`` — recent commits."""

    name = "git.log"
    description = "List recent commits in a repository."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": PATH_PROPERTY,
            "limit": {"type": "integer", "minimum": 1, "default": 20},
            "oneline": {"type": "boolean", "default": True},
        },
        "required": ["path"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.READ_ONLY
    timeout = 30.0
    reversibility = Reversibility.REVERSIBLE

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        """Run ``git log -n<limit>``."""
        cwd = self._repo(arguments)
        limit = as_int(arguments.get("limit"), "limit", default=20, minimum=1)
        argv = ["log", f"-n{limit}"]
        if as_bool(arguments.get("oneline"), "oneline", default=True):
            argv.append("--oneline")
        return await self._git(cwd, argv)


class GitBranchTool(GitTool):
    """``git branch`` — list local branches (SAFE form of the branch operation)."""

    name = "git.branch"
    description = "List local branches in a repository."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"path": PATH_PROPERTY},
        "required": ["path"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.READ_ONLY
    timeout = 30.0
    reversibility = Reversibility.REVERSIBLE

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        """Run ``git branch --list``."""
        return await self._git(self._repo(arguments), ["branch", "--list"])


class GitShowTool(GitTool):
    """``git show`` — one commit/object with a diffstat."""

    name = "git.show"
    description = "Show a single commit or object with a diffstat."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"path": PATH_PROPERTY, "revision": {"type": "string", "minLength": 1}},
        "required": ["path", "revision"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.READ_ONLY
    timeout = 30.0
    reversibility = Reversibility.REVERSIBLE

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        """Run ``git show --stat <revision>``."""
        cwd = self._repo(arguments)
        revision = require_revision(arguments.get("revision"), "revision")
        return await self._git(cwd, ["show", "--stat", revision])


class GitWorktreeListTool(GitTool):
    """``git worktree list`` — existing worktrees."""

    name = "git.worktree_list"
    description = "List the worktrees attached to a repository."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"path": PATH_PROPERTY},
        "required": ["path"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.READ_ONLY
    timeout = 30.0
    reversibility = Reversibility.REVERSIBLE

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        """Run ``git worktree list --porcelain``."""
        return await self._git(self._repo(arguments), ["worktree", "list", "--porcelain"])


__all__ = [
    "GitBranchTool",
    "GitDiffTool",
    "GitLogTool",
    "GitShowTool",
    "GitStatusTool",
    "GitWorktreeListTool",
]
