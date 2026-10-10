"""The Context Manager (T046): conversation state, scoped by §22 namespaces.

Spec §22 creates the memory system and fixes its two structural rules: there are
separate layers (short-term, long-term, episodic, semantic, project), and
**"memory must have namespaces"** — `user`, `project:campuscare`,
`project:ultron`, `agent:coding`, `agent:research` — so that, in the spec's own
words, unrelated project memories are never mixed (server_arc.md:1156-1168).
§66.3 then earmarks *this one file* as the single context layer, growing from
"conversation state + namespaces" into the ranked, bounded provider of record
(`collect`/`resolve`/`rank`/`compress`/`build_prompt_context`/
`clear_expired_context`).

T046 is the first step and deliberately the small one: an in-memory, namespaced
store for the *current* conversation's working state, with the two properties
§66.3 fixes before anything is ranked.

**One layer, per conversation.** A single :class:`ContextManager` holds one
:class:`ConversationContext` per conversation id. §66.3's "one instance" is
about the *layer*, not the conversation: a server runs many conversations at
once, and each keeps its own namespaced state. Nothing here touches a database
or Redis — §22's "Redis *may* handle temporary state" is permission, not an
instruction, and `todo.md` C5 keeps Redis out until a task genuinely needs it.

**Scoped by namespace, and bounded.** Every value lives in a §22 namespace, so
one project's state cannot be read through another's by a typo — a malformed
namespace is a 422 (:class:`~app.core.errors.InvalidInputError`), never a
silent catch-all bucket. Each conversation is capped by ``max_entries``;
crossing the cap is a 409 (:class:`~app.core.errors.ConflictError`) naming the
limit, because §59.25's memory pressure must be observable (§59.24) and never
silently reduce what the user can see.

**Tenancy is checked here.** A conversation's context carries its ``user_id``,
and reading it as a different principal is a 404
(:class:`~app.core.errors.NotFoundError`) — the same "hide existence, do not
leak the shape" rule `ConversationRepository.require_conversation` keeps. The
fuller §66.3 rule — permission-filtered *before* ranking, so a principal cannot
surface another principal's memory — is wired when T380 adds the permission
engine; T046 keeps the namespace boundary and the ownership check and invents no
policy of its own, exactly as §66.7 keeps permission decisions out of the tool
declarations.

Deliberately absent: ranking, compression, prompt assembly and expiry (all
§66.3, T380); persistence (§22's short-term layer is "current
conversation/context", and the durable transcript already lives in the
`messages` table); and any subsystem import — like the rest of ``app.core``,
this module may not reach into the phases above it.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any, Final

from app.core.errors import ConflictError, InvalidInputError, NotFoundError

__all__ = [
    "DEFAULT_MAX_ENTRIES",
    "USER_NAMESPACE",
    "ContextManager",
    "ConversationContext",
    "Namespace",
]

#: How many values one conversation may hold before a write is refused. A bound
#: is required (§59.25: the runtime host has 4 GB), and refusing is the honest
#: overflow — §66.3's `compress()` (T380) will make room later, but today a
#: silent eviction would drop context the user can see and cannot recover.
DEFAULT_MAX_ENTRIES: Final[int] = 256

#: A namespace kind: a lowercase token (`user`, `project`, `agent`).
_KIND_RE: Final[re.Pattern[str]] = re.compile(r"[a-z][a-z0-9_]*\Z")

#: A namespace name: no whitespace, no colon (colon separates kind and name).
_NAME_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9._-]+\Z")


@dataclass(frozen=True, slots=True)
class Namespace:
    """One §22 scope: the global ``user`` scope or a ``kind:name`` scope.

    The value object exists so a namespace is *validated once* and then carried
    as a key, rather than re-parsed and re-trusted at every read. `parse` is the
    only way a string becomes one, and it refuses anything that is not the §22
    shape — an empty scope, a bare colon, an uppercase kind, a name with a colon
    in it — so "unrelated project memories" cannot be merged by a typo.
    """

    kind: str
    name: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or not _KIND_RE.fullmatch(self.kind):
            raise InvalidInputError(
                "a namespace kind must be a lowercase token like 'user' or 'project'",
                details={"kind": str(self.kind)[:100]},
            )
        if self.name is not None and (
            not isinstance(self.name, str) or not _NAME_RE.fullmatch(self.name)
        ):
            raise InvalidInputError(
                "a namespace name must be non-empty, with no whitespace or ':'",
                details={"name": str(self.name)[:100]},
            )

    @classmethod
    def parse(cls, value: Namespace | str) -> Namespace:
        """Coerce ``value`` to a validated :class:`Namespace` (422 otherwise).

        ``"user"`` is the global scope; ``"project:campuscare"`` splits on the
        first colon into kind and name. A string the §22 vocabulary cannot
        describe is the client's mistake, surfaced as a typed 422 rather than a
        bucket nobody can find again.
        """
        if isinstance(value, Namespace):
            return value
        if not isinstance(value, str):
            raise InvalidInputError(
                "a namespace must be a string or Namespace",
                details={"namespace": repr(value)[:100]},
            )
        text = value.strip()
        if not text:
            raise InvalidInputError(
                "a namespace must not be empty",
                details={"namespace": value[:100]},
            )
        kind, separator, name = text.partition(":")
        if not separator:
            return cls(kind)
        if not name:
            raise InvalidInputError(
                "a namespace with ':' needs a name after it",
                details={"namespace": value[:100]},
            )
        return cls(kind, name)

    @classmethod
    def project(cls, name: str) -> Namespace:
        """The `project:<name>` scope of §22's example list."""
        return cls("project", name)

    @classmethod
    def agent(cls, name: str) -> Namespace:
        """The `agent:<type>` scope of §22's example list."""
        return cls("agent", name)

    def __str__(self) -> str:
        return self.kind if self.name is None else f"{self.kind}:{self.name}"


