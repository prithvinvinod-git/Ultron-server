"""The workspace manager (T080): §46's git workspace management.

§46 asks for a workspace manager with six responsibilities, all of which the
spec keeps at the *filesystem and git* boundary — the durable record of a
project lives in the ``projects`` table (``ProjectRepository``), while this
module owns what a repository is *on disk* and how a coding agent gets an
isolated copy of it:

```text
/workspaces/
    campuscare/
    ultron/
    deskai/
```

The six are implemented directly: **register projects** (a name-to-root map,
validated against the configured allow-list), **locate repositories** (walk up
from a registered root to the ``.git`` marker), **create isolated workspaces**
(a dedicated directory under the workspace root, populated by a fresh
``git clone`` so the agent starts with a private history), **create branches**
(a dedicated ``<prefix>/<name>`` branch when ``git_isolation`` is on — §9's
"dedicated branch/worktree" for autonomous modifications), **inspect status
and diffs** (``git status --porcelain`` and ``git diff`` as structured
commands, not free-form shell), and **cleanup** (remove the temporary
workspace, always after re-checking it against the allow-list).

Two boundaries are load-bearing, for the security reasons §59.3/§59.4 state:

1. **The allow-list is mandatory.** Every registered project root and every
   created/cleaned workspace path is resolved — symlinks followed, ``..``
   collapsed — *then* tested against `WorkspaceSettings.allowed_paths()`
   (§34, §48). A boundary checked against a raw string is not a boundary, and
   `cleanup` is not allowed to delete a path the manager could not have
   created in the first place. A path outside the workspace is refused here,
   not passed on.

2. **Git is a structured runner, never a shell.** §59.3 draws the line "the
   tool runtime must not be able to reach a general shell to do this". Every
   git operation here is a literal argument vector handed to
   :class:`SubprocessGitRunner` (``git clone``, ``git -C <w> status``, ...) —
   no string interpolation, no ``&&``, no user input in an argv position that
   could reach the shell.

The runner is injected (:class:`GitRunner`), the same seam the agent runtime
and the executor use elsewhere, so unit tests drive the manager over a
recording double while the integration-style tests exercise the real ``git``
binary. Git calls are awaited subprocesses with a timeout and cancellation
safety, and the in-memory registries stay synchronous — they are dict reads,
like `ToolRegistry` (T035) and `AgentRegistry` (T043).
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import stat
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from app.config import WorkspaceSettings
from app.core.errors import (
    ConflictError,
    InvalidInputError,
    NotFoundError,
    OperationTimeoutError,
    WorkspaceError,
)

__all__ = [
    "GitResult",
    "GitRunner",
    "Project",
    "SubprocessGitRunner",
    "Workspace",
    "WorkspaceManager",
]

#: Default ceiling for one git command (§59.6: nothing runs unbounded).
DEFAULT_GIT_TIMEOUT: float = 60.0

#: How long a terminated subprocess may take to actually exit before the
#: manager escalates to ``kill``. Mirrors the opencode engine's own bound.
TERMINATE_GRACE_SECONDS: float = 5.0


@dataclass(frozen=True, slots=True)
class Project:
    """A project the manager knows about: a name and an on-disk root.

    Mirrors the ``projects`` row's shape (``name``, ``root_path``,
    ``description``) without reaching into the database; the manager runs over
    the filesystem, and the repository layer is the durable record. ``root``
    is a real, resolved path — validation happens at registration.
    """

    name: str
    root: Path
    description: str = ""


@dataclass(frozen=True, slots=True)
class Workspace:
    """One isolated working copy, created under the workspace root.

    ``path`` is the working tree the engine runs in; ``branch`` names the
    dedicated branch the agent was given, or ``None`` when isolation was off.
    """

    name: str
    project: str
    path: Path
    branch: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class GitResult:
    """The outcome of one structured git command.

    ``argv`` is the exact vector that ran — the audit trail of what the
    manager asked git to do, never reconstructed from stdout.
    """

    argv: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        """Git's own success verdict: exit code 0."""
        return self.exit_code == 0


