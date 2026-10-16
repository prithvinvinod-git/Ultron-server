"""Node route tests (T141/T146, spec §13/§29/§64).

The gateway's behaviour is covered by ``test_computer_gateway.py``; what these
tests answer is the part only the assembled routes can: does ``GET /nodes``
serialise a node, does an unknown id become ``404``/``NODE_NOT_FOUND``, and does
the WebSocket route run the T140 handshake — register → ``registered``, heartbeat
→ ``heartbeat_ack``, a refused registration closing the socket, and a closed
socket marking the node offline.

The container is stubbed at the same seam the Core route tests use, and the app's
lifespan is replaced with a no-op so no test needs PostgreSQL or Redis.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.agents.computer.gateway import NodeGateway
from app.agents.computer.protocol import NodeRegistration
from app.api.dependencies import require_principal
from app.config import reload_settings
from app.main import create_app
from app.security.authentication import Principal
from tests.support.node_double import FakeNode

pytestmark = pytest.mark.unit


class _StubContainer:
    """The slice of the container the node routes reach for."""

    def __init__(self, gateway: NodeGateway) -> None:
        self._gateway = gateway

    @property
    def node_gateway(self) -> NodeGateway:
        return self._gateway


def _principal() -> Principal:
    return Principal(
        actor_type="user",
        user_id=uuid.uuid4(),
        username="tester",
        is_superuser=True,
    )


@asynccontextmanager
async def _no_lifespan(app: Any) -> AsyncIterator[None]:
    """Skip the real lifespan (PostgreSQL/Redis pings) but keep the ASGI portal."""
    yield


class _Api:
    def __init__(self, client: TestClient, gateway: NodeGateway) -> None:
        self.client = client
        self.gateway = gateway


def _build(require_auth: bool) -> Any:
    gateway = NodeGateway(require_auth=require_auth)
    container = _StubContainer(gateway)
    app = create_app()
    app.router.lifespan_context = _no_lifespan
    app.state.container = container
    app.dependency_overrides[require_principal] = _principal
    return app, gateway


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Api]:
    monkeypatch.setenv("ALLOWED_HOSTS", "testserver")
    monkeypatch.setenv("METRICS_ENABLED", "false")
    reload_settings()
    app, gateway = _build(require_auth=False)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield _Api(client, gateway)
    app.dependency_overrides.clear()


@pytest.fixture
def secured_api(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Api]:
    monkeypatch.setenv("ALLOWED_HOSTS", "testserver")
    monkeypatch.setenv("METRICS_ENABLED", "false")
    reload_settings()
    app, gateway = _build(require_auth=True)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield _Api(client, gateway)
    app.dependency_overrides.clear()


async def _register(gateway: NodeGateway, node: FakeNode) -> FakeNode:
    registration = NodeRegistration.model_validate(node.registration(credential=""))
    await gateway.register(registration, node.transport)
    return node


# --------------------------------------------------------------------------- #
# GET /nodes
# --------------------------------------------------------------------------- #


class TestListNodes:
    def test_empty_registry_returns_an_empty_list(self, api: _Api) -> None:
        response = api.client.get("/nodes")
        assert response.status_code == 200
        assert response.json() == []

    async def test_a_registered_node_is_listed(self, api: _Api) -> None:
        await _register(api.gateway, FakeNode(node_id="windows-1"))
        response = api.client.get("/nodes")
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["node_id"] == "windows-1"
        assert body[0]["node_type"] == "windows"
        assert body[0]["status"] == "online"
        assert "windows_ui" in body[0]["capabilities"]


class TestGetNode:
    async def test_a_known_node_is_returned(self, api: _Api) -> None:
        await _register(api.gateway, FakeNode(node_id="windows-1"))
        response = api.client.get("/nodes/windows-1")
        assert response.status_code == 200
        assert response.json()["node_id"] == "windows-1"

    def test_an_unknown_node_is_a_404(self, api: _Api) -> None:
        response = api.client.get("/nodes/ghost")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NODE_NOT_FOUND"


# --------------------------------------------------------------------------- #
# WS /nodes/ws
# --------------------------------------------------------------------------- #


class TestNodeSocket:
    def test_register_then_heartbeat_round_trip(self, api: _Api) -> None:
        node = FakeNode(node_id="windows-1")
        with api.client.websocket_connect("/nodes/ws") as socket:
            socket.send_json(node.registration(credential=""))
            hello = socket.receive_json()
            assert hello["type"] == "registered"
            assert hello["accepted"] is True
            assert hello["node_id"] == "windows-1"
            assert hello["heartbeat_interval"] >= 1

            socket.send_json(node.heartbeat(status="online"))
            ack = socket.receive_json()
            assert ack["type"] == "heartbeat_ack"
            assert ack["node_id"] == "windows-1"

        # The socket close marks the node offline via the route's disconnect.
        response = api.client.get("/nodes/windows-1")
        assert response.status_code == 200
        assert response.json()["status"] == "offline"

    def test_a_refused_registration_closes_the_socket(self, secured_api: _Api) -> None:
        node = FakeNode(node_id="windows-1")
        with secured_api.client.websocket_connect("/nodes/ws") as socket:
            socket.send_json(node.registration(credential=""))
            hello = socket.receive_json()
            assert hello["accepted"] is False
            assert hello["reason"]
            with pytest.raises(WebSocketDisconnect):
                socket.receive_json()

    def test_an_issued_credential_is_accepted_over_the_socket(
        self, secured_api: _Api
    ) -> None:
        token = secured_api.gateway.issue_credential("windows-1")
        node = FakeNode(node_id="windows-1")
        with secured_api.client.websocket_connect("/nodes/ws") as socket:
            socket.send_json(node.registration(credential=token))
            hello = socket.receive_json()
            assert hello["accepted"] is True
