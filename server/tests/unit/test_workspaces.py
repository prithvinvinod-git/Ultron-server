"""Unit tests for the workspace manager (T080, spec sections 46, 59.3, 59.4).

§46's six responsibilities are exercised under two regimes:

- **A recording runner** drives the command *contract*: registration,
  allow-list enforcement, the exact argv vectors built for clone/branch/
  status/diff, and cleanup. The manager never touches a real ``git`` here, so
  a broken argv (a shell-unsafe interpolated string, a missing ``--``) fails
  loudly instead of being hidden behind git's own flexibility.
- **Real ``git``** (skipped when the binary is absent) proves the whole
  clone → branch → status → diff → cleanup cycle against an actual repository,
  the shape a coding task will rely on end to end.

The allow-list that §59.4 makes mandatory is checked with a project root
*outside* the workspace boundary: a root the operator never granted must be
refused before the manager learns of it.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from app.config import WorkspaceSettings
from app.core.errors import (
    ConflictError,
    ErrorCode,
    InvalidInputError,
    NotFoundError,
    WorkspaceError,
)
from app.workspaces.manager import GitResult, Project, SubprocessGitRunner, WorkspaceManager

pytestmark = pytest.mark.unit

REQUIRES_GIT = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

WORKSPACE_ROOT = "ws"


def make_manager(
    tmp_path: Path,
    *,
    allowed: list[str] | None = None,
    runner: RecordingGitRunner | None = None,
    branch_prefix: str = "ultron",
    git_isolation: bool = True,
    git_timeout: float = 60.0,
) -> tuple[WorkspaceManager, Path]:
    """A manager whose workspace root lives under ``tmp_path``.

    Project sources register under a default grant of the whole ``tmp_path`` so
    most tests stay simple; boundary tests pass an explicit ``allowed`` list and
    verify a root outside it is refused (which is the §59.4 check).
    """
    root = tmp_path / WORKSPACE_ROOT
    grants = [str(tmp_path)] if allowed is None else allowed
    settings = WorkspaceSettings(root=str(root), allowed_roots=grants)
    manager = WorkspaceManager(
        settings=settings,
        runner=runner,
        branch_prefix=branch_prefix,
        git_isolation=git_isolation,
        git_timeout=git_timeout,
    )
    return manager, root


class RecordingGitRunner:
    """A double that records every argv/cwd/timeout and answers per command.

    The manager's contract is the *vector*; a double that echoes whatever it is
    asked proves the manager asked for exactly the structured command §59.3
    requires, while never contracting with git's own behaviour.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], str, float]] = []
        self.failing: set[tuple[str, ...]] = set()

    def fail(self, *pattern: str) -> None:
        """Make any command whose argv starts with ``pattern`` return 1."""
        self.failing.add(tuple(pattern))

    async def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout_seconds: float = 60.0,
    ) -> GitResult:
        key = tuple(argv)
        self.calls.append((key, str(cwd), timeout_seconds))
        if any(key[: len(pattern)] == pattern for pattern in self.failing):
            return GitResult(argv=key, exit_code=1, stdout="", stderr="boom\n")
        return GitResult(argv=key, exit_code=0, stdout="", stderr="")

    def matching(self, *pattern: str) -> list[tuple[tuple[str, ...], str]]:
        """The argv/cwd pairs for every call matching ``pattern`` up front."""
        needle = tuple(pattern)
        return [(key, cwd) for key, cwd, _ in self.calls if key[: len(needle)] == needle]


def make_project_root(tmp_path: Path, name: str = "src") -> Path:
    """A directory that looks like a git checkout to the locator."""
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    (root / ".git").mkdir(exist_ok=True)
    (root / "README.md").write_text("# sample\n", encoding="utf-8")
    return root


# --------------------------------------------------------------------------- #
# §46.1 register projects
# --------------------------------------------------------------------------- #
def test_register_project_stores_a_resolved_project(tmp_path: Path) -> None:
    manager, _ = make_manager(tmp_path)
    src = make_project_root(tmp_path)

    project = manager.register_project("sample", src, description="spec 46 fixture")

    assert isinstance(project, Project)
    assert project.name == "sample"
    assert project.root == src.resolve()
    assert project.description == "spec 46 fixture"
    assert manager.project("sample").root == src.resolve()
    assert manager.list_projects() == [project]