@runtime_checkable
class GitRunner(Protocol):
    """What any git-backing runner must provide.

    A ``Protocol`` (not an ABC) so a test double does not need to subclass:
    mirroring how the agent runtime injects its ``StepRunner``, the manager
    asks for a ``run`` coroutine and nothing else.
    """

    async def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout_seconds: float = DEFAULT_GIT_TIMEOUT,
    ) -> GitResult:
        """Run ``argv`` as one git command in ``cwd``, with a bounded time."""
        ...


class SubprocessGitRunner:
    """The default runner: a real ``git`` subprocess, no shell involved.

    ``argv`` goes straight to ``asyncio.create_subprocess_exec`` — there is no
    shell to parse it, which is the whole point of §59.3's "structured tools".
    Output is decoded as UTF-8 with a lossy fallback so a message containing
    an unusual byte cannot crash the manager; the exit code is git's own.
    """

    def __init__(self, executable: str = "git") -> None:
        """Bind to a git binary. ``executable`` defaults to ``git`` on PATH —
        never a hard-coded Windows path (§36)."""
        if not isinstance(executable, str) or not executable.strip():
            raise InvalidInputError(
                "a git executable must be a non-empty name or path",
                details={"executable": str(executable)[:100]},
            )
        self._executable = executable

    async def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout_seconds: float = DEFAULT_GIT_TIMEOUT,
    ) -> GitResult:
        """Run one git command, returning its argv, exit code and output."""
        args = [self._executable, *(str(argument) for argument in argv)]
        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as error:
            raise WorkspaceError(
                "the git executable could not be started",
                details={"argv": tuple(args), "working_directory": str(cwd)},
                cause=error,
            ) from error

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=max(timeout_seconds, 0.001)
            )
        except TimeoutError as error:
            await _settle(process)
            raise OperationTimeoutError(
                "git command",
                timeout=timeout_seconds,
                details={"argv": tuple(args), "working_directory": str(cwd)},
                cause=error,
            ) from error
        except asyncio.CancelledError:
            await _settle(process)
            raise

        return_code = process.returncode
        if return_code is None:  # pragma: no cover - communicate() always reaps
            return_code = await process.wait()
        return GitResult(
            argv=(*args,),
            exit_code=int(return_code),
            stdout=_decode(stdout),
            stderr=_decode(stderr),
        )


