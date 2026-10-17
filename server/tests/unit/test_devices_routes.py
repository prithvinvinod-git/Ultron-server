"""Device route tests (T162, spec §26/§29).

The manager's behaviour is covered by ``test_device_manager.py`` and the session
by ``test_device_transport.py``; what these tests answer is the part only the
assembled routes can: does ``GET /devices`` serialise a device, does an unknown
id become ``404``/``NOT_FOUND``, does ``DEVICES_ENABLED=false`` refuse the socket,
and does the WebSocket run the T160 handshake — register → registered, heartbeat
→ heartbeat_ack, a refused registration closing, and a closed socket marking the
device offline.

The container is stubbed at the same seam the node route tests use, and the app's
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

from app.api.dependencies import require_principal
from app.config.settings import DeviceSettings
from app.devices.manager import DeviceManager
from app.main import create_app
from app.security.authentication import Principal
from tests.support.device_double import FakeDevice

pytestmark = pytest.mark.unit


class _StubContainer:
    """The slice of the container the device routes reach for."""

    def __init__(self, manager: DeviceManager) -> None:
        self._manager = manager

    @property
    def device_manager(self) -> DeviceManager:
        return self._manager


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
    def __init__(self, client: TestClient, manager: DeviceManager) -> None:
        self.client = client
        self.manager = manager


def _build(*, require_auth: bool, enabled: bool = True) -> Any:
    manager = DeviceManager(
        settings=DeviceSettings(enabled=enabled), require_auth=require_auth
    )
    container = _StubContainer(manager)
    app = create_app()
    app.router.lifespan_context = _no_lifespan
    app.state.container = container
    app.dependency_overrides[require_principal] = _principal
    return app, manager


def _client(require_auth: bool, *, enabled: bool = True) -> Any:
    app, manager = _build(require_auth=require_auth, enabled=enabled)
    return app, manager


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Api]:
    monkeypatch.setenv("ALLOWED_HOSTS", "testserver")
    monkeypatch.setenv("METRICS_ENABLED", "false")
    from app.config import reload_settings

    reload_settings()
    app, manager = _client(require_auth=False)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield _Api(client, manager)
    app.dependency_overrides.clear()


@pytest.fixture
def secured_api(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Api]:
    monkeypatch.setenv("ALLOWED_HOSTS", "testserver")
    monkeypatch.setenv("METRICS_ENABLED", "false")
    from app.config import reload_settings

    reload_settings()
    app, manager = _client(require_auth=True)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield _Api(client, manager)
    app.dependency_overrides.clear()


async def _register(manager: DeviceManager, device: FakeDevice) -> FakeDevice:
    from app.devices.protocol import DeviceRegister

    registration = DeviceRegister.model_validate(device.registration(credential=None))
    await manager.register(registration, device.transport)
    return device


# --------------------------------------------------------------------------- #
# GET /devices
# --------------------------------------------------------------------------- #


class TestListDevices:
    def test_empty_registry_returns_an_empty_list(self, api: _Api) -> None:
        response = api.client.get("/devices")
        assert response.status_code == 200
        assert response.json() == []

    async def test_a_registered_device_is_listed(self, api: _Api) -> None:
        await _register(api.manager, FakeDevice("esp32-1"))
        response = api.client.get("/devices")
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["device_id"] == "esp32-1"
        assert body[0]["device_type"] == "esp32"
        assert body[0]["status"] == "online"
        assert "display" in body[0]["capabilities"]
        assert body[0]["firmware_version"] == "1.0.0"


class TestGetDevice:
    async def test_a_known_device_is_returned(self, api: _Api) -> None:
        await _register(api.manager, FakeDevice("esp32-1"))
        response = api.client.get("/devices/esp32-1")
        assert response.status_code == 200
        assert response.json()["device_id"] == "esp32-1"

    def test_an_unknown_device_is_a_404(self, api: _Api) -> None:
        response = api.client.get("/devices/ghost")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"
        assert response.json()["error"]["details"]["resource"] == "device"


# --------------------------------------------------------------------------- #
# WS /devices/ws
# --------------------------------------------------------------------------- #


class TestDeviceSocket:
    def test_register_then_heartbeat_round_trip(self, api: _Api) -> None:
        device = FakeDevice("esp32-1")
        with api.client.websocket_connect("/devices/ws") as socket:
            socket.send_json(device.registration(credential=None))
            hello = socket.receive_json()
            assert hello["type"] == "registered"
            assert hello["accepted"] is True
            assert hello["device_id"] == "esp32-1"
            assert hello["offline_after"] >= 1

            socket.send_json(device.heartbeat(status="online"))
            ack = socket.receive_json()
            assert ack["type"] == "heartbeat_ack"
            assert ack["device_id"] == "esp32-1"

        # The socket close marks the device offline via the session's disconnect.
        response = api.client.get("/devices/esp32-1")
        assert response.status_code == 200
        assert response.json()["status"] == "offline"

    def test_a_refused_registration_closes_the_socket(self, secured_api: _Api) -> None:
        device = FakeDevice("esp32-1", credential="not-a-real-token")
        with secured_api.client.websocket_connect("/devices/ws") as socket:
            socket.send_json(device.registration())
            hello = socket.receive_json()
            assert hello["accepted"] is False
            assert hello["reason"]
            with pytest.raises(WebSocketDisconnect):
                socket.receive_json()

    def test_an_issued_credential_is_accepted_over_the_socket(
        self, secured_api: _Api
    ) -> None:
        token = secured_api.manager.issue_credential("esp32-1")
        device = FakeDevice("esp32-1")
        with secured_api.client.websocket_connect("/devices/ws") as socket:
            socket.send_json(device.registration(credential=token))
            hello = socket.receive_json()
            assert hello["accepted"] is True

    def test_a_disabled_subsystem_refuses_the_socket(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALLOWED_HOSTS", "testserver")
        monkeypatch.setenv("METRICS_ENABLED", "false")
        from app.config import reload_settings

        reload_settings()
        app, _manager = _client(require_auth=False, enabled=False)
        with TestClient(app, raise_server_exceptions=False) as client:
            device = FakeDevice("esp32-1")
            with pytest.raises(WebSocketDisconnect), client.websocket_connect(
                "/devices/ws"
            ) as socket:
                socket.send_json(device.registration(credential=None))
                socket.receive_json()
        app.dependency_overrides.clear()
