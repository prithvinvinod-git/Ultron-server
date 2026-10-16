"""Embedding provider abstraction (T127, spec §22 / §66.13).

Semantic memory needs a vector for each memory, and *where that vector comes
from* is a deployment choice, not a property of the memory system. This module
is the seam: the memory layers depend on :class:`EmbeddingProvider` and never on
a specific service. §66.13 keeps the whole system on one vector store
(``memories.embedding``) and one provider abstraction; this is that abstraction.

**``EMBEDDING_PROVIDER=none`` is a valid deployment, and it degrades.** T127's
revision is explicit: do not assume a local embedding provider exists. A machine
with no Ollama, no OpenAI key, and no GPU still boots, still has working
long-term and episodic memory, and reports semantic retrieval as *unavailable*
rather than failing to start or -- worse -- returning plausible-looking
neighbours from a fake vector. :class:`NullEmbeddingProvider` is that path: it
answers ``available == False`` and raises
:class:`~app.core.errors.DependencyUnavailableError` from :meth:`embed`.

**A hash fallback exists, and it announces itself.** When
``EMBEDDING_ALLOW_HASH_FALLBACK`` is set, :class:`HashEmbeddingProvider` gives a
deterministic vector derived from the text, so retrieval can be exercised and a
deployment can run end-to-end without a real provider. It is **not** a semantic
model -- lexically similar text lands near itself and nothing else does -- so
:attr:`HashEmbeddingProvider.is_fallback` is ``True`` and callers that care about
retrieval quality must check it. The alternative, silently substituting hashes
for real embeddings, would make an evaluation report look like retrieval works
when it does not.

**Construction never connects.** Like every other subsystem here, a provider is
built from settings and only talks to its service on the first :meth:`embed`.
That keeps the memory layer importable and testable with no network, and it means
an Ollama that is down is discovered at the first retrieval, where it can be
reported, rather than at process start, where it would take the server down.
"""

from __future__ import annotations

import hashlib
import math
from abc import ABC, abstractmethod
from collections.abc import Sequence

import httpx

from app.config import EmbeddingSettings
from app.core.errors import DependencyUnavailableError
from app.observability import get_logger

_LOGGER = get_logger(__name__)


