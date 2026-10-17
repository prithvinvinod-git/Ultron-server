"""MQTT availability probes (T163, spec §26).

The MQTT device transport is optional and gated behind the ``iot`` extra
(``paho-mqtt``). ULTRON must never assume the broker client is installed: the
probe returns ``False`` with a reason so a deployment that set ``MQTT_ENABLED``
without installing the extra degrades honestly — the WebSocket transport keeps
working and the server logs why MQTT stayed dormant — rather than crashing at
startup (§64.11).
"""

from __future__ import annotations

import functools
import importlib.util

__all__ = [
    "paho_available",
    "paho_unavailable_reason",
]


@functools.lru_cache(maxsize=1)
def paho_available() -> bool:
    """Whether the ``paho-mqtt`` package can be imported.

    Cached: the answer cannot change without a process restart.
    """
    return importlib.util.find_spec("paho") is not None


@functools.lru_cache(maxsize=1)
def paho_unavailable_reason() -> str | None:
    """A human-readable reason MQTT is unavailable, else ``None``."""
    if paho_available():
        return None
    return (
        "the 'paho-mqtt' package is not installed; install the 'iot' extra "
        "(pip install -e '.[iot]') to enable the MQTT device transport"
    )
