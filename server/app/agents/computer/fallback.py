"""The computer-use capability fallback chain (T145, spec §13/§66.10).

§13's Computer Agent controls a desktop; §66.10 requires the *method* not be
hard-coded. There is a natural reliability order — a structured API is exact, a
DOM query is robust, accessibility (UIA) is steadier than pixels, a keyboard
shortcut beats aiming a mouse, and vision is the last resort — and the chain
below is that order, taken verbatim from the project's ``COMPUTER_CAPABILITY_
PRIORITY`` default so the config and the code cannot drift.

The chain is a **planner**: it chooses *how* to attempt an interaction and
records the ordered attempts. It never performs one — execution belongs to the
node (§64.6.5: the node executes tools, the Core reasons), and this module holds
no OS knowledge. That separation is also why it is testable with no Windows
present.

Three rules keep it honest (§54):

- **The chain is ordered, not arbitrary.** A higher-reliability method is always
  tried before a lower one; `select` returns the first *available* method.
- **Availability is declared, never assumed.** A deployment that cannot do
  vision simply does not offer ``vision``; the chain skips it. A method that is
  offered but fails is the *caller's* retry, not a silent substitution — §64.11
  forbids a quiet re-route.
- **An empty chain is a typed refusal**, not a hang: with no method available
  the caller gets a clear "no method available" rather than a loop that never
  terminates.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from enum import StrEnum

from app.core.errors import CapabilityNotImplementedError, InvalidInputError

__all__ = [
    "CAPABILITY_METHODS",
    "DEFAULT_PRIORITY",
    "FallbackAttempt",
    "FallbackPlan",
    "InteractionMethod",
    "build_plan",
    "methods_for_capabilities",
    "select_method",
]


class InteractionMethod(StrEnum):
    """How a desktop interaction may be performed, most reliable first.

    The member *values* are the strings that appear on the wire and in
    ``COMPUTER_CAPABILITY_PRIORITY``, so a config value and an enum member are
    the same token.
    """

    APPLICATION_API = "application_api"
    DOM = "dom"
    UI_AUTOMATION = "ui_automation"
    KEYBOARD_SHORTCUT = "keyboard_shortcut"
    MOUSE = "mouse"
    VISION = "vision"


#: The default reliability order (§13/§66.10). Mirrors
#: `ComputerNodeSettings.capability_priority`; the config is what a deployment
#: may reorder, and `select_method` validates against this set.
DEFAULT_PRIORITY: tuple[InteractionMethod, ...] = (
    InteractionMethod.APPLICATION_API,
    InteractionMethod.DOM,
    InteractionMethod.UI_AUTOMATION,
    InteractionMethod.KEYBOARD_SHORTCUT,
    InteractionMethod.MOUSE,
    InteractionMethod.VISION,
)


def _coerce(method: InteractionMethod | str) -> InteractionMethod:
    if isinstance(method, InteractionMethod):
        return method
    try:
        return InteractionMethod(method)
    except ValueError as exc:
        raise InvalidInputError(
            f"unknown interaction method '{method}'",
            details={"allowed": [m.value for m in InteractionMethod]},
        ) from exc


def coerce_priority(
    priority: list[str] | list[InteractionMethod] | None,
) -> tuple[InteractionMethod, ...]:
    """Normalise a configured priority into a tuple of methods, preserving order.

    A configured list is used as-is (order is the point); a duplicate is
    refused because a duplicated method makes "first available" ambiguous.
    """
    if not priority:
        return DEFAULT_PRIORITY
    methods = tuple(_coerce(method) for method in priority)
    if len(set(methods)) != len(methods):
        raise InvalidInputError(
            "the capability priority contains a duplicate method",
            details={"priority": [m.value for m in methods]},
        )
    return methods


class FallbackAttempt:
    """One planned attempt: a method, and why it was chosen or skipped."""

    __slots__ = ("available", "method", "reason")

    def __init__(self, method: InteractionMethod, *, available: bool, reason: str = "") -> None:
        self.method = method
        self.available = available
        self.reason = reason

    def to_dict(self) -> dict[str, object]:
        return {
            "method": self.method.value,
            "available": self.available,
            "reason": self.reason,
        }

    def __repr__(self) -> str:
        state = "available" if self.available else "skipped"
        return f"<FallbackAttempt {self.method.value} {state}>"


class FallbackPlan:
    """The ordered attempts for one goal, and the method chosen to lead."""

    __slots__ = ("attempts", "created_at", "goal", "selected")

    def __init__(
        self,
        goal: str,
        attempts: tuple[FallbackAttempt, ...],
        selected: InteractionMethod | None,
    ) -> None:
        self.goal = goal
        self.attempts = attempts
        self.selected = selected
        self.created_at = datetime.now(UTC)

    @property
    def available_methods(self) -> tuple[InteractionMethod, ...]:
        return tuple(attempt.method for attempt in self.attempts if attempt.available)

    def to_dict(self) -> dict[str, object]:
        return {
            "goal": self.goal,
            "selected": self.selected.value if self.selected is not None else None,
            "attempts": [attempt.to_dict() for attempt in self.attempts],
        }

    def __repr__(self) -> str:
        chosen = self.selected.value if self.selected is not None else "none"
        return f"<FallbackPlan goal={self.goal!r} selected={chosen}>"


def select_method(
    available: set[InteractionMethod] | set[str],
    *,
    priority: list[str] | list[InteractionMethod] | None = None,
) -> InteractionMethod | None:
    """Return the most reliable available method, or ``None`` if none is.

    ``None`` is a value the caller must handle (no method available ⇒ refuse
    honestly, §54); it is not an exception because "which methods a node
    advertises" is a query, and a query returning empty is not an error.
    """
    order = coerce_priority(priority)
    normalised = {_coerce(method) for method in available}
    for method in order:
        if method in normalised:
            return method
    return None


def build_plan(
    goal: str,
    available: set[InteractionMethod] | set[str],
    *,
    priority: list[str] | list[InteractionMethod] | None = None,
    require: InteractionMethod | str | None = None,
) -> FallbackPlan:
    """Build the ordered attempt plan for ``goal`` against ``available`` methods.

    When ``require`` names a method the caller insists on, the plan leads with it
    (if available) and refuses (`CapabilityNotImplementedError`) when it is not —
    a required method that cannot run is not silently swapped for a weaker one
    (§64.11: the model expresses a preference; a silent re-route is forbidden).
    """
    text = (goal or "").strip()
    if not text:
        raise InvalidInputError(
            "a fallback plan needs a goal", details={"goal": repr(goal)[:100]}
        )
    order = coerce_priority(priority)
    normalised = {_coerce(method) for method in available}

    if require is not None:
        required = _coerce(require)
        if required not in normalised:
            raise CapabilityNotImplementedError(
                f"the required interaction method '{required.value}' is not available"
            )
        lead: InteractionMethod | None = required
    else:
        lead = select_method(normalised, priority=list(order))

    attempts = tuple(
        FallbackAttempt(
            method,
            available=method in normalised,
            reason="" if method in normalised else "not advertised by the target node",
        )
        for method in order
    )
    return FallbackPlan(text, attempts, lead)


#: Which interaction methods a node's advertised capability family implies
#: (§64.10 capability families → §66.10 methods). A node advertising
#: ``windows_ui`` can be driven by accessibility, shortcuts or the pointer; one
#: that advertises only ``screenshots`` can be driven by vision and the pointer.
#: Unknown capability strings contribute nothing — availability is declared,
#: never assumed.
CAPABILITY_METHODS: dict[str, frozenset[InteractionMethod]] = {
    "windows_apps": frozenset({InteractionMethod.APPLICATION_API}),
    "linux_apps": frozenset({InteractionMethod.APPLICATION_API}),
    "git": frozenset({InteractionMethod.APPLICATION_API}),
    "browser": frozenset({InteractionMethod.DOM, InteractionMethod.APPLICATION_API}),
    "windows_ui": frozenset(
        {
            InteractionMethod.UI_AUTOMATION,
            InteractionMethod.KEYBOARD_SHORTCUT,
            InteractionMethod.MOUSE,
        }
    ),
    "screenshots": frozenset({InteractionMethod.VISION, InteractionMethod.MOUSE}),
    "powershell": frozenset({InteractionMethod.KEYBOARD_SHORTCUT}),
    "linux_shell": frozenset({InteractionMethod.KEYBOARD_SHORTCUT}),
}


def methods_for_capabilities(
    capabilities: Iterable[str],
) -> set[InteractionMethod]:
    """Expand advertised capability families into the interaction methods they imply.

    The bridge between §64.10's capability vocabulary (what a node advertises)
    and §66.10's method chain (how it is driven). An unknown capability adds
    nothing, so an unrecognised node simply yields a shorter chain rather than a
    method that was never promised.
    """
    methods: set[InteractionMethod] = set()
    for capability in capabilities:
        methods |= CAPABILITY_METHODS.get(str(capability), frozenset())
    return methods
