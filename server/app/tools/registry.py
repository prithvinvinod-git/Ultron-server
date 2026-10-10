"""The unified Tool Registry (T035): one registry, one router, every tool.

Spec §14 says "Create a Tool Registry"; §59.6 makes it the *single* registry
every tool in the system is reached through, because "a future agent gains
capabilities without reimplementing them" — and the Tool Router sitting on
top of it performs lookup + schema validation (§16 stage 1) before the
permission layer ever sees the call. An agent does not import a tool module
and call it directly; a direct call would bypass schema validation, permission
checks and verification, which is the entire value of the pipeline (§59.6).

What this class does, in that order:

- **`register`** — rejects duplicate names (`ConflictError`; §59.6: the name
  is what appears in events and audit rows, so a silent overwrite would
  rewrite history) and rejects a tool whose `input_schema` is not itself a
  valid JSON Schema (`ToolSchemaInvalidError`). A broken schema caught at
  registration fails at startup; caught mid-call it fails every call.

- **`lookup` / `__contains__` / `list`** — retrieval. `list()` is sorted by
  name so the agent's tool list and any prompt built from it are stable
  across calls (unsorted dict order is a flaky prompt waiting to happen).
  Unknown names raise `ToolNotFoundError` (404) — never `KeyError`, which
  would surface as a 500 for a caller's typo.

- **`describe`** — the §59.6 record as data: the agent-facing triple
  (`name`, `description`, `input_schema`), the §15 level, `timeout`,
  §66.17's `reversibility` (shown *before* execution, per §66.17),
  `node_scope`, `audit`, plus `category` derived from the name prefix
  (`filesystem.read` → `filesystem`) rather than stored twice. The schema is
  deep-copied: this dict goes into prompts and responses, and a caller
  scribbling on it must not corrupt the registered tool.

- **`validate`** — §16 stage 1, powered by the reference `jsonschema`
  library (Draft 2020-12). Accepts a tool name or the `Tool` instance the
  executor already holds, validates `arguments` against `input_schema`, and
  returns a plain `dict` for the pipeline to pass on. A tool object that is
  *not* the registered one is resolved by name through the registry — the
  registry is the authority on what runs (§59.6), so validation can never be
  performed against a stray, unregistered instance.

Argument validation failures raise `ToolSchemaInvalidError` (422) with every
violation path-and-message in `details["errors"]`: the LLM reads the first,
the UI can render the list, and the agent's retry has something concrete to
correct. Registration-time schema failures use the same error type with a
message that says the *schema* is broken rather than the arguments.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from app.core.errors import ConflictError, ToolNotFoundError, ToolSchemaInvalidError
from app.tools.base import Tool

__all__ = ["ToolRegistry"]


class ToolRegistry:
    """In-memory registry of tool instances (§59.6). Synchronous by design:
    registration happens during startup wiring, lookups are dict reads, and
    there is no I/O to await — the async boundary is the pipeline (T036)."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    # ------------------------------------------------------------------ #
    # Registration
    # ------------------------------------------------------------------ #
    def register(self, tool: Tool) -> None:
        """Add one tool instance. Idempotent per *identity* of the name only:
        a second registration of the same name is a bug, not a reload."""
        if tool.name in self._tools:
            raise ConflictError(
                f"tool '{tool.name}' is already registered",
                details={"tool": tool.name},
            )
        self._check_schema(tool)
        self._tools[tool.name] = tool

    @staticmethod
    def _check_schema(tool: Tool) -> None:
        """Refuse a tool whose `input_schema` is not a usable JSON Schema."""
        schema = tool.input_schema
        if not isinstance(schema, dict):
            raise ToolSchemaInvalidError(
                tool.name,
                "input_schema must be a JSON object (Draft 2020-12)",
                details={"errors": [f"input_schema is {type(schema).__name__}"]},
            )
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:
            raise ToolSchemaInvalidError(
                tool.name,
                f"input_schema is not a valid JSON Schema: {exc.message}",
                details={"errors": [exc.message]},
            ) from exc

    # ------------------------------------------------------------------ #
    # Retrieval
    # ------------------------------------------------------------------ #
    def lookup(self, name: str) -> Tool:
        """The registered tool, or `ToolNotFoundError` (§59.6: names are the
        contract — a miss is a 404 with the name, not a KeyError 500)."""
        try:
            return self._tools[name]
        except KeyError:
            raise ToolNotFoundError(name, f"tool '{name}' is not registered") from None

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._tools

    def list(self) -> list[Tool]:
        """Every registered tool, sorted by name for a stable agent tool list."""
        return [self._tools[name] for name in sorted(self._tools)]

    # ------------------------------------------------------------------ #
    # Description (§59.6's required fields, as data)
    # ------------------------------------------------------------------ #
    def describe(self, tool: Tool | str) -> dict[str, Any]:
        """The registry's full record for one tool.

        The schema is deep-copied: this dict travels into prompts and API
        responses, and a caller mutating it must not corrupt the registry.
        """
        resolved = self.lookup(tool) if isinstance(tool, str) else self.lookup(tool.name)
        return {
            "name": resolved.name,
            "description": resolved.description,
            "input_schema": copy.deepcopy(resolved.input_schema),
            "permission_level": resolved.permission_level.value,
            "timeout": resolved.timeout,
            "reversibility": resolved.reversibility.value,
            "node_scope": resolved.node_scope,
            "audit": resolved.audit,
            # §59.6 category, derived from the name prefix rather than
            # declared twice: "filesystem.read" -> "filesystem".
            "category": resolved.name.partition(".")[0],
        }

    # ------------------------------------------------------------------ #
    # Schema validation (§16 stage 1 — the Tool Router's first duty)
    # ------------------------------------------------------------------ #
    def validate(self, tool: Tool | str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """Validate ``arguments`` against the registered tool's `input_schema`.

        Accepts the name or the instance; an instance is resolved *by name*
        through the registry so validation always runs against the registered
        tool, never a stray one (§59.6: the registry is the authority).

        Returns a plain ``dict`` for the pipeline to pass to ``execute``.
        Raises ``ToolSchemaInvalidError`` (422) with every violation in
        ``details["errors"]`` as ``path: message`` — the first of which is
        the retry hint the LLM needs.
        """
        name = tool if isinstance(tool, str) else tool.name
        registered = self.lookup(name)

        validator = Draft202012Validator(registered.input_schema)
        failures = sorted(validator.iter_errors(arguments), key=lambda e: list(e.path))
        if failures:
            messages = [
                f"{'/'.join(str(part) for part in error.path) or '<root>'}: {error.message}"
                for error in failures
            ]
            raise ToolSchemaInvalidError(
                registered.name,
                f"arguments failed schema validation: {messages[0]}",
                details={"errors": messages},
            )
        return dict(arguments)