#: §22's global scope: the conversation owner's own state, not shared.
USER_NAMESPACE: Final[Namespace] = Namespace("user")


class ConversationContext:
    """The namespaced working state of one conversation.

    Constructed by :class:`ContextManager` (or directly in a test); the store is
    a two-level mapping — namespace → key → value — with a hard cap on how many
    values it may hold. Values are held by reference and ``snapshot`` returns a
    fresh outer/inner dict, so a caller can read the whole shape and mutate its
    copy without rewriting the store; deep-copying arbitrary values is left to
    the caller, since a context value may be any object and copies of large ones
    are a cost §59.25 does not want paid on every read.
    """

    def __init__(
        self,
        conversation_id: uuid.UUID,
        *,
        user_id: uuid.UUID | None = None,
        max_entries: int = DEFAULT_MAX_ENTRIES,
    ) -> None:
        if not _is_positive_int(max_entries):
            raise ValueError("max_entries must be a positive whole number")
        self._conversation_id = conversation_id
        self._user_id = user_id
        self._max_entries = max_entries
        self._entries: dict[Namespace, dict[str, Any]] = {}

    @property
    def conversation_id(self) -> uuid.UUID:
        """The conversation this context belongs to."""
        return self._conversation_id

    @property
    def user_id(self) -> uuid.UUID | None:
        """The principal that owns the conversation, or ``None`` if unowned."""
        return self._user_id

    @property
    def max_entries(self) -> int:
        """The cap on how many values this context may hold."""
        return self._max_entries

    def set(self, namespace: Namespace | str, key: str, value: Any) -> None:
        """Store ``value`` under ``key`` in ``namespace``.

        Overwriting an existing key is always allowed; adding a *new* one at the
        cap is a 409 naming the limit, never a quiet eviction.
        """
        scope = Namespace.parse(namespace)
        if not isinstance(key, str) or not key.strip():
            raise InvalidInputError(
                "a context key must be a non-empty string",
                details={"key": str(key)[:100]},
            )
        existing = self._entries.get(scope)
        is_new = existing is None or key not in existing
        if is_new and self._total() >= self._max_entries:
            raise ConflictError(
                "the conversation context is full",
                details={
                    "conversation_id": str(self._conversation_id),
                    "max_entries": self._max_entries,
                    "namespace": str(scope),
                },
            )
        if existing is None:
            existing = self._entries[scope] = {}
        existing[key] = value

    def get(self, namespace: Namespace | str, key: str, default: Any = None) -> Any:
        """The value under ``key`` in ``namespace``, or ``default``."""
        scope = Namespace.parse(namespace)
        entries = self._entries.get(scope)
        if entries is None:
            return default
        return entries.get(key, default)

    def has(self, namespace: Namespace | str, key: str) -> bool:
        """Whether ``key`` is set in ``namespace``."""
        entries = self._entries.get(Namespace.parse(namespace))
        return entries is not None and key in entries

    def delete(self, namespace: Namespace | str, key: str) -> bool:
        """Remove one value; return whether it was there."""
        scope = Namespace.parse(namespace)
        entries = self._entries.get(scope)
        if entries is None or key not in entries:
            return False
        del entries[key]
        if not entries:
            del self._entries[scope]
        return True

    def snapshot(self, namespace: Namespace | str | None = None) -> dict[str, dict[str, Any]]:
        """A copy of the stored state, as ``{namespace: {key: value}}``.

        With ``namespace`` set, only that scope is returned; namespaces are
        ordered by their string form so a snapshot fed into a prompt is stable
        across calls.
        """
        wanted = None if namespace is None else Namespace.parse(namespace)
        return {
            str(scope): dict(entries)
            for scope, entries in sorted(self._entries.items(), key=lambda item: str(item[0]))
            if wanted is None or scope == wanted
        }

    def namespaces(self) -> list[Namespace]:
        """Every namespace that currently holds at least one value, sorted."""
        return sorted(self._entries, key=str)

    def clear(self, namespace: Namespace | str | None = None) -> None:
        """Drop one namespace, or the whole conversation's state with ``None``."""
        if namespace is None:
            self._entries.clear()
            return
        self._entries.pop(Namespace.parse(namespace), None)

    def __len__(self) -> int:
        return self._total()

    def __contains__(self, namespace: object) -> bool:
        if not isinstance(namespace, (Namespace, str)):
            return False
        try:
            scope = Namespace.parse(namespace)
        except InvalidInputError:
            return False
        return scope in self._entries

    def _total(self) -> int:
        return sum(len(entries) for entries in self._entries.values())


