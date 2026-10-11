"""T090 tests: the sandboxed filesystem tools (spec §59.4).

The sandbox is the phase's security boundary, so the tests spend as much time
on what must be *refused* as on what works: paths outside the allow-list,
``..`` smuggled through a path, relative paths, a symlink pointing out of the
workspace, and a delete aimed at an allowed root. The happy paths run against a
real temp directory — real files, real resolution — because a sandbox that only
works against a fake filesystem is not a sandbox.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.config.settings import WorkspaceSettings
from app.core.errors import ConflictError, InvalidInputError, NotFoundError, WorkspaceError
from app.database.models import PermissionLevel
from app.tools.base import Reversibility
from app.tools.filesystem import (
    DeletePathTool,
    FilesystemSandbox,
    ListDirectoryTool,
    ReadFileTool,
    SearchFilesTool,
    WriteFileTool,
    build_filesystem_tools,
)

pytestmark = pytest.mark.unit


@pytest.fixture()
def sandbox(tmp_path: Path) -> FilesystemSandbox:
    settings = WorkspaceSettings(root=str(tmp_path / "ws"), allowed_roots=[str(tmp_path)])
    return FilesystemSandbox(settings)


def _link_or_skip(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):  # pragma: no cover - platform dependent
        pytest.skip("symlinks are not permitted on this platform")


# --------------------------------------------------------------------------- #
# Declarations
# --------------------------------------------------------------------------- #


def test_declarations_match_spec_59_4() -> None:
    assert ReadFileTool.name == "filesystem.read"
    assert ListDirectoryTool.name == "filesystem.list"
    assert SearchFilesTool.name == "filesystem.search"
    assert ReadFileTool.permission_level is PermissionLevel.READ_ONLY
    assert ListDirectoryTool.permission_level is PermissionLevel.READ_ONLY
    assert SearchFilesTool.permission_level is PermissionLevel.READ_ONLY
    assert WriteFileTool.permission_level is PermissionLevel.MODIFY_PROJECT
    assert WriteFileTool.reversibility is Reversibility.PARTIALLY_REVERSIBLE
    assert DeletePathTool.permission_level is PermissionLevel.DESTRUCTIVE
    assert DeletePathTool.reversibility is Reversibility.IRREVERSIBLE
    assert DeletePathTool.audit is True


def test_build_filesystem_tools(sandbox: FilesystemSandbox) -> None:
    names = {tool.name for tool in build_filesystem_tools(sandbox)}
    assert names == {
        "filesystem.read",
        "filesystem.write",
        "filesystem.list",
        "filesystem.search",
        "filesystem.delete",
    }


# --------------------------------------------------------------------------- #
# The boundary (§59.4)
# --------------------------------------------------------------------------- #


async def test_relative_path_is_refused(sandbox: FilesystemSandbox) -> None:
    with pytest.raises(InvalidInputError):
        await ReadFileTool(sandbox).execute({"path": "relative.txt"})


async def test_outside_root_is_refused(sandbox: FilesystemSandbox, tmp_path: Path) -> None:
    outside = tmp_path.parent / "escape.txt"
    outside.write_text("nope", encoding="utf-8")
    with pytest.raises(WorkspaceError):
        await ReadFileTool(sandbox).execute({"path": str(outside)})


async def test_dotdot_cannot_smuggle_out(sandbox: FilesystemSandbox, tmp_path: Path) -> None:
    sneaky = tmp_path / "sub" / ".." / ".." / "escape.txt"
    with pytest.raises(WorkspaceError):
        await ReadFileTool(sandbox).execute({"path": str(sneaky)})


async def test_dotdot_inside_is_allowed(sandbox: FilesystemSandbox, tmp_path: Path) -> None:
    target = tmp_path / "inside.txt"
    target.write_text("ok", encoding="utf-8")
    result = await ReadFileTool(sandbox).execute(
        {"path": str(tmp_path / "sub" / ".." / "inside.txt")}
    )
    assert result["content"] == "ok"


async def test_symlink_escape_is_refused(sandbox: FilesystemSandbox, tmp_path: Path) -> None:
    secret = tmp_path.parent / "secret.txt"
    secret.write_text("top secret", encoding="utf-8")
    link = tmp_path / "link.txt"
    _link_or_skip(link, secret)
    with pytest.raises(WorkspaceError):
        await ReadFileTool(sandbox).execute({"path": str(link)})


# --------------------------------------------------------------------------- #
# read
# --------------------------------------------------------------------------- #


async def test_read_returns_content(sandbox: FilesystemSandbox, tmp_path: Path) -> None:
    target = tmp_path / "hello.txt"
    target.write_text("hello world", encoding="utf-8")
    result = await ReadFileTool(sandbox).execute({"path": str(target)})
    assert result["content"] == "hello world"
    assert result["bytes"] == 11
    assert result["truncated"] is False


async def test_read_truncates(sandbox: FilesystemSandbox, tmp_path: Path) -> None:
    target = tmp_path / "long.txt"
    target.write_text("0123456789", encoding="utf-8")
    result = await ReadFileTool(sandbox).execute({"path": str(target), "max_bytes": 4})
    assert result["content"] == "0123"
    assert result["truncated"] is True


async def test_read_missing_file(sandbox: FilesystemSandbox, tmp_path: Path) -> None:
    with pytest.raises(NotFoundError):
        await ReadFileTool(sandbox).execute({"path": str(tmp_path / "missing.txt")})


async def test_read_directory_is_refused(sandbox: FilesystemSandbox, tmp_path: Path) -> None:
    with pytest.raises(InvalidInputError):
        await ReadFileTool(sandbox).execute({"path": str(tmp_path)})


async def test_read_non_utf8_is_refused(sandbox: FilesystemSandbox, tmp_path: Path) -> None:
    target = tmp_path / "binary.bin"
    target.write_bytes(b"\xff\xfe\x00\x01")
    with pytest.raises(InvalidInputError):
        await ReadFileTool(sandbox).execute({"path": str(target)})


# --------------------------------------------------------------------------- #
# write
# --------------------------------------------------------------------------- #


async def test_write_creates_file(sandbox: FilesystemSandbox, tmp_path: Path) -> None:
    tool = WriteFileTool(sandbox)
    target = tmp_path / "new.txt"
    result = await tool.execute({"path": str(target), "content": "abc"})
    assert result["created"] is True
    assert result["bytes"] == 3
    assert target.read_text(encoding="utf-8") == "abc"


async def test_write_verify_confirms_content(sandbox: FilesystemSandbox, tmp_path: Path) -> None:
    tool = WriteFileTool(sandbox)
    target = tmp_path / "verify.txt"
    arguments: dict[str, Any] = {"path": str(target), "content": "verified"}
    result = await tool.execute(arguments)
    assert await tool.verify(result, arguments) == "success"
    target.write_text("tampered", encoding="utf-8")
    assert await tool.verify(result, arguments) == "failed"


async def test_write_respects_no_overwrite(sandbox: FilesystemSandbox, tmp_path: Path) -> None:
    target = tmp_path / "exists.txt"
    target.write_text("old", encoding="utf-8")
    with pytest.raises(ConflictError):
        await WriteFileTool(sandbox).execute(
            {"path": str(target), "content": "new", "overwrite": False}
        )


async def test_write_creates_parents_when_asked(sandbox: FilesystemSandbox, tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b" / "c.txt"
    await WriteFileTool(sandbox).execute(
        {"path": str(nested), "content": "deep", "create_dirs": True}
    )
    assert nested.read_text(encoding="utf-8") == "deep"


async def test_write_missing_parent_without_create_dirs(
    sandbox: FilesystemSandbox, tmp_path: Path
) -> None:
    nested = tmp_path / "a" / "b" / "c.txt"
    with pytest.raises(NotFoundError):
        await WriteFileTool(sandbox).execute({"path": str(nested), "content": "deep"})


async def test_write_outside_root_is_refused(sandbox: FilesystemSandbox, tmp_path: Path) -> None:
    outside = tmp_path.parent / "escape.txt"
    with pytest.raises(WorkspaceError):
        await WriteFileTool(sandbox).execute({"path": str(outside), "content": "x"})


# --------------------------------------------------------------------------- #
# list
# --------------------------------------------------------------------------- #


async def test_list_reports_entries(sandbox: FilesystemSandbox, tmp_path: Path) -> None:
    (tmp_path / "one.txt").write_text("1", encoding="utf-8")
    (tmp_path / "two.txt").write_text("2", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    result = await ListDirectoryTool(sandbox).execute({"path": str(tmp_path)})
    names = {entry["name"] for entry in result["entries"]}
    assert {"one.txt", "two.txt", "sub"} <= names
    assert result["truncated"] is False


async def test_list_hides_hidden_by_default(sandbox: FilesystemSandbox, tmp_path: Path) -> None:
    (tmp_path / ".secret").write_text("s", encoding="utf-8")
    (tmp_path / "visible.txt").write_text("v", encoding="utf-8")
    result = await ListDirectoryTool(sandbox).execute({"path": str(tmp_path)})
    names = {entry["name"] for entry in result["entries"]}
    assert names == {"visible.txt"}


async def test_list_recursive(sandbox: FilesystemSandbox, tmp_path: Path) -> None:
    nested = tmp_path / "sub"
    nested.mkdir()
    (nested / "deep.txt").write_text("d", encoding="utf-8")
    result = await ListDirectoryTool(sandbox).execute({"path": str(tmp_path), "recursive": True})
    names = {entry["name"] for entry in result["entries"]}
    assert "deep.txt" in names


async def test_list_truncates(sandbox: FilesystemSandbox, tmp_path: Path) -> None:
    for index in range(5):
        (tmp_path / f"f{index}.txt").write_text("x", encoding="utf-8")
    result = await ListDirectoryTool(sandbox).execute({"path": str(tmp_path), "max_entries": 2})
    assert result["count"] == 2
    assert result["truncated"] is True


async def test_list_file_is_refused(sandbox: FilesystemSandbox, tmp_path: Path) -> None:
    target = tmp_path / "file.txt"
    target.write_text("x", encoding="utf-8")
    with pytest.raises(InvalidInputError):
        await ListDirectoryTool(sandbox).execute({"path": str(target)})


# --------------------------------------------------------------------------- #
# search
# --------------------------------------------------------------------------- #


async def test_search_finds_matches(sandbox: FilesystemSandbox, tmp_path: Path) -> None:
    (tmp_path / "code.py").write_text("alpha\nbeta\nneedle here\n", encoding="utf-8")
    result = await SearchFilesTool(sandbox).execute({"path": str(tmp_path), "query": "needle"})
    assert result["matches"] == [
        {"path": str(tmp_path / "code.py"), "line": 3, "text": "needle here"}
    ]


async def test_search_is_case_insensitive_by_default(
    sandbox: FilesystemSandbox, tmp_path: Path
) -> None:
    (tmp_path / "code.txt").write_text("Needle\n", encoding="utf-8")
    result = await SearchFilesTool(sandbox).execute({"path": str(tmp_path), "query": "needle"})
    assert len(result["matches"]) == 1


async def test_search_respects_case_sensitive(sandbox: FilesystemSandbox, tmp_path: Path) -> None:
    (tmp_path / "code.txt").write_text("Needle\n", encoding="utf-8")
    result = await SearchFilesTool(sandbox).execute(
        {"path": str(tmp_path), "query": "needle", "case_sensitive": True}
    )
    assert result["matches"] == []


async def test_search_glob_filter(sandbox: FilesystemSandbox, tmp_path: Path) -> None:
    (tmp_path / "keep.py").write_text("needle\n", encoding="utf-8")
    (tmp_path / "skip.txt").write_text("needle\n", encoding="utf-8")
    result = await SearchFilesTool(sandbox).execute(
        {"path": str(tmp_path), "query": "needle", "glob": "*.py"}
    )
    assert [match["path"] for match in result["matches"]] == [str(tmp_path / "keep.py")]


async def test_search_truncates(sandbox: FilesystemSandbox, tmp_path: Path) -> None:
    (tmp_path / "many.txt").write_text("hit\n" * 10, encoding="utf-8")
    result = await SearchFilesTool(sandbox).execute(
        {"path": str(tmp_path), "query": "hit", "max_results": 3}
    )
    assert len(result["matches"]) == 3
    assert result["truncated"] is True


async def test_search_does_not_follow_escaping_symlink(
    sandbox: FilesystemSandbox, tmp_path: Path
) -> None:
    secret = tmp_path.parent / "secret.txt"
    secret.write_text("hidden needle", encoding="utf-8")
    _link_or_skip(tmp_path / "leak.txt", secret)
    result = await SearchFilesTool(sandbox).execute({"path": str(tmp_path), "query": "needle"})
    assert result["matches"] == []


# --------------------------------------------------------------------------- #
# delete
# --------------------------------------------------------------------------- #


async def test_delete_file(sandbox: FilesystemSandbox, tmp_path: Path) -> None:
    target = tmp_path / "doomed.txt"
    target.write_text("bye", encoding="utf-8")
    tool = DeletePathTool(sandbox)
    arguments: dict[str, Any] = {"path": str(target)}
    result = await tool.execute(arguments)
    assert result["type"] == "file"
    assert not target.exists()
    assert await tool.verify(result, arguments) == "success"


async def test_delete_directory_needs_recursive(sandbox: FilesystemSandbox, tmp_path: Path) -> None:
    directory = tmp_path / "tree"
    directory.mkdir()
    (directory / "child.txt").write_text("x", encoding="utf-8")
    with pytest.raises(InvalidInputError):
        await DeletePathTool(sandbox).execute({"path": str(directory)})


async def test_delete_directory_recursive(sandbox: FilesystemSandbox, tmp_path: Path) -> None:
    directory = tmp_path / "tree"
    directory.mkdir()
    (directory / "child.txt").write_text("x", encoding="utf-8")
    await DeletePathTool(sandbox).execute({"path": str(directory), "recursive": True})
    assert not directory.exists()


async def test_delete_refuses_allowed_root(sandbox: FilesystemSandbox) -> None:
    root = sandbox.roots[0]
    root.mkdir(parents=True, exist_ok=True)
    with pytest.raises(InvalidInputError):
        await DeletePathTool(sandbox).execute({"path": str(root), "recursive": True})


async def test_delete_outside_root_is_refused(sandbox: FilesystemSandbox, tmp_path: Path) -> None:
    outside = tmp_path.parent / "keep.txt"
    outside.write_text("keep", encoding="utf-8")
    with pytest.raises(WorkspaceError):
        await DeletePathTool(sandbox).execute({"path": str(outside)})