def test_register_rejects_a_duplicate_name(tmp_path: Path) -> None:
    manager, _ = make_manager(tmp_path)
    src = make_project_root(tmp_path)

    manager.register_project("sample", src)
    with pytest.raises(ConflictError) as excinfo:
        manager.register_project("sample", src)

    assert excinfo.value.code is ErrorCode.CONFLICT
    assert excinfo.value.details["project"] == "sample"


def test_register_refuses_a_missing_root(tmp_path: Path) -> None:
    manager, _ = make_manager(tmp_path)

    with pytest.raises(NotFoundError) as excinfo:
        manager.register_project("ghost", tmp_path / "does-not-exist")

    assert excinfo.value.identifier == str((tmp_path / "does-not-exist").resolve())


def test_register_refuses_a_non_directory_root(tmp_path: Path) -> None:
    manager, _ = make_manager(tmp_path)
    blob = tmp_path / "blob.txt"
    blob.write_text("x", encoding="utf-8")

    with pytest.raises(InvalidInputError) as excinfo:
        manager.register_project("blob", blob)

    assert excinfo.value.code is ErrorCode.INVALID_INPUT


def test_register_refuses_a_root_outside_the_allow_list(tmp_path: Path) -> None:
    manager, _ = make_manager(tmp_path, allowed=[str(tmp_path / WORKSPACE_ROOT)])
    outside = tmp_path / "outside" / "src"
    outside.mkdir(parents=True)
    (outside / ".git").mkdir()

    with pytest.raises(WorkspaceError) as excinfo:
        manager.register_project("outside", outside)

    assert excinfo.value.code is ErrorCode.WORKSPACE_ERROR
    assert "outside the allowed workspace paths" in excinfo.value.message
    assert excinfo.value.details["project root"] == str(outside.resolve())


def test_register_allows_an_explicitly_granted_root(tmp_path: Path) -> None:
    granted = tmp_path / "granted" / "src"
    granted.mkdir(parents=True)
    (granted / ".git").mkdir()
    manager, _ = make_manager(tmp_path, allowed=[str(tmp_path / "granted")])

    project = manager.register_project("granted", granted)

    assert project.root == granted.resolve()


def test_register_refuses_a_path_smuggler_name(tmp_path: Path) -> None:
    manager, _ = make_manager(tmp_path)
    src = make_project_root(tmp_path)

    registered = manager.register_project("_canary", src)
    assert registered.name == "_canary"

    for name in ("../escape", "a/b", "a\\b"):
        with pytest.raises(InvalidInputError) as excinfo:
            manager.register_project(name, src)
        assert excinfo.value.code is ErrorCode.INVALID_INPUT
        assert "separators" in excinfo.value.message


def test_unregister_forgets_a_project_but_never_a_directory(tmp_path: Path) -> None:
    manager, _ = make_manager(tmp_path)
    src = make_project_root(tmp_path)
    manager.register_project("sample", src)

    project = manager.unregister_project("sample")

    assert project.name == "sample"
    assert src.is_dir()  # unregistering is not deleting


def test_project_unknown_is_404(tmp_path: Path) -> None:
    manager, _ = make_manager(tmp_path)

    with pytest.raises(NotFoundError) as excinfo:
        manager.project("never")

    assert excinfo.value.details["id"] == "never"


# --------------------------------------------------------------------------- #
# §46.2 locate repositories
# --------------------------------------------------------------------------- #
async def test_locate_repository_finds_a_git_marker_up_the_tree(tmp_path: Path) -> None:
    manager, _ = make_manager(tmp_path)
    repo = make_project_root(tmp_path, name="monorepo")
    deep = repo / "journal"
    deep.mkdir()
    manager.register_project("sample", deep)

    located = await manager.locate_repository("sample")

    assert located == repo


async def test_locate_repository_refuses_a_non_repository(tmp_path: Path) -> None:
    manager, _ = make_manager(tmp_path)
    plain = tmp_path / "plain"
    plain.mkdir()
    manager.register_project("plain", plain)

    with pytest.raises(WorkspaceError) as excinfo:
        await manager.locate_repository("plain")

    assert excinfo.value.details["project"] == "plain"
    assert await manager.is_repository("plain") is False


