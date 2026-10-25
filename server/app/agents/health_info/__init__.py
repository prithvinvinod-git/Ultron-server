"""Health-information agent (§59.12): informational medical context only."""

from __future__ import annotations

from .agent import HealthInfoAgent
from .report import HealthReport, OutputFrame, SafetyCategory

__all__ = [
    "HealthInfoAgent",
    "HealthReport",
    "OutputFrame",
    "SafetyCategory",
]
