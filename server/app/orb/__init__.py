"""The orb display contract (spec §27 / §59.13).

:class:`~app.orb.presenter.OrbPresenter` projects real Core events onto one
versioned orb state. Nothing in this package starts a voice session, an agent or
a task — it is a read model that the SSE stream and ``GET /orb/state`` expose.
"""

from __future__ import annotations

from app.orb.presenter import OrbPresenter
from app.orb.states import ORB_STATE_SCHEMA_VERSION, OrbState

__all__ = ["ORB_STATE_SCHEMA_VERSION", "OrbPresenter", "OrbState"]
