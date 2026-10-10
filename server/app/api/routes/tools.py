"""Tool routes (T051, §14/§59.6).

Read-only over the T035 registry, plus one non-mutating validation call. There
is deliberately **no execute endpoint**: §16 makes execution a pipeline, not a
REST call — permission check, argument validation, timeout, verification and
audit all have to run, and a route that dialled a tool directly would bypass
every one of them. Execution arrives with the tool router (T036+), not here.

The registry is the authority for the schema. ``POST /tools/{name}/validate``
is answered by ``ToolRegistry.validate`` for that reason: it is the same check
the pipeline performs, against the same registered tool, so a client cannot get
a second opinion that disagrees with the one that matters.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from app.api.dependencies import Auth, Container

router = APIRouter(prefix="/tools", tags=["tools"])


class ToolRead(BaseModel):
    """One tool's §59.6 description record.

    ``from_attributes`` is unused here — the registry returns a plain dict — but
    the model is constructed from that dict directly, and the field set is the
    registry's contract restated as a type, so a removed field becomes a 500 at
    schema-build time rather than a silently thinner response.
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    permission_level: str
    timeout: float
    reversibility: str
    node_scope: str | None = None
    audit: bool
    category: str


class ToolValidateRequest(BaseModel):
    """Arguments to validate against one tool's ``input_schema``."""

    model_config = ConfigDict(extra="forbid")

    arguments: dict[str, Any]


class ToolValidationResult(BaseModel):
    """The validated arguments, echoed back for the caller to pass on.

    A failure is not represented here: it raises ``ToolSchemaInvalidError``,
    which the app turns into a 422 carrying every violation, so the success
    shape has nothing to say beyond "these arguments are valid".
    """

    valid: bool = True
    tool: str
    arguments: dict[str, Any]


@router.get("", response_model=list[ToolRead], summary="List registered tools")
async def list_tools(container: Container, principal: Auth) -> list[ToolRead]:
    """Return every registered tool, sorted by name."""
    return [
        ToolRead.model_validate(container.tools.describe(tool)) for tool in container.tools.list()
    ]


@router.post(
    "/{name}/validate",
    response_model=ToolValidationResult,
    summary="Validate arguments against a tool's schema",
)
async def validate_tool(
    name: str,
    payload: ToolValidateRequest,
    container: Container,
    principal: Auth,
) -> ToolValidationResult:
    """Validate ``arguments`` for ``name``; a violation is a 422 with details."""
    validated = container.tools.validate(name, payload.arguments)
    return ToolValidationResult(tool=name, arguments=validated)


@router.get("/{name}", response_model=ToolRead, summary="Describe one tool")
async def get_tool(name: str, container: Container, principal: Auth) -> ToolRead:
    """Return one tool's description, or ``404``/``TOOL_NOT_FOUND``."""
    return ToolRead.model_validate(container.tools.describe(name))


__all__ = [
    "ToolRead",
    "ToolValidateRequest",
    "ToolValidationResult",
    "router",
]