# --------------------------------------------------------------------------- #
# §46.3/§46.4 create isolated workspaces + branches
# --------------------------------------------------------------------------- #
async def test_create_workspace_clones_into_the_workspace_root(tmp_path: Path) -> None:
    runner = RecordingGitRunner()
    manager, root = make_manager(tmp_path, runner=runner)
    src = make_project_root(tmp_path)
    manager.register_project("sample", src)

    workspace = await manager.create_workspace("ws1", "sample")

    assert workspace.project == "sample"
    assert workspace.path == root / "ws1"
    assert workspace.branch == "ultron/ws1"

    clones = runner.matching("clone", "--quiet", "--")
    assert clones == [
        (("clone", "--quiet", "--", str(src.resolve()), str(root / "ws1")), str(root))
    ]
    checkouts = runner.matching("checkout", "--quiet", "-b")
    assert checkouts == [(("checkout", "--quiet", "-b", "ultron/ws1"), str(root / "ws1"))]


async def test_create_workspace_without_isolation_skips_the_branch(tmp_path: Path) -> None:
    runner = RecordingGitRunner()
    manager, _ = make_manager(tmp_path, runner=runner, git_isolation=False)
    src = make_project_root(tmp_path)
    manager.register_project("sample", src)

    workspace = await manager.create_workspace("ws1", "sample")

    assert workspace.branch is None
    assert runner.matching("checkout", "--quiet", "-b") == []


async def test_create_workspace_rejects_a_duplicate_name_and_a_collision(tmp_path: Path) -> None:
    runner = RecordingGitRunner()
    manager, _ = make_manager(tmp_path, runner=runner)
    src = make_project_root(tmp_path)
    manager.register_project("sample", src)

    await manager.create_workspace("ws1", "sample")
    with pytest.raises(ConflictError):
        await manager.create_workspace("ws1", "sample")

    manager.register_project("ws2", src)
    with pytest.raises(ConflictError):
        await manager.create_workspace("ws2", "sample")


async def test_create_workspace_fails_when_the_clone_fails(tmp_path: Path) -> None:
    runner = RecordingGitRunner()
    runner.fail("clone", "--quiet", "--")
    manager, _ = make_manager(tmp_path, runner=runner)
    src = make_project_root(tmp_path)
    manager.register_project("sample", src)

    with pytest.raises(WorkspaceError) as excinfo:
        await manager.create_workspace("ws1", "sample")

    assert excinfo.value.code is ErrorCode.WORKSPACE_ERROR
    assert excinfo.value.details["git_stderr"] == "boom"


def test_workspace_unknown_is_404(tmp_path: Path) -> None:
    manager, _ = make_manager(tmp_path)

    with pytest.raises(NotFoundError):
        manager.workspace("never")


# --------------------------------------------------------------------------- #
# §46.5 status / diff
# --------------------------------------------------------------------------- #
async def test_status_and_diff_run_against_the_workspace_directory(tmp_path: Path) -> None:
    runner = RecordingGitRunner()
    manager, root = make_manager(tmp_path, runner=runner)
    src = make_project_root(tmp_path)
    manager.register_project("sample", src)

    await manager.create_workspace("ws1", "sample")
    await manager.status("ws1")
    await manager.diff("ws1")
    await manager.current_branch("ws1")

    assert runner.matching("status", "--porcelain=v1") == [
        (("status", "--porcelain=v1"), str(root / "ws1"))
    ]
    assert runner.matching("diff", "--no-color") == [(("diff", "--no-color"), str(root / "ws1"))]
    assert runner.matching("branch", "--show-current") == [
        (("branch", "--show-current"), str(root / "ws1"))
    ]


async def test_commands_run_with_the_configured_timeout(tmp_path: Path) -> None:
    runner = RecordingGitRunner()
    manager, _ = make_manager(tmp_path, runner=runner, git_timeout=7.5)
    src = make_project_root(tmp_path)
    manager.register_project("sample", src)

    await manager.create_workspace("ws1", "sample")
    await manager.status("ws1")

    assert [(argv, timeout) for (argv, _, timeout) in runner.calls] == [
        (
            (
                "clone",
                "--quiet",
                "--",
                str(src.resolve()),
                str(tmp_path / WORKSPACE_ROOT / "ws1"),
            ),
            7.5,
        ),
        (("checkout", "--quiet", "-b", "ultron/ws1"), 7.5),
        (("status", "--porcelain=v1"), 7.5),
    ]


