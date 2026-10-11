"""The filesystem sandbox (T090, spec §59.4).

Spec §59.4 fixes the layering: the tool router validates the schema, the
permission layer decides, and then — and only then — *the filesystem layer is
the single component that touches a path*. That last sentence is this module.

Two rules are load-bearing and both live here, not in the individual tools:

- **Resolve before you trust.** Every incoming path is turned into a real path
  first — ``~`` expanded, ``..`` collapsed, symlinks followed — and the
  allow-list test runs on *that* result (§59.4). Testing the *spelled* path
  instead would let ``workspace/link -> /etc`` walk straight through.
- **The allow-list is the authority.** ``WorkspaceSettings.allowed_paths()``
  (§34, §48) decides what exists as far as a tool is concerned. Nothing outside
  it is ever handed back to a tool, and out-of-bounds input is refused by this
  layer rather than the caller — §59.4 is explicit that the refusal belongs to
  the layer that holds the path.

The sandbox is deliberately narrow: it resolves and checks paths, and hands
back a resolved ``Path``. It does no I/O of its own, so a tool that forgets to
call it still cannot be trusted — which is why every concrete filesystem tool
goes through :meth:`resolve`.
"""

from __future__ import annotations

from pathlib import Path

from app.config.settings import WorkspaceSettings
from app.core.errors import InvalidInputError, NotFoundError, WorkspaceError

__all__ = ["FilesystemSandbox", "is_within"]


class FilesystemSandbox:
    """Resolve paths and enforce the workspace allow-list (§59.4)."""

    def __init__(self, settings: WorkspaceSettings) -> None:
        """Capture the allowed roots once, at construction.

        The roots are resolved eagerly so a later change to the working
        directory cannot move the boundary underneath a running tool.
        """
        self._roots: tuple[Path, ...] = tuple(settings.allowed_paths())

    @property
    def roots(self) -> tuple[Path, ...]:
        """The resolved directories tools may touch, in configured order."""
        return self._roots

    def allowed(self) -> list[str]:
        """The allow-list as strings, for error payloads and descriptions."""
        return [str(root) for root in self._roots]

    def resolve(self, path: str, *, what: str = "path") -> Path:
        """Resolve ``path`` and refuse it unless it lies inside an allowed root.

        The result is the *real* path: symlinks followed and ``..`` collapsed
        before the allow-list test, exactly as §59.4 requires. A relative path
        is refused rather than guessed at — a tool has no meaningful working
        directory, and inventing one would make the boundary depend on hidden
        state.
        """
        if not isinstance(path, str) or not path.strip():
            raise InvalidInputError(
                f"{what} must be a non-empty path", details={what: repr(path)[:200]}
            )

        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            raise InvalidInputError(
                f"{what} must be an absolute path",
                details={what: path[:200], "allowed": self.allowed()},
            )

        resolved = candidate.resolve()
        if not any(is_within(resolved, root) for root in self._roots):
            raise WorkspaceError(
                f"{what} is outside the allowed workspace paths",
                details={what: str(resolved), "allowed": self.allowed()},
            )
        return resolved

    def resolve_existing(self, path: str, *, what: str = "path") -> Path:
        """Resolve ``path`` and require that it exists on disk."""
        resolved = self.resolve(path, what=what)
        if not resolved.exists():
            raise NotFoundError(what, str(resolved))
        return resolved

    def resolve_file(self, path: str, *, what: str = "file") -> Path:
        """Resolve ``path`` and require that it is an existing regular file."""
        resolved = self.resolve_existing(path, what=what)
        if not resolved.is_file():
            raise InvalidInputError(f"{what} is not a regular file", details={what: str(resolved)})
        return resolved

    def resolve_directory(self, path: str, *, what: str = "directory") -> Path:
        """Resolve ``path`` and require that it is an existing directory."""
        resolved = self.resolve_existing(path, what=what)
        if not resolved.is_dir():
            raise InvalidInputError(f"{what} is not a directory", details={what: str(resolved)})
        return resolved

    def ensure_within(self, path: Path, *, what: str = "path") -> None:
        """Re-check an already-resolved path against the allow-list.

        Used before a mutation whose target may have been constructed from
        something other than raw input (for example a parent directory), so a
        write cannot be redirected outside the workspace by a crafted name.
        """
        if not any(is_within(path, root) for root in self._roots):
            raise WorkspaceError(
                f"{what} is outside the allowed workspace paths",
                details={what: str(path), "allowed": self.allowed()},
            )

    def ensure_not_root(self, path: Path, *, what: str = "path") -> None:
        """Refuse operating on an allowed root itself.

        Deleting a workspace root would remove the sandbox with the data, so
        every destructive tool calls this first; a root is a boundary, not a
        target.
        """
        if any(path == root for root in self._roots):
            raise InvalidInputError(
                f"refusing to {what} an allowed root",
                details={what: str(path), "roots": self.allowed()},
            )


def is_within(child: Path, parent: Path) -> bool:
    """Whether ``child`` is ``parent`` or lives underneath it (real paths)."""
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True
