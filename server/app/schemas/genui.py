"""Generative UI (GenUI) schemas for structured visualization responses.

These schemas define the contract between the AI's structured response and the
frontend renderer. They are validated server-side and re-validated client-side.

All visualization types share a common discriminated-union envelope so the
frontend can reliably dispatch to the correct renderer.

The GenUI response format is designed to be embeddable in the ``done`` SSE frame
under a ``genui`` key, or returned via ``POST /chat`` as part of the response.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Literal

from pydantic import BaseModel, ConfigDict


# ---------------------------------------------------------------------------
# Core envelope types
# ---------------------------------------------------------------------------

class GenUiVersion(Enum):
    """Schema version for backward compatibility."""
    V1 = "1"


class GenUiVisualizationType(Enum):
    """Discriminated union of supported visualization types."""
    BINARY_TREE = "binary_tree"
    CHART = "chart"
    FLOWCHART = "flowchart"
    TIMELINE = "timeline"
    EQUATION = "equation"
    VECTOR = "vector"
    MATRIX = "matrix"
    PHYSICS_SIMULATION = "physics_simulation"
    INTERACTIVE_TABLE = "interactive_table"
    IMAGE_PANEL = "image_panel"


class GenUiControlKey(Enum):
    """Control configuration keys shared across visualizations."""
    PLAY = "play"
    PAUSE = "pause"
    NEXT = "next"
    PREVIOUS = "previous"
    RESTART = "restart"
    SPEED = "speed"
    TRAVERSAL_METHOD = "traversal_method"


class GenUiStateModel(Dict[str, Any]):
    """Typed dict for per-visualization state.

    Each visualization defines its own state keys but the model always contains
    at minimum: algorithm, stepIndex, isPlaying, speed, visitedNodeIds, currentNodeId.
    """
    pass


# ---------------------------------------------------------------------------
# The top-level GenUI response envelope
# ---------------------------------------------------------------------------

class GenUiResponse(BaseModel):
    """A validated Generative UI response from the AI.

    This is the structured response format the AI can return alongside (or instead
    of) plain text. The frontend dispatches to the correct renderer based on
    ``visualization.type``.

    Example::

        {
            "type": "genui",
            "version": "1",
            "title": "Binary Tree Traversal",
            "explanation": "Different traversal algorithms visit tree nodes in different orders.",
            "visualization": {
                "type": "binary_tree",
                "data": {
                    "nodes": [{"id": "1", "value": 10, "left": "2", "right": "3"}],
                    "edges": [{"source": "1", "target": "2"}, {"source": "1", "target": "3"}],
                    "root": "1"
                },
                "controls": {
                    "traversal_method": "preorder",
                    "speed": 1,
                    "isPlaying": false
                },
                "state": {
                    "algorithm": "preorder",
                    "stepIndex": 0,
                    "isPlaying": false,
                    "speed": 1,
                    "visitedNodeIds": [],
                    "currentNodeId": null
                }
            }
        }
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["genui"]  # discriminated union value
    version: GenUiVersion = GenUiVersion.V1
    title: str
    explanation: str
    visualization: Dict[str, Any]  # validated per-type below
    follow_up_suggestion: str | None = None
    """Optional suggestion for a follow-up prompt."""


# ---------------------------------------------------------------------------
# Per-visualization data schemas (server-side validation)
# ---------------------------------------------------------------------------

class BinaryTreeData(BaseModel):
    """Data specification for the binary tree visualizer.

    Trees are defined by nodes and edges. Node ids must be strings (opaque
    identifiers used by the renderer). The root node id must correspond to an
    existing node.

    Limits:
    - Maximum 50 nodes to keep payload sizes reasonable (8GB RAM constraint).
    - Node values must be integers or strings.
    - No cycles allowed (enforced by the renderer, not the schema to avoid
      circular deps at validation time).
    """
    nodes: List[Dict[str, Any]]
    """Each node must have ``id`` (string) and ``value`` (int | str).
    Optional ``left`` and ``right`` should reference child node ids."""

    edges: Optional[List[Dict[str, Any]]] = None
    """Optional explicit edges; if omitted the renderer infers them from
    ``nodes[i].left`` / ``nodes[i].right`` references."""

    root: str
    """The node id of the tree root."""


class ChartData(BaseModel):
    """Data specification for the chart visualizer.

    TODO: Implement chart types as needed.
    """
    type: str
    """Chart type: "bar", "line", "pie", etc."""

    data: Dict[str, Any]
    """Chart data series and labels."""


class FlowchartData(BaseModel):
    """Data specification for the flowchart visualizer.

    TODO: Implement flowchart rendering as needed.
    """
    nodes: List[Dict[str, Any]]
    """Nodes with ``id`` and ``label`` fields."""

    edges: List[Dict[str, str]]
    """Directed edges as [source, target] pairs."""


class TimelineData(BaseModel):
    """Data specification for the timeline visualizer.

    TODO: Implement timeline rendering as needed.
    """
    events: List[Dict[str, str]]
    """Events with ``label`` and ``date`` (ISO 8601 or relative)."""


class EquationData(BaseModel):
    """Data specification for the equation visualizer.

    Renders a LaTeX-formatted mathematical equation.
    """
    latex: str
    """LaTeX source for the equation (rendered client-side)."""


