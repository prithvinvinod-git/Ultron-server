"""Utility tools: ``util.time`` and ``util.calculate`` (spec §14).

Both are pure functions of their arguments, so the suite mostly checks the
edge the tool exists to handle: ``calculate`` must evaluate arithmetic and
refuse *everything else*, because that refusal is what makes it safe to hand a
model-produced string a live arithmetic parser.
"""

from __future__ import annotations

import pytest

from app.core.errors import InvalidInputError
from app.database.models import VerificationOutcome
from app.tools.util import build_util_tools
from app.tools.util.tools import CalculateTool, TimeTool

pytestmark = pytest.mark.unit


def test_build_util_tools_exposes_the_two_utility_tools() -> None:
    names = {tool.name for tool in build_util_tools()}
    assert names == {"util.time", "util.calculate"}


# --------------------------------------------------------------------------- #
# util.time
# --------------------------------------------------------------------------- #
async def test_time_reports_utc_iso_and_epoch() -> None:
    result = await TimeTool().execute({})
    assert result["iso"].endswith("+00:00")
    assert "UTC" in result["utc"]
    assert isinstance(result["epoch_seconds"], int)
    assert "local" not in result


async def test_time_includes_local_rendering_when_an_offset_is_given() -> None:
    result = await TimeTool().execute({"utc_offset_minutes": 330})
    assert result["utc_offset_minutes"] == 330
    assert result["offset_label"] == "UTC+05:30"
    assert "local" in result


async def test_time_renders_a_negative_offset() -> None:
    result = await TimeTool().execute({"utc_offset_minutes": -300})
    assert result["offset_label"] == "UTC-05:00"


async def test_time_verify_reports_success() -> None:
    tool = TimeTool()
    result = await tool.execute({})
    assert await tool.verify(result, {}) is VerificationOutcome.SUCCESS


# --------------------------------------------------------------------------- #
# util.calculate
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("2 + 2", 4.0),
        ("(1984 * 3) / 2", 2976.0),
        ("7 % 4", 3.0),
        ("2 ** 10", 1024.0),
        ("10 // 3", 3.0),
        ("-5 + 3", -2.0),
        ("2 * (3 + 4)", 14.0),
    ],
)
async def test_calculate_evaluates_arithmetic(expression: str, expected: float) -> None:
    result = await CalculateTool().execute({"expression": expression})
    assert result["result"] == expected


async def test_calculate_hides_the_trailing_point_on_integers() -> None:
    result = await CalculateTool().execute({"expression": "6 / 2"})
    assert result["display"] == "3"


async def test_calculate_keeps_the_fraction_when_there_is_one() -> None:
    result = await CalculateTool().execute({"expression": "1 / 3"})
    assert result["display"].startswith("0.33")


async def test_calculate_refuses_names() -> None:
    with pytest.raises(InvalidInputError):
        await CalculateTool().execute({"expression": "__import__('os').system('x')"})


async def test_calculate_refuses_calls() -> None:
    with pytest.raises(InvalidInputError):
        await CalculateTool().execute({"expression": "abs(-3)"})


async def test_calculate_reports_division_by_zero() -> None:
    with pytest.raises(InvalidInputError, match="division by zero"):
        await CalculateTool().execute({"expression": "1 / 0"})


async def test_calculate_rejects_broken_syntax() -> None:
    with pytest.raises(InvalidInputError):
        await CalculateTool().execute({"expression": "2 +"})


async def test_calculate_verify_reports_success() -> None:
    tool = CalculateTool()
    result = await tool.execute({"expression": "1 + 1"})
    assert await tool.verify(result, {"expression": "1 + 1"}) is VerificationOutcome.SUCCESS