class EmbeddingProvider(ABC):
    """Turns text into a fixed-width vector.

    Implementations are cheap to construct and open no connections until first
    use. The width is fixed by the deployment (``EMBEDDING_DIMENSIONS``) because
    it is a column type on ``memories.embedding``; a provider that returns a
    different width is rejected at the boundary rather than stored.
    """

    #: Short name for logs and audit rows (``"ollama"``, ``"hash"``, ``"none"``).
    name: str = "unnamed"

    #: Vector width every :meth:`embed` result must match.
    dimensions: int = 768

    @property
    def available(self) -> bool:
        """Whether this provider can produce embeddings at all.

        ``False`` is a supported state, not an error. Callers that would raise a
        :class:`~app.core.errors.DependencyUnavailableError` on a missing
        provider can ask first and take the honest degraded path instead.
        """
        return True

    @property
    def is_fallback(self) -> bool:
        """Whether the vectors are a non-semantic stand-in (see the module docstring)."""
        return False

    @abstractmethod
    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one vector per input, each of length :attr:`dimensions`."""

    async def embed_one(self, text: str) -> list[float]:
        """Embed a single string, returning one vector."""
        vectors = await self.embed([text])
        return vectors[0]

    async def aclose(self) -> None:
        """Release any held connections. Safe to call more than once."""
        return None


class NullEmbeddingProvider(EmbeddingProvider):
    """The honest "no embeddings here" provider.

    Chosen when ``EMBEDDING_PROVIDER=none`` or when a provider is selected but
    not configured (no model, no key). Every call raises
    :class:`~app.core.errors.DependencyUnavailableError`, so an accidental
    dependency on semantic retrieval surfaces as a clear 503 rather than as an
    empty result a caller might mistake for "no matches".
    """

    name = "none"

    def __init__(self, dimensions: int = 768, *, reason: str = "") -> None:
        self.dimensions = dimensions
        self._reason = reason or "no embedding provider is configured"

    @property
    def available(self) -> bool:
        return False

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise DependencyUnavailableError("embeddings", reason=self._reason)


class HashEmbeddingProvider(EmbeddingProvider):
    """A deterministic, non-semantic fallback (see the module docstring).

    Each whitespace token is hashed to a bucket and a sign, and the resulting
    counts are L2-normalised. Identical text always yields the identical vector,
    so persistence and retrieval round-trip, and lexically overlapping text
    scores higher -- which is enough to test the pipeline and *not* enough to be
    mistaken for a semantic model.
    """

    name = "hash"

    def __init__(self, dimensions: int = 768) -> None:
        self.dimensions = dimensions

    @property
    def is_fallback(self) -> bool:
        return True

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        tokens = [token for token in text.lower().split() if token]
        for token in tokens:
            digest = hashlib.sha1(token.encode("utf-8"), usedforsecurity=False).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        if norm > 0.0:
            vector = [value / norm for value in vector]
        return vector

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]


class OllamaEmbeddingProvider(EmbeddingProvider):
    """Embeddings from an Ollama server (local by default, hostable).

    Speaks the same ``/api/embeddings`` endpoint whether Ollama runs on
    localhost or behind a URL, so a local and a hosted deployment differ by
    configuration only. A connection failure becomes
    :class:`~app.core.errors.DependencyUnavailableError`, which the memory layer
    can report without taking down the request that triggered it.
    """

    name = "ollama"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        dimensions: int,
        timeout: int = 30,
        auth_header: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout
        self._auth_header = auth_header
        self.dimensions = dimensions
        self._client = client

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self._auth_header:
            headers["Authorization"] = self._auth_header
        return headers

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        async with self._client_scope() as client:
            for text in texts:
                vectors.append(await self._embed_one(client, text))
        return vectors

    def _client_scope(self) -> _ClientScope:
        return _ClientScope(self._client, self._timeout)

    async def _embed_one(self, client: httpx.AsyncClient, text: str) -> list[float]:
        payload = {"model": self._model, "prompt": text}
        try:
            response = await client.post(
                f"{self._base_url}/api/embeddings", json=payload, headers=self._headers()
            )
            response.raise_for_status()
            embedding = response.json().get("embedding")
        except httpx.HTTPError as error:
            raise DependencyUnavailableError(
                "embeddings", reason="ollama does not answer", cause=error
            ) from error
        return self._validate(embedding)

    def _validate(self, embedding: object) -> list[float]:
        if not isinstance(embedding, list) or not embedding:
            raise DependencyUnavailableError(
                "embeddings", reason="ollama returned no vector"
            )
        values = [float(value) for value in embedding]
        if len(values) != self.dimensions:
            raise DependencyUnavailableError(
                "embeddings",
                reason=f"ollama returned {len(values)} dimensions, expected {self.dimensions}",
            )
        return values


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """Embeddings from an OpenAI-compatible ``/embeddings`` endpoint."""

    name = "openai"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        dimensions: int,
        timeout: int = 30,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self.dimensions = dimensions
        self._client = client

    def _headers(self) -> dict[str, str]:
        return {"Accept": "application/json", "Authorization": f"Bearer {self._api_key}"}

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        payload: dict[str, object] = {"model": self._model, "input": list(texts)}
        client_scope = _ClientScope(self._client, self._timeout)
        try:
            async with client_scope as client:
                response = await client.post(
                    f"{self._base_url}/embeddings", json=payload, headers=self._headers()
                )
                response.raise_for_status()
                data = response.json().get("data")
        except httpx.HTTPError as error:
            raise DependencyUnavailableError(
                "embeddings", reason="openai embedding request failed", cause=error
            ) from error
        return self._parse(data, expected=len(texts))

    def _parse(self, data: object, *, expected: int) -> list[list[float]]:
        if not isinstance(data, list):
            raise DependencyUnavailableError("embeddings", reason="openai returned no data")
        rows = [row for row in data if isinstance(row, dict)]
        ordered = sorted(rows, key=lambda row: int(row.get("index", 0)))
        vectors = [self._validate(row.get("embedding")) for row in ordered]
        if len(vectors) != expected:
            raise DependencyUnavailableError(
                "embeddings",
                reason=f"openai returned {len(vectors)} vectors for {expected} inputs",
            )
        return vectors

    def _validate(self, embedding: object) -> list[float]:
        if not isinstance(embedding, list) or not embedding:
            raise DependencyUnavailableError("embeddings", reason="openai returned an empty vector")
        values = [float(value) for value in embedding]
        if len(values) != self.dimensions:
            raise DependencyUnavailableError(
                "embeddings",
                reason=f"openai returned {len(values)} dimensions, expected {self.dimensions}",
            )
        return values


class _ClientScope:
    """An async context manager that owns an injected client or opens its own.

    Shared by the HTTP providers so an injected test client is never closed by
    the provider, while a provider-built client *is* -- otherwise each embedding
    call would leak a connection pool.
    """

    def __init__(self, client: httpx.AsyncClient | None, timeout: int) -> None:
        self._client = client
        self._timeout = timeout
        self._owned: httpx.AsyncClient | None = None

    async def __aenter__(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        self._owned = httpx.AsyncClient(timeout=float(self._timeout))
        return self._owned

    async def __aexit__(self, *_exc: object) -> None:
        if self._owned is not None:
            await self._owned.aclose()
            self._owned = None


def create_embedding_provider(
    embeddings: EmbeddingSettings,
    *,
    ollama_base_url: str = "http://localhost:11434",
    ollama_auth_header: str | None = None,
    openai_base_url: str = "https://api.openai.com/v1",
    openai_api_key: str = "",
) -> EmbeddingProvider:
    """Build the provider ``embeddings`` selects, degrading honestly.

    ``provider="none"`` returns :class:`NullEmbeddingProvider`. A provider that
    is selected but not configured (no model for Ollama, no key for OpenAI)
    *also* returns the null provider, with a reason naming what is missing --
    because a half-configured provider is not available, and pretending
    otherwise would fail later with a less useful message. The hash fallback is
    used only when explicitly allowed *and* no real provider is available.
    """
    provider = embeddings.provider
    if provider == "ollama" and embeddings.model:
        return OllamaEmbeddingProvider(
            base_url=ollama_base_url,
            model=embeddings.model,
            dimensions=embeddings.dimensions,
            timeout=embeddings.timeout,
            auth_header=ollama_auth_header,
        )
    if provider == "openai" and openai_api_key:
        return OpenAIEmbeddingProvider(
            base_url=openai_base_url,
            api_key=openai_api_key,
            model=embeddings.model or "text-embedding-3-small",
            dimensions=embeddings.dimensions,
            timeout=embeddings.timeout,
        )
    if embeddings.allow_hash_fallback:
        _LOGGER.warning(
            "using the non-semantic hash embedding fallback",
            extra={"event": "memory.embeddings_hash_fallback", "provider": provider},
        )
        return HashEmbeddingProvider(embeddings.dimensions)
    reason = _unavailable_reason(provider, embeddings.model)
    _LOGGER.info(
        "semantic memory is unavailable",
        extra={"event": "memory.embeddings_unavailable", "provider": provider, "reason": reason},
    )
    return NullEmbeddingProvider(embeddings.dimensions, reason=reason)


def _unavailable_reason(provider: str, model: str) -> str:
    """Describe why no real provider was built, for the error and the log."""
    if provider == "none":
        return "EMBEDDING_PROVIDER=none"
    if provider == "ollama":
        return "Ollama embeddings are selected but EMBEDDING_MODEL is empty"
    if provider == "openai":
        return "OpenAI embeddings are selected but OPENAI_API_KEY is empty"
    return f"unknown embedding provider {provider!r}"


__all__ = [
    "EmbeddingProvider",
    "HashEmbeddingProvider",
    "NullEmbeddingProvider",
    "OllamaEmbeddingProvider",
    "OpenAIEmbeddingProvider",
    "create_embedding_provider",
]