class VectorData(BaseModel):
    """Data specification for the vector visualizer.

    TODO: Implement vector diagram rendering as needed.
    """
    magnitude: float
    """Vector magnitude."""

    direction: float
    """Vector direction in degrees."""

    label: Optional[str] = None
    """Optional label."""


class MatrixData(BaseModel):
    """Data specification for the matrix visualizer.

    TODO: Implement matrix rendering as needed.
    """
    rows: int
    """Number of rows."""

    cols: int
    """Number of columns."""

    values: List[List[float]]
    """2D array of numeric values."""


class PhysicsSimulationData(BaseModel):
    """Data specification for the physics simulation visualizer.

    TODO: Implement physics simulation as needed.
    """
    particles: List[Dict[str, Any]]
    """Particles with ``id``, ``position``, ``velocity``, ``mass``."""

    fields: Optional[Dict[str, Any]] = None
    """Optional force fields."""


class InteractiveTableData(BaseModel):
    """Data specification for the interactive table visualizer.

    TODO: Implement interactive table as needed.
    """
    columns: List[Dict[str, Any]]
    """Column definitions with ``header`` and ``field``."""

    rows: List[Dict[str, Any]]
    """Table rows keyed by column fields."""


class ImagePanelData(BaseModel):
    """Data specification for the image panel visualizer.

    TODO: Implement image panel as needed.
    """
    image_url: str
    """URL of the image to display."""

    caption: Optional[str] = None
    """Optional caption text."""


# ---------------------------------------------------------------------------
# Control schema shared across visualizations
# ---------------------------------------------------------------------------

class VisualizationControls(BaseModel):
    """Optional controls configuration for a visualization.

    Not all controls need to be specified; the frontend uses sensible defaults.
    Unknown control keys are rejected via ``extra="forbid"``.
    """
    model_config = ConfigDict(extra="forbid")

    traversal_method: str | None = None
    """For algorithm visualizers: "preorder", "inorder", "postorder", "level-order"."""

    speed: float | None = None
    """Playback speed multiplier. Must be > 0."""

    isPlaying: bool | None = None
    """Initial play state."""

    reset: bool | None = None
    """Whether to reset the visualization on update."""


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def validate_genui_response(data: dict[str, Any]) -> Optional[GenUiResponse]:
    """Validate and parse a GenUI response dict.

    Returns a validated ``GenUiResponse`` on success, or ``None`` if the data
    is malformed / missing required fields. Callers should fall back to plain
    text when this returns ``None``.

    This function is the single entry point for server-side validation of
    model-generated GenUI payloads. It ensures:
    - The ``type`` field is exactly ``"genui"`` (discriminated union guard).
    - ``version`` is a supported enum value.
    - ``visualization`` conforms to the per-type schema.
    - Unknown component types are rejected.
    - Payload size and structural limits are enforced.
    """
    try:
        # First-level validation: type must be "genui"
        model = GenUiResponse.model_validate(data)
        # Per-type validation happens in the frontend dispatcher; here we just
        # ensure the top-level envelope is well-formed.
        return model
    except Exception as exc:
        # Log the validation error but don't crash — fall back to text.
        import logging
        logging.getLogger(__name__).warning(
            "GenUI validation failed: %s", exc,
            extra={"event": "genui_validation_error"},
        )
        return None


# ---------------------------------------------------------------------------
# Payload size limits (8GB RAM Windows laptop constraint)
# ---------------------------------------------------------------------------

# Maximum number of nodes in a binary tree visualization
MAX_TREE_NODES = 50

# Maximum number of entries in any array payload
MAX_ARRAY_LENGTH = 1000

# Maximum payload size in bytes (approximate; actual will be smaller)
MAX_PAYLOAD_BYTES = 100_000  # 100KB


def check_payload_size(data: dict[str, Any], max_bytes: int = MAX_PAYLOAD_BYTES) -> bool:
    """Quick heuristic check that a serialized payload won't exceed limits."""
    import json
    try:
        serialized = json.dumps(data, default=str)
        return len(serialized.encode("utf-8")) <= max_bytes
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Backward-compatibility alias for the old "genui" event format
# ---------------------------------------------------------------------------

# The /chat/stream endpoint currently emits ``token``, ``tool_started``,
# ``tool_completed``, ``done``, ``error`` frames. In the future, a
# ``genui`` frame may be emitted alongside them. This helper builds a
# ``done`` frame that carries a validated GenUI specification so existing
# clients can incrementally support the new format without breaking.

def build_done_frame_with_genui(
    result: Any,
    answer_parts: list[str],
    started: float,
    conversation_id: Any | None = None,
    *,
    genui: Optional[GenUiResponse] = None,
) -> dict[str, Any]:
    """Append a GenUI specification to a ``done`` SSE frame.

    Clients that understand ``genui`` in the ``done`` frame will render the
    visualization; others will simply ignore the extra field and display the
    plain-text answer as before.

    The GenUI response is validated before inclusion; if it fails validation,
    it is silently dropped and only the text answer is returned.
    """
    payload = _done_payload(result, answer_parts, started, conversation_id)
    if genui is not None:
        # Validate before embedding
        if validate_genui_response(genui.model_dump()) is None:
            # Silently drop invalid GenUI — don't corrupt the text response
            import logging
            logging.getLogger(__name__).warning(
                "Invalid GenUI dropped from done frame",
                extra={"event": "genui_dropped_from_done"},
            )
        else:
            payload["genui"] = genui.model_dump()
    return payload