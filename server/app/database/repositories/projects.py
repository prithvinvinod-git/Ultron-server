"""Project repository (T015).

``root_path`` is the boundary that decides which files an agent may touch, so it
is read through :func:`ProjectRepository.require_root_path` rather than off the
attribute. A project with no root has no filesystem scope, and the function that
enforces the scope is the one that refuses when the root is missing -- returning
``None`` and letting a caller decide would make the unsafe path the default one.
"""

from __future__ import annotations

import uuid
from pathlib import PurePosixPath, PureWindowsPath

from sqlalchemy import func, select

from app.core.errors import InvalidInputError
from app.database.models import Project
from app.database.repositories.base import UuidRepository, apply_limit


class ProjectRepository(UuidRepository[Project]):
    """Projects."""

    model = Project

    async def get_by_slug(self, slug: str) -> Project | None:
        """Return the project with this slug, or ``None``."""
        statement = select(Project).where(Project.slug == slug)
        return await self._fetch_one(statement)

    async def require_root_path(self, identifier: uuid.UUID | str) -> str:
        """Return a project's filesystem root, or raise if it has none.

        A relative root is refused. A root that is not absolute would resolve
        against whatever the process happens to have as its working directory,
        which differs between a shell, a worker, and a container -- so the same
        policy would confine an agent to different directories depending on where
        it happened to run. That is the failure mode a filesystem boundary
        exists to prevent.

        "Absolute" is judged against *both* path flavours rather than the
        host's. ``PurePath`` follows the platform, so on Windows it reads
        ``/srv/projects/p`` as drive-relative and rejects a perfectly good POSIX
        root, while ``C:\\projects\\p`` is accepted on Linux and would then be
        meaningless in the container. Whether a stored root is valid must depend
        on the stored value alone: if it is absolute under either flavour it is a
        usable root, and if it is relative under both it is not. That keeps the
        decision identical on a developer's laptop and in the container that
        actually serves requests.

        Path traversal in the stored value is not checked here. Storing
        ``/srv/app/../../etc`` as a root would pass this check, and resolving it
        is the sandbox's job (T019) rather than a query's -- but the value is
        returned as a plain string so the caller is forced to deal with it
        deliberately.
        """
        project = await self.get_required(identifier)
        root = project.root_path
        if root is None or not root.strip():
            raise InvalidInputError(
                "the project has no root path, so it has no filesystem scope",
                details={"project_id": str(project.id)},
            )
        is_absolute = PurePosixPath(root).is_absolute() or PureWindowsPath(root).is_absolute()
        if not is_absolute:
            raise InvalidInputError(
                "the project root path must be absolute",
                details={"project_id": str(project.id), "root_path": root},
            )
        return root

    async def list_for_owner(
        self,
        owner_id: uuid.UUID,
        *,
        include_inactive: bool = False,
        limit: int | None = None,
    ) -> list[Project]:
        """Return a user's projects, alphabetically.

        ``include_inactive`` defaults to excluding archived projects. A default
        that surfaces deactivated work is the kind of thing nobody notices until a
        user acts on a project they thought they had closed.
        """
        statement = select(Project).where(Project.owner_id == owner_id)
        if not include_inactive:
            statement = statement.where(Project.is_active.is_(True))
        statement = statement.order_by(Project.name)
        return await self._fetch_all(apply_limit(statement, limit))

    async def list_active(self, *, limit: int | None = None) -> list[Project]:
        """Return every active project, alphabetically."""
        statement = select(Project).where(Project.is_active.is_(True)).order_by(Project.name)
        return await self._fetch_all(apply_limit(statement, limit))

    async def deactivate(self, identifier: uuid.UUID | str) -> Project:
        """Mark a project inactive, leaving its rows intact.

        Idempotent. The data stays because tasks, events, and memories reference
        it, and a hard delete would cascade away the record of what happened.
        """
        project = await self.get_required(identifier)
        if not project.is_active:
            return project
        project.is_active = False
        await self._session.flush()
        return project

    async def reactivate(self, identifier: uuid.UUID | str) -> Project:
        """Mark a project active again."""
        project = await self.get_required(identifier)
        if project.is_active:
            return project
        project.is_active = True
        await self._session.flush()
        return project

    async def count_active(self) -> int:
        """Count active projects."""
        statement = select(func.count()).select_from(Project).where(Project.is_active.is_(True))
        result = await self._session.execute(statement)
        return int(result.scalar_one() or 0)


__all__ = ["ProjectRepository"]
