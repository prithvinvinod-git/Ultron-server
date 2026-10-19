# Configuration

ULTRON is configured entirely through environment variables. Nothing is
hard-coded: no API keys, no model names, no Windows paths. The same checkout runs
on Windows for development and Ubuntu Server for production (spec §36).

Copy the template and edit it:

```bash
cp .env.example .env
```

`.env` is read at startup by `app/config/settings.py`. **Never commit `.env`**
(spec §31). The tables below mirror `.env.example`; if the two ever disagree,
`.env.example` is authoritative.

Generate a secret:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

---

## Core application

| Variable | Default | Description |
|---|---|---|
| `ENVIRONMENT` | `development` | `development` \| `staging` \| `production`. |
| `LOG_LEVEL` | `INFO` | `DEBUG` \| `INFO` \| `WARNING` \| `ERROR` \| `CRITICAL`. |
| `LOG_FORMAT` | `json` | `json` \| `console`. |
| `LOG_FILE` | _(empty)_ | Optional log path; empty means stdout only. |
| `APP_NAME` | `ULTRON` | Service name in logs and responses. |
| `API_HOST` | `0.0.0.0` | Bind address. Use `127.0.0.1` behind a proxy. |
| `API_PORT` | `8000` | Listen port. |
| `API_WORKERS` | `1` | Worker processes; `>1` requires Redis coordination (spec §24). |
| `API_RELOAD` | `false` | Auto-reload (development only). |
| `API_ROOT_PATH` | _(empty)_ | Set when served behind a reverse-proxy subpath. |
| `DEBUG` | `false` | Verbose behaviour; never `true` in production. |
| `CORS_ORIGINS` | _(empty)_ | Comma-separated trusted origins. Empty disables CORS. A bare `*` disables credentialed requests. |
| `ALLOWED_HOSTS` | _(unset)_ | Comma-separated `Host` header allow-list. Empty means not checked; set in production. |
| `SHUTDOWN_TIMEOUT` | `30` | Seconds to wait for graceful shutdown before forcing exit. |

## Authentication / security

| Variable | Default | Description |
|---|---|---|
| `JWT_SECRET` | `CHANGE_ME…` | Signing secret. **Must** be generated. |
| `JWT_ALGORITHM` | `HS256` | JWT algorithm. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | Access-token lifetime. |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `30` | Refresh-token lifetime. |
| `ADMIN_USERNAME` | `admin` | Bootstrap administrator, created on first migration if no user exists. |
| `ADMIN_PASSWORD` | `CHANGE_ME` | Bootstrap administrator password. **Must** be changed. |
| `ARGON2_TIME_COST` | `3` | Argon2 time cost. |
| `ARGON2_MEMORY_COST` | `65536` | Argon2 memory cost (KiB). |
| `ARGON2_PARALLELISM` | `4` | Argon2 parallelism. |
| `ALLOW_ANONYMOUS` | `false` | Allow REST/WebSocket without a token. Development only; must be `false` in production. |
| `RATE_LIMIT_ENABLED` | `true` | Enable per-principal rate limiting. |
| `RATE_LIMIT_REQUESTS` | `120` | Requests per minute per principal. |

## PostgreSQL (authoritative store, spec §23/§24)

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://…` | Async SQLAlchemy URL. |
| `DATABASE_POOL_SIZE` | `10` | Connection pool size. |
| `DATABASE_MAX_OVERFLOW` | `20` | Extra connections allowed beyond the pool. |
| `DATABASE_POOL_TIMEOUT` | `30` | Seconds to wait for a pool connection. |
| `DATABASE_ECHO` | `false` | Log SQL statements. |
| `DATABASE_AUTO_MIGRATE` | `false` | Run Alembic on startup. Keep `false` in production; migrate explicitly. |
| `ENABLE_PGVECTOR` | `false` | Enable the `pgvector` extension during migrations. See the note below. |

> `ENABLE_PGVECTOR=false` is the safe default: the stock PostgreSQL Windows build
> does not ship `pgvector`, and the resource policy excludes vector stores
> (spec §59). With it `false`, `memories.embedding` is a JSONB column and
> semantic search falls back to the keyword path. Set it `true` only where the
> extension is actually installed — a mismatch fails the migration after the
> schema is half-created.

## Redis (temporary state, spec §24)

| Variable | Default | Description |
|---|---|---|
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL. |
| `REDIS_MAX_CONNECTIONS` | `50` | Max pooled connections. |
| `REDIS_SOCKET_TIMEOUT` | `5` | Socket timeout, seconds. |
| `REDIS_DEFAULT_TTL` | `3600` | Default TTL for short-lived keys, seconds. |
| `REDIS_EVENT_BRIDGE` | `false` | Mirror bus events to Redis pub/sub for multi-process fan-out. |