class ContextManager:
    """The one context layer (§66.3): a :class:`ConversationContext` per thread.

    Holds the working state of every live conversation, keyed by id. Ownership
    is enforced on every read, so one principal cannot reach another's context
    by guessing a conversation id. Dropping a finished conversation is the
    caller's job (the lifecycle in T049, or a request finally-block); this class
    never expires state on a timer nobody configured.
    """

    def __init__(self, *, max_entries: int = DEFAULT_MAX_ENTRIES) -> None:
        if not _is_positive_int(max_entries):
            raise ValueError("max_entries must be a positive whole number")
        self._max_entries = max_entries
        self._contexts: dict[uuid.UUID, ConversationContext] = {}

    def for_conversation(
        self,
        conversation_id: str | uuid.UUID,
        *,
        user_id: str | uuid.UUID | None = None,
    ) -> ConversationContext:
        """Return the conversation's context, creating it on first use.

        Idempotent: the same conversation yields the same context, so callers
        need no "does it exist yet" branch. An existing context owned by a
        *different* principal is a 404 — the id is not a capability. If the
        context was created unowned, a later owning call adopts it.
        """
        identifier = _require_uuid(conversation_id, field="conversation_id")
        owner = None if user_id is None else _require_uuid(user_id, field="user_id")
        existing = self._contexts.get(identifier)
        if existing is not None:
            _assert_owner(existing, owner)
            if owner is not None and existing.user_id is None:
                existing._user_id = owner
            return existing
        created = ConversationContext(identifier, user_id=owner, max_entries=self._max_entries)
        self._contexts[identifier] = created
        return created

    def get(
        self,
        conversation_id: str | uuid.UUID,
        *,
        user_id: str | uuid.UUID | None = None,
    ) -> ConversationContext | None:
        """The conversation's context, or ``None`` — checking ownership."""
        identifier = _require_uuid(conversation_id, field="conversation_id")
        owner = None if user_id is None else _require_uuid(user_id, field="user_id")
        context = self._contexts.get(identifier)
        if context is None:
            return None
        _assert_owner(context, owner)
        return context

    def require(
        self,
        conversation_id: str | uuid.UUID,
        *,
        user_id: str | uuid.UUID | None = None,
    ) -> ConversationContext:
        """Like :meth:`get`, but a missing conversation is a 404."""
        context = self.get(conversation_id, user_id=user_id)
        if context is None:
            raise NotFoundError("conversation", str(conversation_id))
        return context

    def drop(self, conversation_id: str | uuid.UUID) -> bool:
        """Forget one conversation's context; return whether it was held."""
        identifier = _require_uuid(conversation_id, field="conversation_id")
        return self._contexts.pop(identifier, None) is not None

    def clear(self) -> None:
        """Forget every conversation's context (a process-wide reset)."""
        self._contexts.clear()

    def conversation_ids(self) -> list[uuid.UUID]:
        """Every held conversation id, sorted for a stable listing."""
        return sorted(self._contexts)

    def __len__(self) -> int:
        return len(self._contexts)

    def __contains__(self, conversation_id: object) -> bool:
        if not isinstance(conversation_id, (uuid.UUID, str)):
            return False
        try:
            identifier = uuid.UUID(str(conversation_id))
        except (ValueError, AttributeError, TypeError):
            return False
        return identifier in self._contexts

    def __repr__(self) -> str:
        return (
            f"ContextManager(conversations={len(self._contexts)}, max_entries={self._max_entries})"
        )


def _assert_owner(context: ConversationContext, owner: uuid.UUID | None) -> None:
    """Refuse a read whose principal does not own the conversation (404).

    ``None`` matches any context: an internal caller with no principal (a
    background worker, say) is not a *different* user. A non-``None`` owner that
    disagrees is answered as "not found", because confirming a conversation id
    belongs to someone else already leaks that it exists.
    """
    if owner is not None and context.user_id not in (None, owner):
        raise NotFoundError("conversation", str(context.conversation_id))


def _require_uuid(value: str | uuid.UUID, *, field: str) -> uuid.UUID:
    """Parse ``value`` as a UUID or refuse it as the client's mistake (422)."""
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError) as error:
        raise InvalidInputError(
            f"{field} must be a UUID",
            details={field: str(value)[:100]},
        ) from error


def _is_positive_int(value: object) -> bool:
    """Whether ``value`` is a positive ``int`` and not a ``bool``."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1
