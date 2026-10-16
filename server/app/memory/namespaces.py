"""Memory namespace rules (T120, spec §22).

§22 requires that *"Memory must have namespaces"* (line 1156) and forbids mixing
unrelated project memories (line 1168). The namespace is therefore not a label
hung on a row -- it is the access boundary, and this module is the one place
that decides what a legal namespace is.

**The scheme is open, the kinds are not.** The spec offers examples -- ``user``,
``project:campuscare``, ``project:ultron``, ``agent:coding``, ``agent:research``
(lines 1160-1166) -- and calls them examples, so the *slug* is free-form. But
the *kind* controls who may read the memory later, so an unknown kind is
rejected rather than accepted and treated as opaque. A namespace whose kind
nothing understands is a row that will never be retrieved by the rules that were
supposed to govern it.

**``user`` has no slug; ``project`` and ``agent`` require one.** ``user`` is the
single default personal scope (``settings.memory.default_namespace``).
``project`` and ``agent`` name *which* project or agent, so a slugless
``project`` would be one bucket shared by every project -- precisely the mixing
§22 forbids.

**Parsing reuses the model's splitter.** :func:`app.database.models.memories.namespace_scope`
already defines how a namespace string is carved into ``(kind, slug)`` and is
used to populate the indexed ``namespace_kind`` / ``namespace_slug`` columns.
Parsing here with a second, subtly different splitter is how the in-memory rule
and the stored columns drift apart, so there is only the one.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.errors import InvalidInputError
from app.database.models.memories import PERSONAL_NAMESPACE_KIND, namespace_scope

#: Personal scope. The one namespace with no slug. Re-uses the storage layer's
#: constant so there is a single spelling of the personal kind.
USER: str = PERSONAL_NAMESPACE_KIND

#: Project scope. ``project:<slug>``.
PROJECT: str = "project"

#: Agent scope. ``agent:<slug>``.
AGENT: str = "agent"

#: Every kind this module accepts. An open string scheme would let a typo
#: (``"projekt:x"``) create a namespace no ownership rule could ever match, so
#: the vocabulary is closed even though the slugs are not.
ALLOWED_KINDS: frozenset[str] = frozenset({USER, PROJECT, AGENT})

#: Kinds that must carry a slug -- everything except :data:`USER`.
_SLUG_KINDS: frozenset[str] = frozenset({PROJECT, AGENT})

#: Characters allowed inside a slug. Deliberately conservative: a slug ends up
#: in a Redis key and in the indexed ``namespace_slug`` column, so whitespace
#: and path separators are refused rather than escaped.
_SLUG_ALLOWED: frozenset[str] = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)

_SEPARATOR = ":"


def _normalise_slug(kind: str, slug: str) -> str:
    """Return the validated, lower-cased slug for a slug-bearing kind."""
    cleaned = slug.strip()
    if not cleaned:
        raise InvalidInputError(
            f"the '{kind}' namespace needs a slug",
            details={"kind": kind},
        )
    if not set(cleaned) <= _SLUG_ALLOWED:
        raise InvalidInputError(
            f"the '{kind}' namespace slug contains characters that are not allowed",
            details={"kind": kind, "slug": cleaned},
        )
    return cleaned.lower()


def build(kind: str, slug: str = "") -> str:
    """Return the canonical namespace string for ``kind`` and ``slug``.

    ``build("project", "CampusCare")`` -> ``"project:campuscare"``. The kind is
    lower-cased and must be one of :data:`ALLOWED_KINDS`; a slug is required for
    :data:`PROJECT` and :data:`AGENT` and refused for :data:`USER`.
    """
    normalised_kind = kind.strip().lower()
    if normalised_kind not in ALLOWED_KINDS:
        raise InvalidInputError(
            "unknown memory namespace kind",
            details={"kind": kind, "allowed": sorted(ALLOWED_KINDS)},
        )
    if normalised_kind in _SLUG_KINDS:
        return f"{normalised_kind}{_SEPARATOR}{_normalise_slug(normalised_kind, slug)}"
    if slug.strip():
        raise InvalidInputError(
            f"the '{normalised_kind}' namespace does not take a slug",
            details={"kind": normalised_kind},
        )
    return normalised_kind


def user() -> str:
    """The personal namespace."""
    return USER


def for_project(slug: str) -> str:
    """The namespace for one project."""
    return build(PROJECT, slug)


def for_agent(slug: str) -> str:
    """The namespace for one agent."""
    return build(AGENT, slug)


@dataclass(frozen=True, slots=True)
class Namespace:
    """A parsed, validated namespace."""

    kind: str
    slug: str
    raw: str

    @property
    def is_user(self) -> bool:
        """Whether this is the personal scope."""
        return self.kind == USER

    @property
    def is_project(self) -> bool:
        """Whether this is a project scope."""
        return self.kind == PROJECT

    @property
    def is_agent(self) -> bool:
        """Whether this is an agent scope."""
        return self.kind == AGENT

    def __str__(self) -> str:
        return self.raw


def parse(namespace: str) -> Namespace:
    """Parse and validate ``namespace``, raising :class:`InvalidInputError` if it is not legal.

    The split is :func:`namespace_scope` so the kind and slug returned here are
    exactly the ones the model stores in its indexed columns.
    """
    if not isinstance(namespace, str) or not namespace.strip():
        raise InvalidInputError("a memory namespace must be a non-empty string")
    kind, slug = namespace_scope(namespace)
    if kind not in ALLOWED_KINDS:
        raise InvalidInputError(
            "unknown memory namespace kind",
            details={"namespace": namespace, "kind": kind, "allowed": sorted(ALLOWED_KINDS)},
        )
    if kind in _SLUG_KINDS and not slug:
        raise InvalidInputError(
            f"the '{kind}' namespace needs a slug",
            details={"namespace": namespace},
        )
    return Namespace(kind=kind, slug=slug, raw=build(kind, slug))


__all__ = [
    "AGENT",
    "ALLOWED_KINDS",
    "PROJECT",
    "USER",
    "Namespace",
    "build",
    "for_agent",
    "for_project",
    "parse",
    "user",
]
