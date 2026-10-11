"""The coding agent (Phase 4, spec sections 8 and 9): engines, the agent, verification.

The one rule §8 states up front — "ULTRON should NOT hard-code OpenCode
throughout the system" — is what gives this package its shape: the engine is a
*declared* interface (:mod:`app.agents.coding.engine`, T081), OpenCode is one
adapter for it (:mod:`opencode_engine`, T082), the agent drives whichever
engine it is given (:mod:`agent`, T083), and verification is a small typed
decision (:mod:`verify`, T084) so the whole path can be tested against a fake
engine over a real sample repository.
"""

from app.agents.coding.agent import CodingAgent
from app.agents.coding.engine import (
    CodingEngine,
    CodingEvent,
    CodingEventCallback,
    CodingEventKind,
    CodingRunResult,
    CodingTask,
)
from app.agents.coding.opencode_engine import OpenCodeEngine
from app.agents.coding.verify import expected_files_present, verify_coding_result

__all__ = [
    "CodingAgent",
    "CodingEngine",
    "CodingEvent",
    "CodingEventCallback",
    "CodingEventKind",
    "CodingRunResult",
    "CodingTask",
    "OpenCodeEngine",
    "expected_files_present",
    "verify_coding_result",
]
