"""Small argument coercions shared by the tool families.

The pipeline validates arguments against each tool's schema before ``execute``
runs (§16 stage 1), so in normal operation these already hold the right types.
The tools still re-check here for two reasons: a tool may be constructed and
called directly in a unit test, and a value that slipped through as the wrong
type must become a typed ``InvalidInputError`` rather than an ``AttributeError``
deep inside the handler.
"""

from __future__ import annotations

from typing import Any

from app.core.errors import InvalidInputError

__all__ = ["as_bool", "as_int", "as_optional_int", "as_str"]


def as_int(
    value: Any,
    name: str,
    *,
    default: int | None = None,
    minimum: int | None = None,
) -> int:
    """Return ``value`` as an int, or ``default`` when it is absent."""
    if value is None:
        if default is None:
            raise InvalidInputError(f"{name} is required")
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidInputError(f"{name} must be an integer", details={name: repr(value)[:100]})
    if minimum is not None and value < minimum:
        raise InvalidInputError(f"{name} must be at least {minimum}", details={name: value})
    return value


def as_optional_int(value: Any, name: str, *, minimum: int | None = None) -> int | None:
    """Return ``value`` as an int, or ``None`` when it is absent.

    The distinction from :func:`as_int` is the absent case: there a missing
    value either raises or takes a non-``None`` default, here it is a valid
    ``None`` (an option that simply was not supplied, e.g. a per-call timeout).
    """
    if value is None:
        return None
    return as_int(value, name, minimum=minimum)


def as_bool(value: Any, name: str, *, default: bool = False) -> bool:
    """Return ``value`` as a bool, or ``default`` when it is absent."""
    if value is None:
        return default
    if not isinstance(value, bool):
        raise InvalidInputError(f"{name} must be a boolean", details={name: repr(value)[:100]})
    return value


def as_str(
    value: Any,
    name: str,
    *,
    default: str | None = None,
    allow_empty: bool = False,
) -> str:
    """Return ``value`` as a non-empty string unless ``allow_empty``."""
    if value is None:
        if default is not None:
            return default
        raise InvalidInputError(f"{name} is required")
    if not isinstance(value, str):
        raise InvalidInputError(f"{name} must be a string", details={name: repr(value)[:100]})
    if not allow_empty and not value:
        raise InvalidInputError(f"{name} must not be empty")
    return value