## Ollama (model provider, spec §21)

| Variable | Default | Description |
|---|---|---|
| `OLLAMA_URL` | `http://localhost:11434` | Ollama base URL. A hosted instance speaks the same API. |
| `OLLAMA_API_KEY` | _(empty)_ | Bearer token for a hosted Ollama; empty for a local one. |
| `OLLAMA_TIMEOUT` | `120` | Generation timeout, seconds. |
| `OLLAMA_CONNECT_TIMEOUT` | `10` | Health-probe budget, seconds. |
| `OLLAMA_KEEPALIVE` | `5m` | How long a model stays loaded. |
| `OLLAMA_NUM_CTX` | `4096` | Context window. |
| `OLLAMA_MODELS` | _(empty)_ | Offer only these models to the router; empty discovers all. |
| `OLLAMA_EMBEDDING_MODEL` | _(empty)_ | Embedding model for semantic memory. |
| `OLLAMA_HEALTHCHECK` | `true` | Report Ollama health through `/health` and `/ready`. |

## Cloud model providers (optional)

Leave blank to disable a provider. The server boots with all of these empty and
must function with only Ollama configured.

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | _(empty)_ | OpenAI API key. |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | OpenAI base URL. |
| `OPENAI_TIMEOUT` | `60` | Timeout, seconds. |
| `GEMINI_API_KEY` | _(empty)_ | Gemini API key. |
| `GEMINI_BASE_URL` | `https://generativelanguage.googleapis.com` | Gemini base URL. |
| `GEMINI_TIMEOUT` | `60` | Timeout, seconds. |
| `ANTHROPIC_API_KEY` | _(empty)_ | Anthropic API key. |
| `ANTHROPIC_BASE_URL` | `https://api.anthropic.com` | Anthropic base URL. |
| `ANTHROPIC_TIMEOUT` | `60` | Timeout, seconds. |

## Model router (spec §20/§49)

Format: `<provider>:<model>` (e.g. `ollama:qwen2.5-coder:7b`).

| Variable | Default | Description |
|---|---|---|
| `MODEL_DEFAULT` | `ollama:qwen2.5:7b-instruct` | Default model. |
| `MODEL_CODING` | _(empty)_ | Model for coding tasks. |
| `MODEL_RESEARCH` | _(empty)_ | Model for research tasks. |
| `MODEL_VISION` | _(empty)_ | Model for vision tasks. |
| `MODEL_FAST` | _(empty)_ | Model for low-latency tasks. |
| `MODEL_REASONING` | _(empty)_ | Model for reasoning-heavy tasks. |
| `MODEL_FALLBACK_CHAIN` | _(empty)_ | Ordered fallbacks when the selected provider is unavailable. |
| `MODEL_MAX_OUTPUT_TOKENS` | `4096` | Max output tokens. |
| `MODEL_TEMPERATURE` | `0.7` | Sampling temperature. |
| `MODEL_REQUEST_TIMEOUT` | `180` | Per-request timeout, seconds. |
| `MODEL_MAX_RETRIES` | `2` | Retries for transient provider errors. |
| `MODEL_RETRY_BACKOFF` | `2.0` | Backoff multiplier. |
| `MODEL_USAGE_TRACKING` | `true` | Record token usage and latency. |
| `MODEL_HEALTH_REFRESH` | `60` | Ollama availability re-probe interval, seconds. |

## Embeddings / semantic memory

| Variable | Default | Description |
|---|---|---|
| `EMBEDDING_PROVIDER` | `ollama` | `ollama` \| `openai` \| `none`. |
| `EMBEDDING_MODEL` | _(empty)_ | Embedding model. |
| `EMBEDDING_DIMENSIONS` | `768` | Vector dimensions. |
| `EMBEDDING_TIMEOUT` | `30` | Timeout, seconds. |
| `EMBEDDING_ALLOW_HASH_FALLBACK` | `false` | Deterministic offline fallback for tests only, never production. |

## OpenCode coding engine (spec §8/§9)

