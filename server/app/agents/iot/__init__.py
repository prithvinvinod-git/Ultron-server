"""IoT agent for device interaction through the device gateway (T164, spec §26).

:class:`IoTAgent` acts on the device layer the T160 protocol and T161 gateway
define — reacting to device events and dispatching structured commands, with an
optional model-driven ``device.*`` tool loop behind it.
"""

from __future__ import annotations

from app.agents.iot.agent import IoTAgent

__all__ = ["IoTAgent"]
