"""Deterministic mock agents used by the test suite (T045).

`mock.agent`, `mock.long_running`, `mock.failing` — one module each, one probe
each. They are **never registered by the application**: each test registers the
ones it needs, so a production registry cannot grow a `mock.*` entry by accident
(the T037 rule for tools, kept for agents — §40 makes the kind a declaration on
the agent, and the authority keeps its fictions out of the real catalogue).
"""