class WorkspaceManager:
    """§46's six responsibilities, over a filesystem that is always allow-listed.

    Projects and workspaces are keyed by name (the contract string, like §6's
    agent types); a duplicate registration is a 409 because a silent overwrite
    would rewrite the past (§59.6's argument, applied to names). All operations
    that touch a path validate it against ``WorkspaceSettings.allowed_paths()``
    first — see the module docstring for why that boundary is mandatory.
    """

    def __init__(
        self,
        *,
        settings: WorkspaceSettings,
        runner: GitRunner | None = None,
        branch_prefix: str = "ultron",
        git_isolation: bool = True,
        git_timeout: float = DEFAULT_GIT_TIMEOUT,
    ) -> None:
        """Bind to the workspace slice, a git runner, and isolation policy.

        ``branch_prefix`` is the dedicated-branch prefix (§9, configurable via
        ``OPENCODE_GIT_BRANCH_PREFIX``); ``git_isolation`` switches the clone+
        branch behaviour off for a deployment that wants raw working trees.
        """
        self._settings = settings
        self._runner = runner if runner is not None else SubprocessGitRunner()
        self._branch_prefix = _prefix(branch_prefix)
        self._git_isolation = bool(git_isolation)
        self._git_timeout = _positive(git_timeout, "git_timeout")
        self._projects: dict[str, Project] = {}
        self._workspaces: dict[str, Workspace] = {}

    # ------------------------------------------------------------------ #
    # §46.1 register projects
    # ------------------------------------------------------------------ #
    def register_project(self, name: str, root: str | Path, *, description: str = "") -> Project:
        """Register a project by name and on-disk root.

        The root is resolved and validated **before** it is stored: it must
        exist, it must be a directory, and it must sit inside the configured
        allow-list (`allowed_paths`, the workspace root plus any explicit
        grants). A root outside the boundary is refused here — the manager
        never learns a path it could later be tempted to serve or delete.
        """
        project_name = _require_name(name, "project")
        if project_name in self._projects:
            raise ConflictError(
                f"project '{project_name}' is already registered",
                details={"project": project_name},
            )
        resolved = _resolve(root)
        if not resolved.exists():
            raise NotFoundError("project root", str(resolved))
        if not resolved.is_dir():
            raise InvalidInputError(
                "a project root must be a directory",
                details={"root": str(resolved)},
            )
        self._ensure_allowed(resolved, "project root")

        project = Project(
            name=project_name,
            root=resolved,
            description=_description(description),
        )
        self._projects[project_name] = project
        return project

    def unregister_project(self, name: str) -> Project:
        """Forget a project. Workspaces created from it still exist until
        cleaned up — deleting the mapping never deletes a working tree."""
        project = self._projects.pop(_require_name(name, "project"), None)
        if project is None:
            raise NotFoundError("project", name)
        return project

    def list_projects(self) -> list[Project]:
        """Every registered project, sorted by name (a stable inventory)."""
        return [self._projects[project_name] for project_name in sorted(self._projects)]

    def project(self, name: str) -> Project:
        """One registered project, or 404 with its name."""
        try:
            return self._projects[_require_name(name, "project")]
        except KeyError:
            raise NotFoundError("project", name) from None

    # ------------------------------------------------------------------ #
    # §46.2 locate repositories
    # ------------------------------------------------------------------ #
    async def locate_repository(self, name: str) -> Path:
        """Locate the git repository for a registered project.

        Walks up from the project's root until it finds a ``.git`` entry (a
        directory for a normal checkout, a file for a worktree), so a deep
        ``monorepo/journal/`` root still locates ``monorepo/.git``. No marker
        anywhere up the tree means the project is not under version control —
        the workspaces are clones, so a source that is not a repository has
        nothing to clone.
        """
        project = self.project(name)
        root = project.root
        probe = root
        while True:
            marker = probe / ".git"
            if marker.exists():
                return probe
            if probe.parent == probe:
                break
            probe = probe.parent
        raise WorkspaceError(
            "no git repository found for project",
            details={"project": name, "root": str(root)},
        )

    async def is_repository(self, name: str) -> bool:
        """Whether the registered project names a git repository (§46.2)."""
        try:
            await self.locate_repository(name)
        except WorkspaceError:
            return False
        return True

    # ------------------------------------------------------------------ #
    # §46.3 create isolated workspaces (+ §46.4 create branches)
    # ------------------------------------------------------------------ #
    async def create_workspace(
        self,
        name: str,
        project: str,
        *,
        git_isolation: bool | None = None,
    ) -> Workspace:
        """Create one isolated working copy of a registered project.

        The clone target is ``<workspace root>/<name>`` — inside the
        allow-list by construction, and confirmed again before any filesystem
        action. A fresh ``git clone`` of the located repository gives the
        workspace its own history, so the agent's commits never touch the
        project's working tree. When ``git_isolation`` is on (default: the
        manager's policy; pass an override per call), the clone switches to a
        dedicated branch ``<prefix>/<name>`` — §9's dedicated branch for
        autonomous modifications.
        """
        workspace_name = _require_name(name, "workspace")
        if workspace_name in self._workspaces:
            raise ConflictError(
                f"workspace '{workspace_name}' already exists",
                details={"workspace": workspace_name},
            )
        if workspace_name in self._projects:
            raise ConflictError(
                f"workspace '{workspace_name}' collides with a project",
                details={"workspace": workspace_name},
            )

        source = await self.locate_repository(project)
        target = self._settings.resolved_root() / workspace_name
        self._ensure_allowed(target, "workspace root")
        if target.exists():
            raise ConflictError(
                f"workspace directory '{target}' already exists",
                details={"workspace": workspace_name},
            )
        target.parent.mkdir(parents=True, exist_ok=True)

        result = await self._runner.run(
            ["clone", "--quiet", "--", str(source), str(target)],
            cwd=target.parent,
            timeout_seconds=self._git_timeout,
        )
        if not result.ok:
            raise WorkspaceError(
                "could not clone the project into the workspace",
                details={
                    "workspace": workspace_name,
                    "project": project,
                    "source": str(source),
                    "target": str(target),
                    "git_stderr": _summarise(result.stderr),
                },
            )

        branch: str | None = None
        if self._git_isolation if git_isolation is None else git_isolation:
            branch = f"{self._branch_prefix}/{workspace_name}"
            branch_result = await self._runner.run(
                ["checkout", "--quiet", "-b", branch],
                cwd=target,
                timeout_seconds=self._git_timeout,
            )
            if not branch_result.ok:
                raise WorkspaceError(
                    "could not create the dedicated workspace branch",
                    details={
                        "workspace": workspace_name,
                        "branch": branch,
                        "git_stderr": _summarise(branch_result.stderr),
                    },
                )

        workspace = Workspace(
            name=workspace_name,
            project=project,
            path=target,
            branch=branch,
            created_at=datetime.now(UTC),
        )
        self._workspaces[workspace_name] = workspace
        return workspace

    def workspace(self, name: str) -> Workspace:
        """One live workspace, or 404 with its name."""
        try:
            return self._workspaces[_require_name(name, "workspace")]
        except KeyError:
            raise NotFoundError("workspace", name) from None

    def list_workspaces(self) -> list[Workspace]:
        """Every live workspace, sorted by name."""
        return [self._workspaces[name] for name in sorted(self._workspaces)]

    # ------------------------------------------------------------------ #
    # §46.5 inspect status / generate diffs
    # ------------------------------------------------------------------ #
    async def status(self, name: str) -> GitResult:
        """The workspace's working-tree status as a structured git command.

        ``git status --porcelain=v1`` produces one line per change in a
        machine-stable shape — the same format the agent's report can echo
        without re-parsing prose.
        """
        return await self._runner.run(
            ["status", "--porcelain=v1"],
            cwd=self.workspace(name).path,
            timeout_seconds=self._git_timeout,
        )

    async def diff(self, name: str) -> GitResult:
        """The workspace's unstaged diff, in git's colourless patch form.

        This is the change summary an agent reports (spec §8: "inspect diffs",
        "report changes"); it never includes staged or committed history, which
        belong to git's own inspection surface.
        """
        return await self._runner.run(
            ["diff", "--no-color"],
            cwd=self.workspace(name).path,
            timeout_seconds=self._git_timeout,
        )

    async def current_branch(self, name: str) -> GitResult:
        """The branch the workspace is on, as one clean token (§8: inspect Git)."""
        return await self._runner.run(
            ["branch", "--show-current"],
            cwd=self.workspace(name).path,
            timeout_seconds=self._git_timeout,
        )

    # ------------------------------------------------------------------ #
    # §46.6 cleanup
    # ------------------------------------------------------------------ #
    async def cleanup(self, name: str) -> str:
        """Remove one temporary workspace and forget it.

        Deletion is the one irreversible operation here, so the same
        allow-list test that permitted creation is re-run against the recorded
        path before anything is removed — a workspace can only ever be cleaned
        up inside the boundary that created it. Returns the removed name so a
        caller can report it.
        """
        workspace = self.workspace(name)
        awaited = workspace.path.resolve()
        self._ensure_allowed(awaited, "workspace cleanup")
        if awaited.exists():
            shutil.rmtree(awaited, onexc=_force_unlink)
        del self._workspaces[name]
        return name

    async def cleanup_all(self) -> list[str]:
        """Remove every live workspace, returning the names in sorted order."""
        return [await self.cleanup(name) for name in sorted(self._workspaces)]

    # ------------------------------------------------------------------ #
    # Boundary guard (§59.3/§59.4: the allow-list is the authority)
    # ------------------------------------------------------------------ #
    def _ensure_allowed(self, path: Path, what: str) -> None:
        """Refuse ``path`` unless it resolves inside ``allowed_paths()``."""
        resolved = _resolve(path)
        for root in self._settings.allowed_paths():
            if _is_within(resolved, root):
                return
        raise WorkspaceError(
            f"{what} is outside the allowed workspace paths",
            details={
                what: str(resolved),
                "allowed": [str(root) for root in self._settings.allowed_paths()],
            },
        )


