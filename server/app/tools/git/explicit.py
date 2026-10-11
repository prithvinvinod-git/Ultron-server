"""EXPLICIT-AUTHORIZATION git tools (T092, spec §59.3) — history/remote changes.

§59.3's top tier: ``push``, force-push, ``reset --hard``, ``clean -fd``,
``branch -D``, rebase. Each declares ``DESTRUCTIVE`` and ``IRREVERSIBLE``, which
feeds §66.17's strong-confirmation path, and each sets ``audit`` because these
are the operations §59.6 treats as sensitive *beyond* the permission log every
call already gets. Like the rest of the family they run through the same
literal-argv runner and never a shell.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from app.core.errors import InvalidInputError
from app.database.models import PermissionLevel
from app.tools.arguments import as_bool, as_str
from app.tools.base import Reversibility
from app.tools.git.base import PATH_PROPERTY, GitTool, require_ref, require_revision

_RESET_MODES = frozenset({"soft", "mixed", "hard"})


class GitPushTool(GitTool):
    """``git push`` — publish local commits to a remote."""

    name = "git.push"
    description = "Push the current branch to a remote, optionally with force-with-lease."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": PATH_PROPERTY,
            "remote": {"type": "string", "default": "origin"},
            "branch": {"type": "string"},
            "force": {"type": "boolean", "default": False},
        },
        "required": ["path"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.DESTRUCTIVE
    timeout = 120.0
    reversibility = Reversibility.IRREVERSIBLE
    audit = True

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        """Run ``git push`` (force uses the safer ``--force-with-lease``)."""
        cwd = self._repo(arguments)
        remote = require_ref(arguments.get("remote", "origin"), "remote")
        argv = ["push"]
        if as_bool(arguments.get("force"), "force"):
            argv.append("--force-with-lease")
        argv.append(remote)
        if arguments.get("branch") is not None:
            argv.append(require_ref(arguments["branch"], "branch"))
        return await self._git(cwd, argv)


class GitResetTool(GitTool):
    """``git reset`` — move HEAD and optionally the working tree."""

    name = "git.reset"
    description = "Reset the current branch to a revision (soft, mixed or hard)."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": PATH_PROPERTY,
            "target": {"type": "string", "minLength": 1},
            "mode": {"type": "string", "enum": ["soft", "mixed", "hard"], "default": "hard"},
        },
        "required": ["path", "target"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.DESTRUCTIVE
    timeout = 30.0
    reversibility = Reversibility.IRREVERSIBLE
    audit = True

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        """Run ``git reset --<mode> <target>``."""
        cwd = self._repo(arguments)
        target = require_revision(arguments.get("target"), "target")
        mode = as_str(arguments.get("mode"), "mode", default="hard")
        if mode not in _RESET_MODES:
            raise InvalidInputError("mode must be one of soft, mixed, hard", details={"mode": mode})
        return await self._git(cwd, ["reset", f"--{mode}", target])


class GitCleanTool(GitTool):
    """``git clean`` — delete untracked files."""

    name = "git.clean"
    description = "Remove untracked files (and optionally directories) from the working tree."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": PATH_PROPERTY,
            "directories": {"type": "boolean", "default": True},
            "force": {"type": "boolean", "default": True},
        },
        "required": ["path"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.DESTRUCTIVE
    timeout = 60.0
    reversibility = Reversibility.IRREVERSIBLE
    audit = True

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        """Run ``git clean``; directories require ``directories`` to stay true."""
        cwd = self._repo(arguments)
        if not as_bool(arguments.get("force"), "force", default=True):
            raise InvalidInputError("clean requires force=true (spec 59.3: clean -fd)")
        flags = (
            "-fd" if as_bool(arguments.get("directories"), "directories", default=True) else "-f"
        )
        return await self._git(cwd, ["clean", flags])


class GitDeleteBranchTool(GitTool):
    """``git branch -d/-D`` — delete a branch."""

    name = "git.delete_branch"
    description = "Delete a local branch, forcibly with force=true."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": PATH_PROPERTY,
            "name": {"type": "string", "minLength": 1},
            "force": {"type": "boolean", "default": False},
        },
        "required": ["path", "name"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.DESTRUCTIVE
    timeout = 30.0
    reversibility = Reversibility.IRREVERSIBLE
    audit = True

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        """Run ``git branch -d`` or ``-D``."""
        cwd = self._repo(arguments)
        name = require_ref(arguments.get("name"), "name")
        flag = "-D" if as_bool(arguments.get("force"), "force") else "-d"
        return await self._git(cwd, ["branch", flag, name])


class GitRebaseTool(GitTool):
    """``git rebase`` — replay commits onto another base."""

    name = "git.rebase"
    description = "Rebase the current branch onto another revision."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"path": PATH_PROPERTY, "onto": {"type": "string", "minLength": 1}},
        "required": ["path", "onto"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.DESTRUCTIVE
    timeout = 60.0
    reversibility = Reversibility.IRREVERSIBLE
    audit = True

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        """Run ``git rebase <onto>``."""
        cwd = self._repo(arguments)
        onto = require_revision(arguments.get("onto"), "onto")
        return await self._git(cwd, ["rebase", onto])


__all__ = [
    "GitCleanTool",
    "GitDeleteBranchTool",
    "GitPushTool",
    "GitRebaseTool",
    "GitResetTool",
]
