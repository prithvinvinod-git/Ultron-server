"""The Intent Router (T047): classify a request, name the Core path that handles it.

Spec §5 lists "classify intent" among the Core's responsibilities, between
receiving a request and selecting a model; §50 draws the pipeline as
``... → ULTRON CORE → INTENT → PLAN → ...``; and the Core's own module chart names
an **Intent Router** beside the Context Manager and Planner (§ around
server_arc.md:2756-2769). Phase 2 lists "intent routing" (§51).

The spec then stops. It never fixes an intent vocabulary, and there is no LLM in
Phase 2 to classify with. So this module is the smallest honest form of that
stage:

**Rules are declared, not hard-coded.** An :class:`IntentRouter` holds an ordered
list of :class:`RouteRule`\\ s contributed by the caller. Exactly as §40 keeps the
*kind* of agent a declaration on the agent (T043) and §59.6 keeps a tool's name on
the tool (T035), the router knows nothing about "coding" or "research" or any
other kind — it evaluates the rules it was given and reports the first match. T049
(the orchestrator) is where the real rules are declared, from the agents actually
registered.

**Deterministic by construction.** Rules run lowest :attr:`RouteRule.priority`
first; equal priorities keep registration order (a stable sort, so the same
declarations always produce the same route). A match is a match: the router does
not "score" or fall back through several, because a routing decision a test
cannot predict is a routing decision a user cannot reason about. The first rule
whose matcher is true wins, and a rule with no matcher (:attr:`RouteRule.matches`
is ``None``) always matches — that is how a caller declares a catch-all, placed
last by a high ``priority``.

**It classifies; it does not decide policy.** The router returns a
:class:`Routing` — the :class:`Intent`, the ``handler`` key naming the Core path,
and the rule that produced it. Whether "task" means the planner, or "tool" means
the tool executor, is the caller's mapping, expressed in the rule's ``handler``.
The router never imports the planner, the executor or the agent manager; it is
pure Core and imports only :mod:`app.core.errors`, like the rest of
``app.core``.

Phase 3 replaces the fine print, not the shape: the keyword :func:`keyword_matcher`
here is explicitly a placeholder for a model-backed understanding step (the
``classify`` seam stays the same), and a richer :class:`Intent` set is additive
because a rule carries its own intent.
"""

from __future__ import annotations

import enum
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from app.core.errors import ConflictError, InvalidInputError

__all__ = [
    "Intent",
    "IntentRouter",
    "RouteRule",
    "Routing",
    "RoutingMatcher",
    "RoutingRequest",
    "keyword_matcher",
]


class Intent(enum.StrEnum):
    """The Core's first-cut vocabulary for what a request is asking for.

    These are the *paths* the §50 pipeline can take out of INTENT, not agent
    types — the router holds no agent kinds (§40). A rule carries whichever of
    these it means, and a caller that needs a new one adds a member here and a
    rule that uses it; nothing else changes.
    """

    #: Direct dialogue the Core can answer without planning (§5, "produce final
    #: responses").
    CONVERSATION = "conversation"
    #: A request for information, answered from context or memory (T046/§22).
    QUESTION = "question"
    #: A goal that needs the planner and a task graph (§5, §50 PLAN).
    TASK = "task"
    #: An imperative operation on a node/system (§64: nodes execute).
    COMMAND = "command"
    #: An explicit single-tool request — the Tool Router's domain (§16/§59.6).
    TOOL = "tool"
    #: No rule matched. The caller decides; the router invents no policy.
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class RoutingRequest:
    """What a matcher sees: the request text and any routing context.

    ``context`` is deliberately an open mapping rather than the context engine's
    :class:`~app.core.context.ConversationContext`: a rule may key off a project
    id, a client kind, a flag — whatever the caller assembles — without the
    router taking a dependency on the context layer or coupling to its shape.
    """

    text: str
    context: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise InvalidInputError(
                "a routing request's text must be a string",
                details={"text": repr(self.text)[:100]},
            )


RoutingMatcher = Callable[[RoutingRequest], bool]


@dataclass(frozen=True, slots=True)
class RouteRule:
    """One declared intent rule: "if ``matches``, the request is this intent".

    A rule with ``matches`` of ``None`` always matches — the way a caller writes a
    catch-all, given the highest ``priority`` number so it runs only after every
    specific rule has declined.
    """

    name: str
    intent: Intent
    handler: str
    matches: RoutingMatcher | None = None
    priority: int = 100
    description: str = ""


@dataclass(frozen=True, slots=True)
class Routing:
    """The result of classifying a request: intent, handler, and why.

    ``matched`` is ``False`` only when no rule matched at all; ``handler`` is
    then ``None`` and the caller must decide what to do with an unclassified
    request (a catch-all rule, if declared, reports ``matched=True``).
    """

    intent: Intent
    handler: str | None
    rule: str | None
    matched: bool
    reason: str


