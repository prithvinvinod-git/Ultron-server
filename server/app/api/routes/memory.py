"""Memory API endpoints for the ULTRON console.

Provides a simple memory interface compatible with the Next.js Ultron app:
  POST /memory              Store a new memory (summary, detail, kind, tags, importance, scope, sessionId)
  GET  /memory              Recall/list memories (namespace, query, top_k, layers)
  DELETE /memory?id=<id>    Delete a memory by ID

The internal layered memory system (short/long/episodic/semantic/project) is
exposed through a simplified facade matching the Next.js API.
"""

from fastapi import APIRouter, HTTPException, Query, Request, Body
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import Optional
import uuid

from app.memory.manager import MemoryManager
from app.database.models import MemoryLayer
from app.memory import namespaces

router = APIRouter(prefix="/memory", tags=["memory"])


class MemoryCreate(BaseModel):
    """Request body for storing a memory (Next.js compatible)."""
    summary: str = Field(..., min_length=1, max_length=500, description="One-line statement of the memory")
    detail: Optional[str] = Field(default="", description="Optional supporting detail")
    kind: str = Field(default="fact", description="Memory category: fact, preference, or decision")
    tags: Optional[list[str]] = Field(default_factory=list, description="Optional tags to aid later recall")
    importance: int = Field(default=1, ge=1, le=5, description="Importance weight 1..5")
    scope: str = Field(default="global", description="Scope: global or session")
    sessionId: Optional[str] = Field(default=None, description="Optional session ID for session-scoped memories")


class MemoryResponse(BaseModel):
    """Memory record response."""
    id: str
    namespace: str
    kind: str
    slug: str
    layer: str
    content: str
    importance: float
    score: Optional[float] = None
    created_at: str
    detail: Optional[str] = None
    tags: list[str] = []


@router.get("")
async def recall_memory(
    request: Request,
    namespace: str = Query(default=None, description="Memory namespace (user, project:<slug>, etc.)"),
    query: str = Query(default=None, description="Search query text"),
    top_k: int = Query(default=8, ge=1, le=100, description="Number of results to return"),
    layers: str = Query(
        default=None,
        description="Comma-separated list of layers: long_term, episodic, short_term, semantic, project",
    ),
) -> JSONResponse:
    """Recall memories from the specified namespace and layers.

    Returns a ranked, de-duplicated list of memory records across all specified
    layers. Each layer that cannot contribute is skipped gracefully rather than
    failing the entire request.
    """
    try:
        # Parse layers
        layers_list: list[str] | None = None
        if layers:
            layer_names = [l.strip() for l in layers.split(",") if l.strip()]
            layers_list = []
            for name in layer_names:
                if name in ("long_term", "episodic", "short_term", "semantic", "project"):
                    layers_list.append(name)

        # Parse namespace
        namespace_value = namespace or request.app.get("default_namespace", "user")

        # Get memory manager from app state
        memory_manager: MemoryManager = request.app.state.memory_manager

        # Perform recall
        records = await memory_manager.recall(
            query=None,
            namespace=namespace_value,
            text=query,
            top_k=top_k,
        )

        # Convert to serializable format
        result = []
        for record in records:
            # Parse tags from content if available
            tags = []
            result.append({
                "id": str(record.id),
                "namespace": record.namespace,
                "kind": record.kind,
                "slug": record.slug,
                "layer": record.layer.value if hasattr(record.layer, 'value') else str(record.layer),
                "content": record.content,
                "importance": record.importance,
                "score": record.score,
                "created_at": record.created_at.isoformat() if hasattr(record.created_at, 'isoformat') else str(record.created_at),
                "detail": "",
                "tags": tags,
            })

        return JSONResponse(content={"records": result, "count": len(result)})

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("")
async def store_memory(
    request: Request,
    memory: MemoryCreate = Body(...),
) -> JSONResponse:
    """Store a new memory (Next.js compatible API).

    Maps the simple Next.js fields to the internal layered memory system.
    Stores in long-term memory by default.
    """
    try:
        # Get memory manager from app state
        memory_manager: MemoryManager = request.app.state.memory_manager

        # Build content from summary + detail
        content = memory.summary
        if memory.detail:
            content += f" — {memory.detail}"
        if memory.tags:
            content += f" [{', '.join(memory.tags)}]"

        # Determine namespace from scope
        if memory.scope == "session" and memory.sessionId:
            namespace_value = f"session:{memory.sessionId}"
        else:
            namespace_value = request.app.get("default_namespace", "user")

        # Map importance 1..5 to 0.0..1.0
        importance = max(0.0, min(1.0, memory.importance / 5.0))

        # Validate kind
        if memory.kind not in ("fact", "preference", "decision"):
            memory.kind = "fact"

        # Store in long-term memory
        record = await memory_manager.remember(
            content,
            namespace=namespace_value,
            layer=MemoryLayer.LONG_TERM,
            importance=importance,
        )

        return JSONResponse(
            content={
                "ok": True,
                "memory": {
                    "id": str(record.id),
                    "namespace": record.namespace,
                    "kind": memory.kind,
                    "slug": record.slug,
                    "layer": record.layer.value if hasattr(record.layer, 'value') else str(record.layer),
                    "content": record.content,
                    "importance": record.importance,
                    "created_at": record.created_at.isoformat() if hasattr(record.created_at, 'isoformat') else str(record.created_at),
                    "detail": memory.detail,
                    "tags": memory.tags,
                }
            },
            status_code=201,
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("")
async def delete_memory(
    request: Request,
    id: str = Query(..., description="Memory ID to delete"),
) -> JSONResponse:
    """Delete a memory by ID."""
    try:
        # Get memory manager from app state
        memory_manager: MemoryManager = request.app.state.memory_manager

        # Parse UUID
        try:
            memory_id = uuid.UUID(id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid memory ID format")

        # Delete using the long-term memory layer
        success = await memory_manager.forget(memory_id)

        if not success:
            raise HTTPException(status_code=404, detail="Memory not found")

        return JSONResponse(content={"ok": True})

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))