"""Utility tools (spec §14): the small, stateless capabilities.

Two tools that need nothing but the request itself:

* ``util.time`` — the current date and time, so the agent never guesses.
* ``util.calculate`` — arithmetic, evaluated from a parsed syntax tree rather
  than ``eval`` (see :func:`_evaluate` for why that distinction is the whole
  point of the tool).

Both are read-only and reversible: they touch no external state, so the
permission engine has nothing to guard and the audit log nothing to record.
"""

from __future__ import annotations

import ast
import operator
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, ClassVar

from app.core.errors import InvalidInputError
from app.database.models import PermissionLevel, VerificationOutcome
from app.tools.base import Reversibility, Tool

__all__ = ["CalculateTool", "TimeTool"]

#: The four arithmetic operators, named exactly as their AST nodes.
_BINARY_OPS: dict[type[ast.operator], Callable[[float, float], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.FloorDiv: operator.floordiv,
}
_UNARY_OPS: dict[type[ast.unaryop], Callable[[float], float]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def _evaluate(node: ast.AST) -> float:
    """Evaluate one arithmetic AST node, or raise ``InvalidInputError``.

    This is an allow-list walk, never ``eval``: only numeric literals, the
    arithmetic operators and parentheses are understood. Anything else — a
    name, a call, an attribute, a comparison — is refused with the offending
    construct named, so a prompt-injected expression has no code path to a
    side effect. ``eval`` would have to be *trusted* to be safe; this can only
    ever be arithmetic.
    """
    if isinstance(node, ast.Expression):
        return _evaluate(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise InvalidInputError(
                "expression may only contain numbers and arithmetic operators",
                details={"invalid": repr(node.value)},
            )
        return float(node.value)
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPS:
        left = _evaluate(node.left)
        right = _evaluate(node.right)
        try:
            return _BINARY_OPS[type(node.op)](left, right)
        except ZeroDivisionError as exc:
            raise InvalidInputError(
                "division by zero", details={"expression": ast.unparse(node)}
            ) from exc
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_evaluate(node.operand))
    raise InvalidInputError(
        "expression may only contain numbers and arithmetic operators",
        details={"invalid": ast.unparse(node) if node.__class__.__name__ != "Module" else "?"},
    )


class TimeTool(Tool):
    """The current date and time.

    Returns ISO-8601 UTC plus a human-readable UTC string, and, when the
    caller supplies an ``utc_offset_minutes``, the same instant in that offset.
    The offset is an argument rather than a host setting because the server
    has no idea which timezone a browser is in; the client owns that fact and
    passes it, keeping the tool a pure function of its input.
    """

    name = "util.time"
    description = (
        "Get the current date and time in UTC (and optionally a caller-supplied "
        "UTC offset). Use it for 'what time is it', 'what day is it', or any "
        "question about the present moment."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "utc_offset_minutes": {
                "type": "integer",
                "minimum": -1080,
                "maximum": 1080,
                "description": (
                    "Minutes east of UTC to also render, e.g. 330 for India "
                    "(UTC+5:30) or -300 for US Eastern. Omit for UTC only."
                ),
            },
        },
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.READ_ONLY
    timeout = 5.0
    reversibility = Reversibility.REVERSIBLE

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        now = datetime.now(UTC)
        payload: dict[str, Any] = {
            "iso": now.isoformat(),
            "utc": now.strftime("%A, %d %B %Y, %H:%M:%S UTC"),
            "epoch_seconds": int(now.timestamp()),
        }
        offset = arguments.get("utc_offset_minutes")
        if offset is not None:
            tz = timezone(timedelta(minutes=int(offset)))
            local = now.astimezone(tz)
            sign = "+" if offset >= 0 else "-"
            hours, minutes = divmod(abs(int(offset)), 60)
            payload["utc_offset_minutes"] = int(offset)
            payload["offset_label"] = f"UTC{sign}{hours:02d}:{minutes:02d}"
            payload["local"] = local.strftime("%A, %d %B %Y, %H:%M:%S")
        return payload

    async def verify(self, result: Any, arguments: Mapping[str, Any]) -> VerificationOutcome:
        if isinstance(result, Mapping) and isinstance(result.get("iso"), str):
            return VerificationOutcome.SUCCESS
        return VerificationOutcome.FAILED


class CalculateTool(Tool):
    """Evaluate an arithmetic expression.

    The expression is parsed with :mod:`ast` and evaluated by the allow-list
    walk in :func:`_evaluate`; ``eval`` is never used. The result is returned
    both as a float and as a display string (an integral result loses its
    ``.0``), so the model can quote it directly without reformatting.
    """

    name = "util.calculate"
    description = (
        "Safely evaluate an arithmetic expression using + - * / // % ** and "
        "parentheses. Use it for any arithmetic instead of computing in your "
        "head; it never touches anything but numbers."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "minLength": 1,
                "description": "Arithmetic expression, e.g. '(1984 * 3) / 2 + 7 % 4'.",
            },
        },
        "required": ["expression"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.READ_ONLY
    timeout = 5.0
    reversibility = Reversibility.REVERSIBLE

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        expression = str(arguments["expression"])
        try:
            tree = ast.parse(expression, mode="eval")
        except SyntaxError as exc:
            raise InvalidInputError(
                "expression is not valid arithmetic syntax",
                details={"expression": expression, "reason": exc.msg},
            ) from exc
        value = _evaluate(tree)
        return {
            "expression": expression,
            "result": value,
            "display": str(int(value)) if value.is_integer() else repr(value),
        }

    async def verify(self, result: Any, arguments: Mapping[str, Any]) -> VerificationOutcome:
        if isinstance(result, Mapping) and isinstance(result.get("result"), (int, float)):
            return VerificationOutcome.SUCCESS
        return VerificationOutcome.FAILED
