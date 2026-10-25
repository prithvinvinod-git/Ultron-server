"""Orb control and resync routes (spec §27 / §59.13).

The orb's *state* is produced by :class:`~app.orb.presenter.OrbPresenter` from
real Core events and fanned out over ``GET /events`` (topic ``orb``). These
routes add the two things a display needs beyond the stream:

* ``GET /orb/state`` — the current snapshot, so a client that was disconnected
  longer than the bus replay window can resync instead of guessing;
* ``POST /orb/show|hide`` — explicit visibility commands, which publish the
  canonical ``ORB_SHOW`` / ``ORB_HIDE`` events (never a private side channel);
* ``POST /orb/resync`` — re-broadcast the snapshot so every connected renderer
  converges after a reconnect.

No business logic lives here: the presenter owns the state machine, and the
voice/agent/task subsystems own the events that drive it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Body
from pydantic import BaseModel, ConfigDict

from app.api.dependencies import Auth, Container

router = APIRouter(prefix="/orb", tags=["orb"])


class OrbSnapshotRead(BaseModel):
    """The wire shape of ``GET /orb/state`` (versioned by ``schema_version``)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int
    state: str
    visible: bool
    updated_at: datetime
    source_event: str | None = None
    source_event_id: int | None = None
    active: dict[str, Any]
    wake: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    notification: dict[str, Any] | None = None
    transcripts: dict[str, str] = {}


class OrbCommand(BaseModel):
    """Body for show/hide. ``reason`` is recorded in the event payload."""

    model_config = ConfigDict(extra="forbid")

    reason: str = "manual"


@router.get("/state", response_model=OrbSnapshotRead)
async def read_orb_state(container: Container, _: Auth) -> OrbSnapshotRead:
    """Return the current orb state (the reconnect/resync document)."""
    return OrbSnapshotRead.model_validate(container.orb.snapshot())


@router.post("/resync", response_model=OrbSnapshotRead)
async def resync_orb(container: Container, _: Auth) -> OrbSnapshotRead:
    """Re-broadcast the current state and return it.

    A client calls this after its SSE stream reconnects. The alternative —
    trusting ``Last-Event-ID`` — silently loses state when the outage outlasts
    the bus's bounded replay buffer.
    """
    return OrbSnapshotRead.model_validate(await container.orb.resync())


@router.post("/show", response_model=OrbSnapshotRead)
async def show_orb(
    container: Container,
    _: Auth,
    command: Annotated[OrbCommand, Body()] = OrbCommand(),
) -> OrbSnapshotRead:
    """Publish ``ORB_SHOW`` so every renderer reveals the orb."""
    return OrbSnapshotRead.model_validate(await container.orb.show(reason=command.reason))


@router.post("/hide", response_model=OrbSnapshotRead)
async def hide_orb(
    container: Container,
    _: Auth,
    command: Annotated[OrbCommand, Body()] = OrbCommand(),
) -> OrbSnapshotRead:
    """Publish ``ORB_HIDE`` so every renderer hides the orb (Core keeps running)."""
    return OrbSnapshotRead.model_validate(await container.orb.hide(reason=command.reason))
