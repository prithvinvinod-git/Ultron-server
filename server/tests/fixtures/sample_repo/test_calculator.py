"""Tests for the sample repository's calculator module."""

from __future__ import annotations

from calculator import add, subtract


def test_add() -> None:
    assert add(2, 3) == 5


def test_subtract() -> None:
    assert subtract(5, 2) == 3