| Variable | Default | Description |
|---|---|---|
| `OPENCODE_PATH` | `opencode` | CLI path or a name resolvable on `PATH`. |
| `OPENCODE_MODEL` | _(empty)_ | `provider/model` passed to the CLI. |
| `OPENCODE_PROVIDER` | _(empty)_ | Provider passed to the CLI. |
| `OPENCODE_TIMEOUT` | `1800` | Timeout, seconds. |
| `OPENCODE_MAX_OUTPUT_LINES` | `5000` | Cap on captured output lines. |
| `OPENCODE_EXTRA_ARGS` | _(empty)_ | Arguments always passed to the CLI. |
| `OPENCODE_WORKSPACE_TEMPLATE` | _(empty)_ | Working-directory template; placeholder `{workspace}`. |
| `OPENCODE_GIT_ISOLATION` | `true` | Use a dedicated git worktree/branch for autonomous edits. |
| `OPENCODE_GIT_BRANCH_PREFIX` | `ultron` | Branch prefix for isolated worktrees. |

## Workspaces (spec §46)

| Variable | Default | Description |
|---|---|---|
| `WORKSPACES_ROOT` | `./workspaces` | Root holding registered project checkouts. |
| `WORKSPACES_ALLOWED_ROOTS` | _(empty)_ | Refuse filesystem/git operations outside these roots. |
| `WORKSPACES_CLEANUP_ON_TASK_END` | `false` | Remove the workspace when a task ends. |
| `WORKSPACES_MAX_SIZE_MB` | `5120` | Size cap per workspace. |

## Agents (spec §6/§7/§41)

| Variable | Default | Description |
|---|---|---|
| `AGENT_DEFAULT_TIMEOUT` | `900` | Default agent timeout, seconds. |
| `AGENT_MAX_CONCURRENCY` | `4` | Resource-aware scheduling cap (spec §48). |
| `AGENT_MAX_PER_TYPE` | `8` | Max agents of one type. |
| `AGENT_HEARTBEAT_INTERVAL` | `15` | Heartbeat interval, seconds. |
| `AGENT_DEFAULT_PERMISSION_LEVEL` | `1` | Default ceiling for new agents. `0`=read `1`=safe `2`=project files `3`=execute `4`=system config `5`=destructive. |
| `AGENT_CONFIRMATION_LEVEL` | `4` | Permission level requiring explicit human confirmation. |
| `AGENT_MAX_LOG_LINES` | `2000` | Cap on agent log output. |
| `AGENT_MEMORY_NAMESPACE` | `agent` | Default memory namespace prefix. |

## Tasks (spec §18)

| Variable | Default | Description |
|---|---|---|
| `TASK_DEFAULT_TIMEOUT` | `1800` | Default task timeout, seconds. |
| `TASK_MAX_PARALLEL` | `8` | Max parallel tasks. |
| `TASK_MAX_RETRIES` | `1` | Retry budget. |
| `TASK_PERSIST` | `true` | Persist tasks to PostgreSQL. |
| `TASK_RECOVER_ON_STARTUP` | `true` | Recover unfinished tasks after a restart. |
| `TASK_MAX_STEPS` | `100` | Max steps per task. |
| `TASK_GRAPH_CYCLE_DETECTION` | `true` | Reject cyclic task graphs. |

## Tool runtime (spec §14/§16)

| Variable | Default | Description |
|---|---|---|
| `TERMINAL_ALLOWED_COMMANDS` | _(empty)_ | Allow-list for `terminal.execute`. Empty denies all shell execution. |
| `TERMINAL_TIMEOUT` | `60` | Terminal timeout, seconds. |
| `TERMINAL_MAX_OUTPUT_BYTES` | `1048576` | Output cap. |
| `TERMINAL_DENIED_PATTERNS` | `rm -rf /,mkfs,shutdown,reboot,dd if=` | Prefixes never allowed, even if allow-listed. |
| `PYTHON_TIMEOUT` | `60` | Python execution timeout, seconds. |
| `PYTHON_MEMORY_LIMIT_MB` | `512` | Python execution memory cap. |
| `WEB_TIMEOUT` | `30` | Web request timeout, seconds. |
| `WEB_MAX_RESPONSE_BYTES` | `5242880` | Web response size cap. |
| `WEB_USER_AGENT` | `ULTRON/0.1 (+https://localhost)` | User agent for web tools. |
| `WEB_SEARCH_PROVIDER` | `duckduckgo` | `duckduckgo` \| `searxng` \| `none`. |
| `WEB_SEARCH_URL` | _(empty)_ | Required for `searxng`. |
| `WEB_ALLOW_PRIVATE_NETWORKS` | `false` | SSRF guard (spec §31). |

## Browser automation (spec §11)

