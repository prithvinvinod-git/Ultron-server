"""Deterministic mock tools used by the test suite (T037).

`mock.echo`, `mock.fail`, `mock.sleep`, `mock.write_state` — one module
each, one probe each. They are **never registered by the application**:
each test registers the ones it needs, so a production registry cannot
grow a `mock.*` entry by accident (§66.7's registry is the authority, and
the authority keeps its fictions out of the real catalogue).
"""
