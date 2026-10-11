"""The git tool family (T092/T230, spec §59.3).

``build_git_tools`` returns every git capability the runtime exposes, in three
tiers, so §16 can enforce each tool's declared level without the tool checking
anything itself (§66.7). Git is *only* reachable through these tools — there is
no shell fallback — and every one runs literal argv via the workspace's
:class:`~app.workspaces.manager.SubprocessGitRunner` against a sandboxed path.
"""

from __future__ import annotations

from app.tools.base import Tool
from app.tools.filesystem.sandbox import FilesystemSandbox
from app.tools.git.base import GitTool
from app.tools.git.controlled import (
    GitCommitTool,
    GitCreateBranchTool,
    GitPullTool,
    GitStashPopTool,
    GitStashPushTool,
    GitSwitchBranchTool,
    GitWorktreeAddTool,
    GitWorktreeRemoveTool,
)
from app.tools.git.explicit import (
    GitCleanTool,
    GitDeleteBranchTool,
    GitPushTool,
    GitRebaseTool,
    GitResetTool,
)
from app.tools.git.safe import (
    GitBranchTool,
    GitDiffTool,
    GitLogTool,
    GitShowTool,
    GitStatusTool,
    GitWorktreeListTool,
)
from app.workspaces.manager import GitRunner

__all__ = ["GitTool", "build_git_tools"]


def build_git_tools(runner: GitRunner, sandbox: FilesystemSandbox) -> tuple[Tool, ...]:
    """Construct every git tool bound to ``runner`` and ``sandbox``."""
    return (
        # SAFE — read-only.
        GitStatusTool(runner, sandbox),
        GitDiffTool(runner, sandbox),
        GitLogTool(runner, sandbox),
        GitBranchTool(runner, sandbox),
        GitShowTool(runner, sandbox),
        GitWorktreeListTool(runner, sandbox),
        # CONTROLLED — working-tree mutation.
        GitCreateBranchTool(runner, sandbox),
        GitSwitchBranchTool(runner, sandbox),
        GitCommitTool(runner, sandbox),
        GitPullTool(runner, sandbox),
        GitStashPushTool(runner, sandbox),
        GitStashPopTool(runner, sandbox),
        GitWorktreeAddTool(runner, sandbox),
        GitWorktreeRemoveTool(runner, sandbox),
        # EXPLICIT AUTHORIZATION — history/remote destruction.
        GitPushTool(runner, sandbox),
        GitResetTool(runner, sandbox),
        GitCleanTool(runner, sandbox),
        GitDeleteBranchTool(runner, sandbox),
        GitRebaseTool(runner, sandbox),
    )