# -------------------------------------------------------------------------- #
# Pure helpers (stateless, mirroring the manager/task split)
# -------------------------------------------------------------------------- #


def _require_name(value: str, what: str) -> str:
    """Validate a project/workspace name: non-empty, and never a path.

    A name that can smuggle ``../`` or a separator would let an agent read or
    write outside its workspace, so ``/``, ``\\``, stepping and the empty
    string are all refused up front (422).
    """
    if not isinstance(value, str) or not value.strip():
        raise InvalidInputError(
            f"a {what} needs a non-empty name", details={what: str(value)[:100]}
        )
    if "/" in value or "\\" in value or value == "." or value == "..":
        raise InvalidInputError(
            f"a {what} name must not contain path separators",
            details={what: str(value)[:100]},
        )
    return value


def _prefix(value: str) -> str:
    """Normalise the branch prefix to a bare, separator-free slug."""
    if not isinstance(value, str) or not value.strip():
        return "ultron"
    return _require_name(value, "branch_prefix")


def _description(value: str) -> str:
    return value if isinstance(value, str) else ""


def _resolve(path: str | Path) -> Path:
    """Realpath with ``..`` collapsed — a boundary must be checked on the
    resolved path (§59.4)."""
    return Path(path).expanduser().resolve()


def _is_within(child: Path, parent: Path) -> bool:
    """Whether ``child`` is ``parent`` or lives underneath it."""
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def _positive(value: float, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"{what} must be a positive number")
    return float(value)