class IntentRouter:
    """A deterministic, declared-rules classifier for the Core's INTENT stage.

    Hold one instance per application (it is stateless between calls), register
    the rules the running system can route, and call :meth:`route` on each
    request. Registration validates eagerly so a broken rule is a wiring error at
    startup, not a silent miss on some later request.
    """

    def __init__(self) -> None:
        self._rules: list[RouteRule] = []
        self._names: set[str] = set()

    def register(self, rule: RouteRule) -> None:
        """Add a rule, refusing a name already taken (409).

        A name is the rule's identity — it is what :attr:`Routing.rule` and any
        log record name — so a second rule with the same name would silently
        shadow the first and rewrite what "the rule that matched" means. A rule
        is validated before it is stored: an empty name/handler or a
        non-:class:`Intent` intent is the caller's mistake (422), and a
        non-callable matcher is a wiring bug (``TypeError``).
        """
        _validate_rule(rule)
        if rule.name in self._names:
            raise ConflictError(
                f"a routing rule named '{rule.name}' is already registered",
                details={"rule": rule.name},
            )
        self._rules.append(rule)
        self._names.add(rule.name)

    def unregister(self, name: str) -> bool:
        """Remove the rule named ``name``; return whether it was there."""
        if name not in self._names:
            return False
        self._rules = [rule for rule in self._rules if rule.name != name]
        self._names.discard(name)
        return True

    def route(self, request: RoutingRequest) -> Routing:
        """Classify ``request`` and name the handler that should take it.

        Rules are evaluated lowest ``priority`` first, ties in registration
        order; the first matcher that returns true decides. A matcher that
        raises propagates — the router does not swallow a bug in a rule. When no
        rule matches, the result is :attr:`Intent.UNKNOWN` with no handler.
        """
        for rule in self._ordered():
            if rule.matches is None or rule.matches(request):
                return Routing(
                    intent=rule.intent,
                    handler=rule.handler,
                    rule=rule.name,
                    matched=True,
                    reason=rule.description or f"matched rule '{rule.name}'",
                )
        return Routing(
            intent=Intent.UNKNOWN,
            handler=None,
            rule=None,
            matched=False,
            reason="no routing rule matched",
        )

    def rules(self) -> list[RouteRule]:
        """Every rule in evaluation order (priority, then registration)."""
        return self._ordered()

    def intents(self) -> list[Intent]:
        """The distinct intents any rule can produce, sorted by value."""
        return sorted({rule.intent for rule in self._rules}, key=str)

    def __len__(self) -> int:
        return len(self._rules)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._names

    def __repr__(self) -> str:
        return f"IntentRouter(rules={len(self._rules)})"

    def _ordered(self) -> list[RouteRule]:
        # A stable sort: equal priorities keep registration order, so the same
        # declarations always route the same way.
        return sorted(self._rules, key=lambda rule: rule.priority)


def keyword_matcher(*words: str, case_sensitive: bool = False) -> RoutingMatcher:
    """A matcher that fires when any whole word in ``words`` appears in the text.

    ULTRON's Phase 2 understanding is deliberately not an LLM call — there is no
    model in the pipeline yet (Phase 3, T066) — so this is the explicit
    placeholder: a word-boundary search over the request text, deterministic and
    trivially testable. Boundaries are ``(?<!\\w)…(?!\\w)`` rather than ``\\b`` so
    phrases and punctuated terms ("c++", "log in") still match where they should
    and "cat" does not fire inside "category".

    At least one non-empty word is required; an empty list is a wiring mistake
    (``ValueError``), because a matcher that can never fire is almost always a
    rule the author did not mean to write.
    """
    if not words:
        raise ValueError("keyword_matcher needs at least one word")
    for word in words:
        if not isinstance(word, str) or not word.strip():
            raise ValueError("keyword_matcher words must be non-empty strings")
    alternation = "|".join(re.escape(word) for word in words)
    flags = 0 if case_sensitive else re.IGNORECASE
    pattern = re.compile(rf"(?<!\w)(?:{alternation})(?!\w)", flags)

    def matches(request: RoutingRequest) -> bool:
        return pattern.search(request.text) is not None

    return matches


def _validate_rule(rule: RouteRule) -> None:
    """Refuse a structurally broken rule at registration time."""
    if not isinstance(rule.name, str) or not rule.name.strip():
        raise InvalidInputError(
            "a routing rule needs a non-empty name",
            details={"name": str(rule.name)[:100]},
        )
    if not isinstance(rule.handler, str) or not rule.handler.strip():
        raise InvalidInputError(
            "a routing rule needs a non-empty handler",
            details={"rule": rule.name, "handler": str(rule.handler)[:100]},
        )
    if not isinstance(rule.intent, Intent):
        raise InvalidInputError(
            "a routing rule's intent must be an Intent",
            details={"rule": rule.name, "intent": str(rule.intent)[:100]},
        )
    if isinstance(rule.priority, bool) or not isinstance(rule.priority, int):
        raise InvalidInputError(
            "a routing rule's priority must be an integer",
            details={"rule": rule.name, "priority": str(rule.priority)[:100]},
        )
    if rule.matches is not None and not callable(rule.matches):
        raise TypeError(f"routing rule '{rule.name}' has a non-callable matcher")