# --------------------------------------------------------------------------- #
# §46.6 cleanup
# --------------------------------------------------------------------------- #
async def test_cleanup_removes_the_directory_and_forgets_the_workspace(tmp_path: Path) -> None:
    runner = RecordingGitRunner()
    manager, root = make_manager(tmp_path, runner=runner)
    src = make_project_root(tmp_path)
    manager.register_project("sample", src)

    await manager.create_workspace("ws1", "sample")
    (root / "ws1").mkdir(parents=True)
    assert (root / "ws1").is_dir()

    removed = await manager.cleanup("ws1")

    assert removed == "ws1"
    assert not (root / "ws1").exists()
    with pytest.raises(NotFoundError):
        manager.workspace("ws1")


async def test_cleanup_all_removes_every_workspace(tmp_path: Path) -> None:
    runner = RecordingGitRunner()
    manager, root = make_manager(tmp_path, runner=runner)
    src = make_project_root(tmp_path)
    manager.register_project("sample", src)

    await manager.create_workspace("ws1", "sample")
    await manager.create_workspace("ws2", "sample")
    (root / "ws1").mkdir(parents=True)
    (root / "ws2").mkdir(parents=True)

    removed = await manager.cleanup_all()

    assert removed == ["ws1", "ws2"]
    assert not (root / "ws1").exists()
    assert not (root / "ws2").exists()


# --------------------------------------------------------------------------- #
# The runner components stay honest
# --------------------------------------------------------------------------- #
def test_git_result_ok_follows_the_exit_code() -> None:
    assert GitResult(argv=("git",), exit_code=0, stdout="", stderr="").ok
    assert not GitResult(argv=("git",), exit_code=1, stdout="", stderr="").ok


def test_the_default_runner_refuses_an_empty_executable() -> None:
    with pytest.raises(InvalidInputError):
        SubprocessGitRunner(executable="")


# --------------------------------------------------------------------------- #
# Real git — the end-to-end shape a coding task depends on (skipped without git)
# --------------------------------------------------------------------------- #
@REQUIRES_GIT
async def test_real_git_clone_status_diff_and_cleanup(tmp_path: Path) -> None:
    src = tmp_path / "project"
    src.mkdir()
    (src / "calculator.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    _git(src, "init", "-q")
    _git(src, "config", "user.email", "tests@ultron.local")
    _git(src, "config", "user.name", "ULTRON Tests")
    _git(src, "add", ".")
    _git(src, "commit", "-q", "-m", "init")
    branch_before = _git(src, "branch", "--show-current").strip()

    manager, root = make_manager(tmp_path, git_isolation=False)
    manager.register_project("project", src)

    workspace = await manager.create_workspace("ws1", "project")
    assert workspace.path == root / "ws1"
    assert (workspace.path / "calculator.py").is_file()
    assert (workspace.path / ".git").is_dir()

    assert (await manager.status("ws1")).ok

    # A real edit shows up in status and diff.
    (workspace.path / "calculator.py").write_text(
        "def add(a, b):\n    return a + b + 1\n", encoding="utf-8"
    )
    dirty = (await manager.status("ws1")).stdout.strip()
    assert "calculator.py" in dirty
    diff = (await manager.diff("ws1")).stdout
    assert "+    return a + b + 1" in diff

    branch = await manager.current_branch("ws1")
    assert branch.stdout.strip() == branch_before

    await manager.cleanup("ws1")
    assert not (root / "ws1").exists()


@REQUIRES_GIT
async def test_real_git_creates_a_dedicated_branch(tmp_path: Path) -> None:
    src = tmp_path / "project"
    src.mkdir()
    (src / "main.py").write_text("print('hi')\n", encoding="utf-8")
    _git(src, "init", "-q")
    _git(src, "config", "user.email", "tests@ultron.local")
    _git(src, "config", "user.name", "ULTRON Tests")
    _git(src, "add", ".")
    _git(src, "commit", "-q", "-m", "init")
    original_branch = _git(src, "branch", "--show-current").strip()

    manager, _ = make_manager(tmp_path)
    manager.register_project("project", src)

    workspace = await manager.create_workspace("ws1", "project")

    assert workspace.branch == "ultron/ws1"
    branch = await manager.current_branch("ws1")
    assert branch.stdout.strip() == workspace.branch
    assert branch.stdout.strip() != original_branch


def _git(repo: Path, *args: str) -> str:
    """Run one git command in ``repo`` synchronously (a test helper only)."""
    process = subprocess.run(  # noqa: S603 - test-only; args come from test literals
        ["git", *args],  # noqa: S607 - PATH lookup is the manager's documented default (§36)
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if process.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {process.stderr}")
    return process.stdout
