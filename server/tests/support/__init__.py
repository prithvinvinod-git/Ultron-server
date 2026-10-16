"""Test support code shared across suites (T146).

Not a test package: modules here are imported by tests, never collected. The
computer-node double lives here because the gateway (T141), tool (T143), agent
(T144) and route (T141) suites all speak to the same node, and a second copy of
it would be a second thing to keep honest.
"""