| Variable | Default | Description |
|---|---|---|
| `BROWSER_ENABLED` | `false` | Enable Playwright browser tools. |
| `BROWSER_HEADLESS` | `true` | Run headless. |
| `BROWSER_EXECUTABLE_PATH` | _(empty)_ | Empty uses the bundled Chromium. |
| `BROWSER_TIMEOUT` | `30` | Action timeout, seconds. |
| `BROWSER_MAX_SESSIONS` | `4` | Max concurrent sessions. |
| `BROWSER_SCREENSHOT_DIR` | `./data/screenshots` | Screenshot output directory. |
| `BROWSER_DOWNLOAD_DIR` | `./data/downloads` | Download directory. |
| `BROWSER_PERSIST_PROFILE` | `false` | Persist cookies/state between sessions. |
| `BROWSER_BLOCK_MEDIA` | `true` | Block images/media for speed. |

## Computer nodes (spec §13/§42)

| Variable | Default | Description |
|---|---|---|
| `COMPUTER_NODES_ENABLED` | `true` | Enable the node gateway. |
| `COMPUTER_NODE_TOKEN_TTL` | `3600` | Node token lifetime, seconds. |
| `COMPUTER_NODE_HEARTBEAT` | `15` | Expected heartbeat interval, seconds. |
| `COMPUTER_NODE_OFFLINE_AFTER` | `45` | Mark offline after this many seconds. |
| `COMPUTER_CAPABILITY_PRIORITY` | `application_api,dom,ui_automation,keyboard_shortcut,mouse,vision` | Capability preference order (spec §42). |
| `COMPUTER_NODE_DEFAULT_PERMISSION_LEVEL` | `4` | Default node permission level. |

## Devices / ESP32 (spec §26)

| Variable | Default | Description |
|---|---|---|
| `DEVICES_ENABLED` | `true` | Enable the device gateway. |
| `DEVICES_HEARTBEAT_TIMEOUT` | `60` | Device heartbeat timeout, seconds. |
| `DEVICES_MAX_CONNECTIONS` | `32` | Max device connections. |
| `ESP32_PROTOCOL_VERSION` | `1` | ESP32 protocol version. |
| `ESP32_REQUIRE_AUTH` | `true` | Require authentication from devices. |
| `MQTT_ENABLED` | `false` | Enable the optional MQTT transport. |
| `MQTT_BROKER_HOST` | `localhost` | MQTT broker host. |
| `MQTT_BROKER_PORT` | `1883` | MQTT broker port. |
| `MQTT_USERNAME` | _(empty)_ | MQTT username. |
| `MQTT_PASSWORD` | _(empty)_ | MQTT password. |
| `MQTT_CLIENT_ID` | `ultron` | MQTT client id. |
| `MQTT_TOPIC_PREFIX` | `ultron` | MQTT topic prefix. |

## Voice (spec §25)

| Variable | Default | Description |
|---|---|---|
| `VOICE_ENABLED` | `false` | Enable voice. |
| `STT_ENGINE` | `none` | `none` \| `faster-whisper` \| `groq`. |
| `STT_FALLBACK_ENGINE` | `none` | Fallback STT engine. |
| `STT_MODEL` | `base` | STT model. |
| `STT_DEVICE` | `cpu` | `cpu` \| `cuda`. |
| `STT_COMPUTE_TYPE` | `int8` | Compute type. |
| `STT_LANGUAGE` | `en` | Language hint. |
| `STT_TIMEOUT` | `60` | Timeout, seconds. |
| `STT_PARTIAL_RESULTS` | `true` | Emit partial transcripts. |
| `STT_AUDIO_FORMAT` | `wav` | `wav` \| `mp3` \| `webm` \| `ogg` \| `flac` \| `m4a`. |
| `GROQ_API_KEY` | _(empty)_ | Groq API key (OpenAI-compatible STT). |
| `GROQ_BASE_URL` | `https://api.groq.com/openai/v1` | Groq base URL. |
| `GROQ_STT_MODEL` | `whisper-large-v3-turbo` | Groq STT model. |
| `TTS_ENGINE` | `none` | `none` \| `piper` \| `elevenlabs` \| `edge-tts`. |
| `TTS_FALLBACK_ENGINE` | `none` | Fallback TTS engine. |
| `TTS_MODEL_PATH` | _(empty)_ | Path to a Piper `.onnx` voice. |
| `TTS_BINARY_PATH` | `piper` | Piper binary. |
| `TTS_SAMPLE_RATE` | `22050` | Output sample rate. |
| `TTS_TIMEOUT` | `60` | Timeout, seconds. |
| `ELEVENLABS_API_KEY` | _(empty)_ | ElevenLabs API key. |
| `ELEVENLABS_BASE_URL` | `https://api.elevenlabs.io/v1` | ElevenLabs base URL. |
| `ELEVENLABS_VOICE_ID` | `21m00Tcm4TlvDq8ikWAM` | Voice id. |
| `ELEVENLABS_MODEL` | `eleven_turbo_v2_5` | Model. |
| `ELEVENLABS_OUTPUT_FORMAT` | `pcm_22050` | `pcm_*` raw PCM, `mp3_*` MP3. |
| `EDGE_TTS_VOICE` | `en-US-AriaNeural` | edge-tts voice. |
| `VOICE_INTERRUPTION_ENABLED` | `true` | Barge-in: stop TTS when the user speaks. |
| `VOICE_SESSION_TTL` | `300` | Session lifetime, seconds. |
| `WAKE_WORDS` | `ultron,hey ultron` | Comma-separated wake words. |

