"""CONTROLLED git tools (T092, spec §59.3) — worktree mutations.

These change the repository's working tree but not published history, so §59.3
places them in the middle tier: the tool declares ``MODIFY_PROJECT`` and the
permission layer may run them once the project is authorized (no explicit
per-operation confirmation). ``pull`` and ``stash`` are partial: their effect
can usually be undone, but not always, hence ``PARTIALLY_REVERSIBLE``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from app.database.models import PermissionLevel
from app.tools.arguments import as_bool, as_str
from app.tools.base import Reversibility
from app.tools.git.base import (
    PATH_PROPERTY,
    GitTool,
    require_ref,
    require_revision,
)


class GitCreateBranchTool(GitTool):
    """``git branch <name> [<start>]`` — create a branch."""

    name = "git.create_branch"
    description = "Create a local branch, optionally from a given start point."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": PATH_PROPERTY,
            "name": {"type": "string", "minLength": 1},
            "start_point": {"type": "string"},
        },
        "required": ["path", "name"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.MODIFY_PROJECT
    timeout = 30.0
    reversibility = Reversibility.PARTIALLY_REVERSIBLE

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        """Run ``git branch`` (branch creation does not checkout)."""
        cwd = self._repo(arguments)
        argv = ["branch", require_ref(arguments.get("name"), "name")]
        if arguments.get("start_point") is not None:
            argv.append(require_revision(arguments["start_point"], "start_point"))
        return await self._git(cwd, argv)


class GitSwitchBranchTool(GitTool):
    """``git switch <name>`` — change the checked-out branch."""

    name = "git.switch_branch"
    description = "Switch the working tree to another branch, optionally creating it."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": PATH_PROPERTY,
            "name": {"type": "string", "minLength": 1},
            "create": {"type": "boolean", "default": False},
        },
        "required": ["path", "name"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.MODIFY_PROJECT
    timeout = 30.0
    reversibility = Reversibility.PARTIALLY_REVERSIBLE

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        """Run ``git switch``, adding ``-c`` when creating."""
        cwd = self._repo(arguments)
        name = require_ref(arguments.get("name"), "name")
        argv = ["switch"]
        if as_bool(arguments.get("create"), "create"):
            argv.append("-c")
        argv.append(name)
        return await self._git(cwd, argv)


class GitCommitTool(GitTool):
    """``git commit`` — record the staged (or all tracked) changes."""

    name = "git.commit"
    description = "Create a commit from staged changes (optionally staging tracked changes first)."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": PATH_PROPERTY,
            "message": {"type": "string", "minLength": 1},
            "add_all": {"type": "boolean", "default": False},
        },
        "required": ["path", "message"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.MODIFY_PROJECT
    timeout = 60.0
    reversibility = Reversibility.PARTIALLY_REVERSIBLE

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        """Run ``git commit -m <message>`` (``-a`` when ``add_all``)."""
        cwd = self._repo(arguments)
        message = as_str(arguments.get("message"), "message")
        argv = ["commit", "-m", message]
        if as_bool(arguments.get("add_all"), "add_all"):
            argv.append("-a")
        return await self._git(cwd, argv)


class GitPullTool(GitTool):
    """``git pull`` — fetch and integrate from a remote (CONTROLLED, partial)."""

    name = "git.pull"
    description = "Fetch from a remote and integrate the changes into the current branch."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": PATH_PROPERTY,
            "remote": {"type": "string"},
            "branch": {"type": "string"},
        },
        "required": ["path"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.MODIFY_PROJECT
    timeout = 120.0
    reversibility = Reversibility.PARTIALLY_REVERSIBLE

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        """Run ``git pull [<remote> [<branch>]]``."""
        cwd = self._repo(arguments)
        argv = ["pull"]
        if arguments.get("remote") is not None:
            argv.append(require_ref(arguments["remote"], "remote"))
        if arguments.get("branch") is not None:
            argv.append(require_ref(arguments["branch"], "branch"))
        return await self._git(cwd, argv)


class GitStashPushTool(GitTool):
    """``git stash push`` — shelve working-tree changes."""

    name = "git.stash_push"
    description = "Stash the current working-tree changes."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"path": PATH_PROPERTY},
        "required": ["path"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.MODIFY_PROJECT
    timeout = 30.0
    reversibility = Reversibility.PARTIALLY_REVERSIBLE

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        """Run ``git stash push``."""
        return await self._git(self._repo(arguments), ["stash", "push"])


class GitStashPopTool(GitTool):
    """``git stash pop`` — restore the most recent stash."""

    name = "git.stash_pop"
    description = "Restore the most recently stashed changes to the working tree."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"path": PATH_PROPERTY},
        "required": ["path"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.MODIFY_PROJECT
    timeout = 30.0
    reversibility = Reversibility.PARTIALLY_REVERSIBLE

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        """Run ``git stash pop``."""
        return await self._git(self._repo(arguments), ["stash", "pop"])


class GitWorktreeAddTool(GitTool):
    """``git worktree add <path> <ref>`` — attach a new working tree."""

    name = "git.worktree_add"
    description = "Create a new linked worktree inside the workspace for a branch or commit."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": PATH_PROPERTY,
            "worktree": {"type": "string", "minLength": 1},
            "ref": {"type": "string", "minLength": 1},
        },
        "required": ["path", "worktree", "ref"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.MODIFY_PROJECT
    timeout = 60.0
    reversibility = Reversibility.PARTIALLY_REVERSIBLE

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        """Run ``git worktree add`` against a sandboxed destination."""
        cwd = self._repo(arguments)
        destination = self._workspace_path(arguments.get("worktree"), "worktree")
        ref = require_revision(arguments.get("ref"), "ref")
        return await self._git(cwd, ["worktree", "add", destination, ref])


class GitWorktreeRemoveTool(GitTool):
    """``git worktree remove <path>`` — detach a working tree."""

    name = "git.worktree_remove"
    description = "Remove a linked worktree from the repository."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": PATH_PROPERTY,
            "worktree": {"type": "string", "minLength": 1},
        },
        "required": ["path", "worktree"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.MODIFY_PROJECT
    timeout = 30.0
    reversibility = Reversibility.PARTIALLY_REVERSIBLE

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        """Run ``git worktree remove`` against a sandboxed target."""
        cwd = self._repo(arguments)
        target = self._workspace_path(arguments.get("worktree"), "worktree")
        return await self._git(cwd, ["worktree", "remove", target])


__all__ = [
    "GitCommitTool",
    "GitCreateBranchTool",
    "GitPullTool",
    "GitStashPopTool",
    "GitStashPushTool",
    "GitSwitchBranchTool",
    "GitWorktreeAddTool",
    "GitWorktreeRemoveTool",
]