def _summarise(stderr: str) -> str:
    """The actionable tail of git's stderr, bounded for the error payload."""
    return stderr.strip().splitlines()[-1] if stderr.strip() else ""


def _force_unlink(func: Callable[[str], object], path: str, exc: BaseException) -> None:
    """Retry a failed unlink/rmdir once the read-only bit is cleared.

    ``shutil.rmtree`` reports some Windows removals as denied because the
    target still carries the read-only attribute — git leaves packed files and
    loose objects that way deliberately. The attribute is wiped and the exact
    failed call retried, so a cleanup is not refused by the filesystem's own
    permission bit. Any real error still propagates: this helper only retries
    once the cause it can fix is gone.
    """
    del exc  # unused: the retry is unconditional after clearing the bit
    with contextlib.suppress(OSError):
        Path(path).chmod(stat.S_IWRITE)
    func(path)  # a genuine failure still propagates to ``shutil.rmtree``


def _decode(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace")


async def _settle(process: asyncio.subprocess.Process) -> None:
    """Cleanly stop a subprocess and always reap it (process cleanup).

    Used when a command times out or a caller cancels an await. The child is
    terminated first and given a short grace; only if it refuses to die is it
    killed. The final ``wait`` guarantees the process is reaped — no zombie —
    which is why this helper is awaited even in the cancellation path.
    """
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=TERMINATE_GRACE_SECONDS)
    except TimeoutError:
        process.kill()
        with contextlib.suppress(asyncio.CancelledError, TimeoutError):
            await process.wait()
    except asyncio.CancelledError:
        process.kill()
        with contextlib.suppress(asyncio.CancelledError, TimeoutError):
            await process.wait()
        raise