## Scheduler (spec §44)

| Variable | Default | Description |
|---|---|---|
| `SCHEDULER_ENABLED` | `true` | Enable the scheduler. |
| `SCHEDULER_TIMEZONE` | `UTC` | Timezone. |
| `SCHEDULER_MAX_INSTANCES` | `3` | Max concurrent job instances. |
| `SCHEDULER_MISFIRE_GRACE` | `300` | Misfire grace, seconds. |
| `SCHEDULER_JOB_STORE` | `database` | Job store backend. |
| `SCHEDULER_MISFIRE_GRACE_SECONDS` | `300` | Misfire grace (alias form). |

## Notifications (spec §45)

| Variable | Default | Description |
|---|---|---|
| `NOTIFICATION_ENABLED` | `true` | Enable notifications. |
| `NOTIFICATION_DEFAULT_PROVIDER` | `log` | Default provider. |
| `NOTIFICATION_WEBHOOK_URL` | _(empty)_ | Webhook URL. |
| `NOTIFICATION_WEBHOOK_TIMEOUT` | `15` | Webhook timeout, seconds. |
| `NOTIFICATION_LOG_LEVEL` | `INFO` | Log level for the log provider. |

## Memory (spec §22)

| Variable | Default | Description |
|---|---|---|
| `MEMORY_ENABLED` | `true` | Enable memory. |
| `MEMORY_SHORT_TERM_MAX_MESSAGES` | `40` | Short-term buffer size. |
| `MEMORY_SHORT_TERM_TTL` | `86400` | Short-term TTL, seconds. |
| `MEMORY_RETRIEVAL_TOP_K` | `8` | Retrieval top-k. |
| `MEMORY_MIN_SIMILARITY` | `0.25` | Minimum similarity for semantic hits. |
| `MEMORY_EPISODIC_RETENTION_DAYS` | `180` | Episodic retention. |
| `MEMORY_LONG_TERM_ENABLED` | `true` | Enable long-term memory. |
| `MEMORY_SEMANTIC_ENABLED` | `true` | Enable semantic memory. |
| `MEMORY_DEFAULT_NAMESPACE` | `user` | Default namespace. |

## Observability (spec §32)

| Variable | Default | Description |
|---|---|---|
| `METRICS_ENABLED` | `false` | Expose `/metrics`. Off by default because the probe is unauthenticated. |
| `METRICS_PATH` | `/metrics` | Metrics path. |
| `METRICS_INCLUDE_IN_LOG` | `false` | Include metrics in logs. |
| `RESOURCE_MONITOR_ENABLED` | `true` | Sample host resources. |
| `RESOURCE_MONITOR_INTERVAL` | `30` | Sample interval, seconds. |
| `CPU_HIGH_PERCENT` | `85` | CPU threshold for `CPU_HIGH`. |
| `RAM_HIGH_PERCENT` | `90` | RAM threshold for `RAM_HIGH`. |
| `DISK_LOW_PERCENT` | `10` | Free-space threshold for `DISK_LOW`. |
| `GPU_HIGH_PERCENT` | `90` | GPU threshold for `GPU_HIGH`. |
| `EVENTS_PERSIST` | `true` | Persist bus events. |
| `EVENT_RETENTION_DAYS` | `30` | Event retention. |
| `AUDIT_LOG_RETENTION_DAYS` | `180` | Audit-log retention. |
| `HEALTHCHECK_CACHE_TTL` | `5` | Health-check cache TTL, seconds. |

---

## Production minimum

Before exposing ULTRON, at minimum generate `JWT_SECRET`, change
`ADMIN_PASSWORD`, set a real `DATABASE_URL` password, set `ENVIRONMENT=production`,
keep `ALLOW_ANONYMOUS=false`, keep `METRICS_ENABLED=false` unless `/metrics` is
restricted at the proxy, set `ALLOWED_HOSTS`, and set `CORS_ORIGINS` to your real
origin. See [`security.md`](security.md) and [`deployment.md`](deployment.md).
