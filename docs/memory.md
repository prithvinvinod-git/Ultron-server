# Memory

Memory gives agents continuity: a conversation buffer, durable facts, past
episodes, semantic recall, and per-project knowledge — each with its own
lifetime and access rule, and never mixed across unrelated projects.

Code: `server/app/memory/`.

## Layers

| Layer | Module | Purpose |
|---|---|---|
| `SHORT_TERM` | `short_term.py` | The current conversation buffer; bounded and TTL-expiring. |
| `LONG_TERM` | `long_term.py` | Durable facts worth keeping. |
| `EPISODIC` | `episodic.py` | Past episodes/events, retention-limited. |
| `SEMANTIC` | `semantic.py` | Similarity recall over embeddings. |
| `PROJECT` | `project.py` | Knowledge scoped to a project. |

`MemoryRecord`, `MemoryMessage` and `RetrievalQuery` (`types.py`) are the shared
shapes. A memory can be both long-term and semantic; it is stored once and
reached through whichever layer the query needs. Semantic ranking uses `text`;
the keyword layers use their own importance ordering.

## Namespaces

Memory **must** be namespaced (§22), so unrelated projects never contaminate
each other. Namespace rules live in `namespaces.py`:

| Kind | Meaning |
|---|---|
| `user` | Personal scope; the one namespace with no slug. |
| `project` | e.g. `project:<slug>`. |
| `agent` | e.g. `agent:<slug>`. |

A namespace is parsed into `(kind, slug)` and backed by indexed
`namespace_kind` / `namespace_slug` columns. Malformed namespaces are rejected,
not treated as opaque strings.

## Retrieval

`RetrievalQuery` carries the request; `MemoryManager` (`manager.py`) selects the
layers to search and merges results:

- Semantic search ranks by embedding similarity, filtered by
  `MEMORY_MIN_SIMILARITY` and limited to `MEMORY_RETRIEVAL_TOP_K`.
- Keyword layers return their most important entries.

### Embeddings

`embeddings.py` produces vectors. The provider is `EMBEDDING_PROVIDER`
(`ollama` \| `openai` \| `none`), with `EMBEDDING_MODEL` and
`EMBEDDING_DIMENSIONS`.

**pgvector is optional.** With `ENABLE_PGVECTOR=false` (the safe default),
`memories.embedding` is a JSONB column and semantic search falls back to the
keyword path. Set it `true` only where the extension is installed. A
deterministic hash fallback (`EMBEDDING_ALLOW_HASH_FALLBACK`) exists for local
tests only, never production.

## Recording

`recorder.py` subscribes to the event stream and records memories from agent and
task activity, so memory is populated by real work rather than by explicit
calls alone. `MemoryRecorder` is started in `Container.startup()` after the
persistence handler.

## Configuration

| Variable | Default | Effect |
|---|---|---|
| `MEMORY_ENABLED` | `true` | Enable memory. |
| `MEMORY_SHORT_TERM_MAX_MESSAGES` | `40` | Short-term buffer size. |
| `MEMORY_SHORT_TERM_TTL` | `86400` | Short-term TTL (seconds). |
| `MEMORY_RETRIEVAL_TOP_K` | `8` | Retrieval top-k. |
| `MEMORY_MIN_SIMILARITY` | `0.25` | Minimum semantic similarity. |
| `MEMORY_EPISODIC_RETENTION_DAYS` | `180` | Episodic retention. |
| `MEMORY_LONG_TERM_ENABLED` | `true` | Enable long-term layer. |
| `MEMORY_SEMANTIC_ENABLED` | `true` | Enable semantic layer. |
| `MEMORY_DEFAULT_NAMESPACE` | `user` | Default personal namespace. |
| `EMBEDDING_PROVIDER` | `ollama` | `ollama` \| `openai` \| `none`. |
| `EMBEDDING_MODEL`, `EMBEDDING_DIMENSIONS`, `EMBEDDING_TIMEOUT` | — | Embedding settings. |
| `EMBEDDING_ALLOW_HASH_FALLBACK` | `false` | Test-only offline fallback. |
| `ENABLE_PGVECTOR` | `false` | Use pgvector for embeddings. |

See [`configuration.md`](configuration.md) for the full list.

## Hard rules

1. Every memory belongs to a namespace; malformed namespaces are rejected.
2. Unrelated projects are never mixed.
3. PostgreSQL is authoritative; Redis is not a memory store.
4. Missing embeddings degrade to keyword recall rather than fabricating a vector.
