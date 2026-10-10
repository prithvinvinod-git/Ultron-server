# ULTRON — COMPLETE SERVER-SIDE BUILD SPECIFICATION

## ROLE

You are the primary senior software engineer responsible for building the server-side infrastructure of a project called **ULTRON**.

ULTRON is a local-first, agentic AI operating environment.

You are not building a chatbot.

You are building the backend operating infrastructure that coordinates:

* AI models
* autonomous agents
* tools
* tasks
* memory
* browser automation
* coding agents
* computer-control nodes
* voice
* ESP32/IoT devices
* scheduling
* permissions
* verification
* events
* persistent state
* observability

The system must be modular, secure, observable, testable, and deployable to a standalone Ubuntu Linux server.

The initial development environment is:

* Windows
* VS Code
* Git
* Python
* Node.js
* local development services where practical

The completed project will later be cloned from Git onto:

* Ubuntu Server 26.x
* headless/server environment
* Ethernet
* optional NVIDIA GPU
* optional Ollama models

DO NOT design the codebase around Windows-only paths or Windows-only APIs.

The server must ultimately run independently on Ubuntu.

---

# 1. CORE ARCHITECTURAL PRINCIPLE

ULTRON is NOT an LLM.

ULTRON is the orchestration platform around LLMs.

The relationship is:

```text
                    ULTRON
                       |
                  ULTRON CORE
                       |
       +---------------+---------------+
       |               |               |
    Models           Agents           Tools
       |               |               |
 Ollama/OpenAI    Coding/Research   Browser/System
 Gemini/Anthropic Browser/System     Files/Git
       |               |               |
       +---------------+---------------+
                       |
                    Memory
                       |
                  Task Engine
                       |
                  Event System
```

Never couple ULTRON Core directly to one model provider.

Never allow an LLM to directly receive unrestricted operating-system access.

Never make a specific agent framework the foundation of the entire architecture.

Every external engine must be accessed through an adapter/interface.

> **Amended after §63 (added with §64):** the diagram above is the logical
> view. Physically, Core runs on the Ubuntu server (§64.4) while parts of the
> tool layer execute on other machines - the Windows node (§64.6), the server
> node (§64.7), the ESP32 (§64.9). The principles are unchanged: everything
> still reaches the OS through adapters, and no node bypasses Core's
> permission and event systems (§64.16).

---

# 2. PRIMARY OBJECTIVES

Build a complete server platform containing:

1. ULTRON Core
2. API server
3. WebSocket server
4. Agent runtime
5. Agent manager
6. Model router
7. Ollama integration
8. external model adapters
9. tool runtime
10. permission system
11. task/workflow engine
12. event bus
13. memory system
14. PostgreSQL persistence
15. Redis infrastructure
16. coding agent
17. OpenCode CLI integration
18. research agent
19. browser agent
20. system agent
21. computer-node gateway
22. voice backend
23. ESP32/IoT gateway
24. scheduler
25. verification system
26. logging
27. observability
28. health monitoring
29. configuration system
30. authentication
31. deployment configuration
32. testing infrastructure
33. documentation

Build these as independent modules.

---

# 3. TECHNOLOGY STACK

Use the following stack unless there is a strong technical reason to change it.

## Backend

Python 3.12+

FastAPI

Pydantic v2

Uvicorn

WebSockets

SQLAlchemy

Alembic

asyncio

httpx

## Database

PostgreSQL

pgvector where appropriate

Redis

## Local AI

Ollama

The model provider must be abstracted.

## External AI

Create provider adapters for:

* OpenAI
* Google Gemini
* Anthropic

Do not require API keys during initial local development.

Providers must be optional.

## Browser

Playwright

## Coding

OpenCode CLI

ULTRON must communicate with OpenCode through a dedicated adapter.

## Voice

STT adapter architecture compatible with:

* faster-whisper initially
* future cloud STT providers

TTS adapter architecture compatible with:

* Piper initially
* future cloud TTS providers

## Hardware

ESP32 communication over network.

MQTT may be used for device messaging if appropriate.

WebSocket/HTTP should remain available.

## Deployment

Docker Compose

systemd-compatible services

Ubuntu Linux

## Monitoring

Prometheus-compatible metrics

structured logs

health endpoints

Optional Grafana integration

---

# 4. REPOSITORY STRUCTURE

Create a clean monorepo:

```text
ultron/
│
├── server/
│   ├── app/
│   │   ├── main.py
│   │   │
│   │   ├── api/
│   │   │   ├── routes/
│   │   │   ├── websocket/
│   │   │   └── dependencies.py
│   │   │
│   │   ├── core/
│   │   │   ├── orchestrator.py
│   │   │   ├── planner.py
│   │   │   ├── router.py
│   │   │   ├── executor.py
│   │   │   ├── context.py
│   │   │   ├── permissions.py
│   │   │   ├── events.py
│   │   │   └── lifecycle.py
│   │   │
│   │   ├── agents/
│   │   │   ├── base.py
│   │   │   ├── manager.py
│   │   │   ├── registry.py
│   │   │   ├── coding/
│   │   │   ├── research/
│   │   │   ├── browser/
│   │   │   ├── system/
│   │   │   ├── computer/
│   │   │   ├── automation/
│   │   │   ├── vision/
│   │   │   ├── file/
│   │   │   └── iot/
│   │   │
│   │   ├── models/
│   │   │   ├── base.py
│   │   │   ├── router.py
│   │   │   ├── ollama.py
│   │   │   ├── openai.py
│   │   │   ├── gemini.py
│   │   │   └── anthropic.py
│   │   │
│   │   ├── tools/
│   │   │   ├── base.py
│   │   │   ├── registry.py
│   │   │   ├── executor.py
│   │   │   ├── browser/
│   │   │   ├── terminal/
│   │   │   ├── filesystem/
│   │   │   ├── git/
│   │   │   ├── python/
│   │   │   ├── web/
│   │   │   ├── system/
│   │   │   ├── notifications/
│   │   │   └── computer/
│   │   │
│   │   ├── memory/
│   │   │   ├── manager.py
│   │   │   ├── short_term.py
│   │   │   ├── long_term.py
│   │   │   ├── episodic.py
│   │   │   ├── semantic.py
│   │   │   └── project.py
│   │   │
│   │   ├── tasks/
│   │   │   ├── manager.py
│   │   │   ├── executor.py
│   │   │   ├── graph.py
│   │   │   └── state.py
│   │   │
│   │   ├── events/
│   │   │   ├── bus.py
│   │   │   ├── types.py
│   │   │   └── handlers.py
│   │   │
│   │   ├── voice/
│   │   │   ├── stt/
│   │   │   ├── tts/
│   │   │   ├── wake/
│   │   │   └── manager.py
│   │   │
│   │   ├── devices/
│   │   │   ├── manager.py
│   │   │   ├── esp32/
│   │   │   └── protocol.py
│   │   │
│   │   ├── scheduler/
│   │   │   └── manager.py
│   │   │
│   │   ├── verification/
│   │   │   ├── verifier.py
│   │   │   └── checks.py
│   │   │
│   │   ├── security/
│   │   │   ├── auth.py
│   │   │   ├── permissions.py
│   │   │   └── audit.py
│   │   │
│   │   ├── database/
│   │   │   ├── models/
│   │   │   ├── repositories/
│   │   │   └── session.py
│   │   │
│   │   ├── observability/
│   │   │   ├── logging.py
│   │   │   ├── metrics.py
│   │   │   └── health.py
│   │   │
│   │   └── config/
│   │       └── settings.py
│   │
│   ├── tests/
│   ├── migrations/
│   └── pyproject.toml
│
├── clients/
│   ├── desktop-node/
│   ├── orb/
│   └── protocol/
│
├── deployment/
│   ├── docker/
│   ├── compose/
│   ├── systemd/
│   └── scripts/
│
├── docs/
│
├── scripts/
│
├── .env.example
├── docker-compose.yml
├── README.md
└── LICENSE
```

Keep modules small.

Do not create giant files.

---

# 5. ULTRON CORE

Implement the central orchestrator.

Responsibilities:

* receive user requests
* maintain context
* classify intent
* select model
* select agent
* create tasks
* delegate tasks
* execute tool calls
* monitor agents
* receive events
* verify results
* maintain state
* produce final responses

Core lifecycle:

```text
REQUEST
 ↓
UNDERSTAND
 ↓
PLAN
 ↓
CREATE TASK
 ↓
SELECT AGENT
 ↓
SELECT MODEL
 ↓
EXECUTE
 ↓
OBSERVE
 ↓
VERIFY
 ↓
RETRY / REPLAN
 ↓
COMPLETE
 ↓
RESPOND
```

Do not allow agents to bypass the Core's permission and event systems.

> **Amended after §63 (added with §64):** Core describes *what* the
> orchestrator does, not *where* it runs. Core always runs in the cloud server
> (§64.4); Windows, mobile, and ESP32 never host it. Nodes register their
> capabilities with Core and execute tool calls it routes to them (§64.10,
> §64.11) - they do not become orchestrators.

---

# 6. AGENT RUNTIME

Create a common Agent interface.

Every agent should have:

```text
agent_id
agent_type
name
description
status
task_id
model
tools
permissions
memory_namespace
created_at
updated_at
```

Agent lifecycle:

```text
CREATED
 ↓
INITIALIZING
 ↓
READY
 ↓
RUNNING
 ↓
WAITING
 ↓
VERIFYING
 ↓
COMPLETED
```

Failure states:

```text
FAILED
CANCELLED
TIMEOUT
```

Agents must emit events during execution.

---

# 7. AGENT MANAGER

The Agent Manager must be able to:

* create agents
* destroy agents
* pause agents
* resume agents
* cancel agents
* inspect agents
* assign tasks
* assign models
* assign tools
* assign permissions
* monitor health
* retrieve status

Support multiple simultaneous agents.

Example:

```text
CodingAgent #001
ResearchAgent #002
BrowserAgent #003
SystemAgent #004
```

All can execute concurrently.

Use asyncio/task management appropriately.

Do not block the entire server because one agent is running.

> **Amended after §65 (added with §66):** §66.5/§66.20 add the orchestration
> contract on top of this runtime - selection, sequential/parallel invocation,
> structured `AgentMessage` results between agents, per-invocation permission
> checks and an execution history. The runtime itself (registry, manager,
> lifecycle) is unchanged; agents still never bypass the Core's permission and
> event systems.

---

# 8. CODING AGENT

Create a Coding Agent.

Its purpose is software engineering.

Capabilities:

* inspect repository
* understand project
* create task plan
* edit files
* run tests
* run builds
* inspect Git
* create branches
* inspect diffs
* report changes
* verify results

The actual coding engine must be abstracted.

Initial implementation:

```text
Coding Agent
      ↓
OpenCode Adapter
      ↓
OpenCode CLI
```

ULTRON should NOT hard-code OpenCode throughout the system.

Create:

```text
CodingEngine
```

interface.

Then:

```text
OpenCodeEngine
```

implementation.

Future engines can be added:

```text
ClaudeCodeEngine
AiderEngine
CustomEngine
```

without rewriting Coding Agent.

---

# 9. OPENCODE INTEGRATION

Create a secure OpenCode subprocess adapter.

Requirements:

* configurable executable path
* configurable model/provider
* configurable working directory
* task input
* output streaming
* exit-code handling
* timeout
* cancellation
* logs
* process cleanup

The adapter must never expose arbitrary server directories to an agent automatically.

Every coding task must receive an explicit workspace.

Example:

```text
/workspaces/campuscare
/workspaces/deskai
/workspaces/ultron
```

Support Git isolation.

Prefer a dedicated branch/worktree for autonomous modifications.

---

# 10. RESEARCH AGENT

Create a Research Agent.

Capabilities:

* web search
* retrieve pages
* extract information
* compare sources
* summarize
* cite sources
* preserve source URLs
* create research artifacts

Use a Web Tool abstraction.

Do not make the LLM directly perform arbitrary HTTP requests.

---

# 11. BROWSER AGENT

Create a Browser Agent using Playwright.

Capabilities:

* create browser session
* navigate
* click
* type
* select
* inspect page
* extract text
* screenshots
* downloads
* cookies/session handling
* close session

Browser sessions must be isolated by agent.

Example:

```text
BrowserAgent #12
 ↓
BrowserSession #12
 ↓
Playwright
```

Do not use GUI automation when DOM/browser automation can solve the task.

---

# 12. SYSTEM AGENT

Create a System Agent for server monitoring.

Monitor:

* CPU
* RAM
* disk
* GPU
* temperature where available
* processes
* network
* services
* Ollama status
* Docker status

It should be able to report:

```text
CPU: ...
RAM: ...
GPU: ...
Disk: ...
Ollama: ...
Agents: ...
Tasks: ...
```

Safe read-only operations should not require elevated permissions.

---

# 13. COMPUTER AGENT

Create a Computer Agent abstraction.

The server should not assume the target computer is the server itself.

Create a Computer Node architecture:

```text
ULTRON SERVER
      |
 Computer Agent
      |
 Computer Node Gateway
      |
      +---- Windows Laptop
      +---- Windows Desktop
      +---- future Linux machine
```

The Windows node will later expose controlled capabilities such as:

* Windows UI automation
* Windows-Use
* PowerShell
* filesystem
* screenshots
* keyboard/mouse
* application control

The server should issue structured commands rather than blindly executing arbitrary commands.

> **Amended after §63 (added with §64):** this Computer Node diagram is now
> normative rather than "later". It is the Windows node of §64.6, registered in
> the single node registry of §64.5 with an explicit capability list (§64.10),
> reachable through node-targeted tool execution (§64.11). The capability list
> above matches §64.6.2; nothing new was added here.

---

# 14. TOOL SYSTEM

Every tool must implement a common interface.

Example conceptual schema:

```text
Tool
 ├── name
 ├── description
 ├── input_schema
 ├── permission_level
 ├── execute()
 └── verify()
```

Create a Tool Registry.

Example tools:

```text
browser.open
browser.click
browser.type

filesystem.read
filesystem.write
filesystem.list

git.status
git.diff
git.branch

terminal.execute

system.cpu
system.memory
system.disk

computer.screenshot
computer.click
computer.type

web.search
web.fetch
```

---

# 15. PERMISSION SYSTEM

Never allow unrestricted agent access.

Use levels:

```text
LEVEL 0
Read-only

LEVEL 1
Safe actions

LEVEL 2
Modify project files

LEVEL 3
Execute programs

LEVEL 4
System configuration

LEVEL 5
Destructive / high-risk actions
```

Every tool declares its required level.

Every execution is checked by Permission Manager.

Every execution is logged.

> **Amended after §63 (added with §64):** LEVEL 0 - LEVEL 5 remains the only
> permission scale (D020). What §64.12 adds is *scope*: a grant is evaluated as
> (principal, node, tool, operation), so a LEVEL 3 permission granted for the
> server node does not silently authorise the same tool on the Windows node.
> Client-side "capabilities" (§63.3) are a separate, non-security reporting
> concern and never substitute for this check.

---

# 16. TOOL EXECUTION PIPELINE

Never directly:

```text
LLM → OS
```

Use:

```text
LLM
 ↓
Tool Request
 ↓
Schema Validation
 ↓
Permission Check
 ↓
Policy Check
 ↓
Execution
 ↓
Result
 ↓
Verification
 ↓
Event
 ↓
LLM
```

> **Amended after §63 (added with §64):** one pipeline stage sits between
> Policy Check and Execution: **target selection** (§64.11). A tool call
> carries a `node` target; the router dispatches it to the Windows node, the
> server node, or an ESP32-capable handler, and the result returns through the
> same Verification/Event stages. Tools that do not declare a node run where
> they always ran - in the cloud server.

---

# 17. VERIFICATION SYSTEM

ULTRON must never assume success.

Create verification mechanisms.

Examples:

Coding:

```text
edit
 ↓
run tests
 ↓
inspect exit code
 ↓
verify files
```

Browser:

```text
click
 ↓
inspect page
 ↓
verify expected state
```

System:

```text
restart service
 ↓
health check
 ↓
confirm service active
```

The final response should distinguish:

```text
SUCCESS
PARTIAL
FAILED
UNVERIFIED
```

> **Amended after §65 (added with §66):** §66.4 writes the full
> goal→plan→execute→verify lifecycle around this section -
> UNDERSTAND → CONTEXT → PLAN → PERMISSION → AGENT → NODE → TOOL → OBSERVE →
> VERIFY → complete | retry | replan | escalate - implemented by the existing
> planner/orchestrator/executor tasks (T048-T050) on the task DAG (T040/T041).
> This section's verification mechanisms remain the authority for *how* a
> result is checked.

---

# 18. TASK ENGINE

Build a persistent task system.

A task contains:

```text
task_id
goal
status
priority
created_at
started_at
completed_at
agent_id
parent_task_id
steps
result
error
```

Support task graphs.

Example:

```text
Task
 |
 +-- Research
 |
 +-- Implementation
 |
 +-- Testing
 |
 +-- Verification
 |
 +-- Report
```

Tasks must survive server restarts where practical.

---

# 19. EVENT BUS

Create an internal event bus.

Events include:

```text
USER_MESSAGE

TASK_CREATED
TASK_STARTED
TASK_COMPLETED
TASK_FAILED

AGENT_CREATED
AGENT_STARTED
AGENT_PAUSED
AGENT_COMPLETED
AGENT_FAILED

TOOL_STARTED
TOOL_COMPLETED
TOOL_FAILED

MODEL_REQUEST
MODEL_RESPONSE

BUILD_STARTED
BUILD_FAILED
TEST_FAILED

DEVICE_CONNECTED
DEVICE_DISCONNECTED

WAKE_DETECTED
STT_PARTIAL
STT_FINAL
TTS_STARTED
TTS_COMPLETED

CPU_HIGH
RAM_HIGH
DISK_LOW
GPU_HIGH

SCHEDULE_TRIGGERED
```

Support asynchronous subscribers.

> **Amended after §64 (added with §65):** telephony adds `VOICE_CALL_*` events
> (CREATED/RINGING/CONNECTED/LISTENING/THINKING/SPEAKING/ENDED/FAILED) to this
> bus - additive names on the same T030/T031 bus, never a second bus, and §19's
> existing set is not renamed (same rule as §64.15). They fan out over the
> existing SSE stream (T023/T320) to Electron, mobile, ESP32 and logs.

---

# 20. MODEL ROUTER

Create a model abstraction.

Conceptually:

```text
ModelProvider
      |
      +-- Ollama
      +-- OpenAI
      +-- Gemini
      +-- Anthropic
```

The router chooses the provider/model.

Configuration should allow:

```text
default_model
coding_model
research_model
vision_model
fast_model
reasoning_model
```

Do not hard-code model names throughout the application.

Example:

```text
model.router.select(
    capability="coding",
    speed="balanced"
)
```

> **Amended after §65 (added with §66):** §66.6 extends this router rather
> than adding one: selection inputs grow (task/agent type, capability, latency,
> cost, context size, availability, privacy, local/cloud preference) and
> fallback chains (§59.9) gain provider **health tracking with temporary
> circuit breaking**. Providers stay config-selected behind the adapter
> interface; no provider type reaches agents, and no provider is hard-coded.

---

# 21. OLLAMA

Create a proper Ollama adapter.

Requirements:

* configurable host
* model discovery
* health check
* chat
* streaming
* model availability check
* timeout
* error handling

The default local endpoint should be configurable.

Do not assume Ollama runs on localhost in production.

---

# 22. MEMORY SYSTEM

Create separate memory layers.

## Short-term

Current conversation/context.

## Long-term

Persistent user/project facts.

## Episodic

What ULTRON did previously.

## Semantic

Embeddings/vector retrieval.

## Project memory

Project-specific context.

Use PostgreSQL + pgvector initially.

Redis may handle temporary state/caching.

Memory must have namespaces.

Example:

```text
user
project:campuscare
project:ultron
agent:coding
agent:research
```

Do not mix unrelated project memories.

> **Amended after §65 (added with §66):** §66.12-§66.13 extend this system in
> place: preference/task/device/agent memory become **scopes on these same
> layers** (not a second store), and knowledge/RAG ingestion
> (source→ingest→parse→chunk→embed→index) targets the existing pgvector
> `memories.embedding` vector store - one vector database, permission-scoped
> retrieval, intentional extraction (conversation messages are not memorized
> by default).

---

# 23. DATABASE

Use PostgreSQL.

Create migrations with Alembic.

Initial tables should cover:

```text
users
sessions
agents
tasks
task_steps
tool_executions
events
conversations
messages
memories
projects
devices
device_events
agent_logs
model_usage
audit_logs
```

Do not over-normalize unnecessarily.

Use repository/service patterns.

---

# 24. REDIS

Use Redis for:

* temporary state
* queues where required
* pub/sub
* WebSocket coordination
* caching
* rate limiting
* short-lived locks

Do not use Redis as the permanent source of truth for important data.

PostgreSQL remains authoritative.

---

# 25. VOICE BACKEND

Create a voice subsystem that is provider-independent.

Architecture:

```text
Audio
 ↓
VAD
 ↓
Wake Word
 ↓
STT
 ↓
ULTRON Core
 ↓
Agent
 ↓
Response
 ↓
TTS
```

Initial adapters:

```text
STT → faster-whisper
TTS → Piper
```

Support streaming/partial transcripts where practical.

The ESP32 wake-word device should communicate with the server through a defined protocol.

> **Amended after §64 (added with §65):** a phone call is a second *source* of
> audio into this same subsystem, not a second voice system (§65.1). The
> telephony service (§65.3) attaches call audio to a normal voice session
> (T153); STT/TTS stay behind the `STTEngine`/`TTSEngine` ABCs (T150/T151), and
> the pipeline above runs unchanged once audio arrives.

---

# 26. ESP32 / DEVICE GATEWAY

Create a device abstraction.

Each device has:

```text
device_id
name
type
capabilities
status
last_seen
firmware_version
```

Support events:

```text
wake_word
button_press
sensor_event
audio_stream
device_online
device_offline
```

Do not hard-code one ESP32 board into the Core.

> **Amended after §63 (added with §64):** device records (T161) and node
> records (T141/T332) are **one registry**, not two (D021). An ESP32 is a node
> of type `esp32` carrying the device fields above plus heartbeat and
> capability data (§64.5, §64.10); `device_online` / `device_offline` are the
> same events as the `NODE_*` family of §64.15, kept under their existing names
> here so §19 is not rewritten.

---

# 27. DESKTOP ORB BACKEND SUPPORT

The server must support a desktop client later.

The orb is NOT the AI.

It is a client UI.

Server events should allow:

```text
ORB_SHOW
ORB_HIDE
ORB_STATE_CHANGED
TRANSCRIPT_PARTIAL
TRANSCRIPT_FINAL
AGENT_CREATED
AGENT_STARTED
AGENT_COMPLETED
AGENT_FAILED
```

Example:

```text
ESP32 wake
 ↓
Server
 ↓
WAKE_DETECTED
 ↓
Desktop client
 ↓
Show orb
```

---

# 28. AGENT WINDOW PROTOCOL

Each agent may later have its own standalone desktop window.

The server should expose agent state through WebSocket.

Example:

```text
Agent #A123
```

has:

```text
status
task
plan
current_step
tool_activity
logs
result
```

The Windows client can create:

```text
Coding Agent Window #A123
```

The server remains the actual execution environment.

The UI is only a visualization/control surface.

> **Amended after §63 (added with §64):** "the server" in this section means
> **agents and their reasoning execute in the cloud server** - that is still
> true. What §64.6 adds is that a *tool call* an agent makes may be routed to
> the Windows node (§64.11), so the effects of some executions happen on
> Windows while the agent, permission check, and event log stay server-side.
> Window state is published as cross-node events (§64.15), so the visualization
> surface works the same whether it is a local window or a remote one.

---

# 29. API

Create REST endpoints for:

```text
/health

/auth

/agents
/agents/{id}

/tasks
/tasks/{id}

/models
/models/{id}

/projects
/projects/{id}

/memory

/tools

/devices

/system

/events
```

Use WebSockets for real-time state.

Example:

```text
/ws
/ws/agents/{agent_id}
/ws/tasks/{task_id}
/ws/devices/{device_id}
```

> **Amended after §63 (T311/T320).** The *client-facing* stream is now
> **receive-only SSE**, not `/ws`, so these paths describe the internal/optional
> direction only. Per-channel subscription (`/ws/agents/{agent_id}` and friends)
> is replaced by SSE `Last-Event-ID` filtering or distinct event paths. The
> ESP32 device transport stays JSON-over-WebSocket (T162) and is unaffected.
> Do not build the mobile/desktop client against `/ws`.

> **Amended after §64 (added with §65):** telephony adds `/voice/calls`,
> `/voice/calls/{call_id}`, `/voice/calls/{call_id}/end` and the unauthenticated
> but signature-verified `/voice/webhooks/{provider}` intake (§65.6), following
> this section's plural convention. User-facing routes require the existing
> auth + permission dependencies; responses never carry provider secrets.

---

# 30. AUTHENTICATION

Implement a basic authentication architecture.

Do not expose administrative functionality publicly without authentication.

Use:

* secure password handling
* session/token architecture
* API keys for machine clients
* device authentication
* role/permission checks

The internal server should still use authorization checks even on trusted networks.

---

# 31. SECURITY

Security is a first-class subsystem.

Never:

* execute raw LLM-generated shell commands without validation
* expose PostgreSQL publicly
* expose Redis publicly
* expose Ollama publicly without protection
* give agents unrestricted filesystem access
* store secrets in Git
* commit `.env`
* run everything as root

Use:

```text
.env
.env.example
```

Secrets must come from environment/configuration.

Create an audit log for sensitive operations.

---

# 32. OBSERVABILITY

Every important operation must be observable.

Structured logs should contain:

```text
timestamp
request_id
task_id
agent_id
tool
event
status
duration
error
```

Add:

```text
/health
/ready
/metrics
```

Health checks:

```text
PostgreSQL
Redis
Ollama
filesystem
agent runtime
event bus
```

---

# 33. ERROR HANDLING

Never silently swallow exceptions.

Every subsystem must have:

* typed exceptions
* structured errors
* logging
* retry policy where appropriate
* timeout
* cancellation
* graceful degradation

External model failures should not crash ULTRON Core.

If Ollama is unavailable, the system should report:

```text
LOCAL_MODEL_UNAVAILABLE
```

rather than crashing.

---

# 34. CONFIGURATION

Use environment variables/configuration.

Create:

```text
.env.example
```

Include placeholders for:

```text
DATABASE_URL
REDIS_URL

OLLAMA_URL

OPENAI_API_KEY
GEMINI_API_KEY
ANTHROPIC_API_KEY

OPENCODE_PATH

JWT_SECRET

LOG_LEVEL
ENVIRONMENT
```

Never put real secrets in the repository.

---

# 35. DOCKER COMPOSE

Create a production-oriented Compose stack.

Initial services:

```text
ultron-api
postgres
redis
ollama
```

Optional:

```text
prometheus
grafana
```

Do not require every optional component for basic startup.

Make service dependencies explicit.

---

# 36. LOCAL WINDOWS DEVELOPMENT

The repository must work from Windows during development.

Provide:

```text
scripts/dev.ps1
scripts/test.ps1
scripts/lint.ps1
scripts/start.ps1
```

Also provide Linux equivalents:

```text
scripts/dev.sh
scripts/test.sh
scripts/start.sh
```

Do not hard-code Windows paths.

Use pathlib/configuration.

---

# 37. UBUNTU DEPLOYMENT

Create deployment documentation for:

```text
Ubuntu Server 26.x
```

The process should be:

```text
git clone
 ↓
install prerequisites
 ↓
configure .env
 ↓
docker compose up -d
 ↓
run migrations
 ↓
health check
```

Also document a non-Docker/systemd deployment path where practical.

Create systemd unit examples for:

```text
ultron-api
ultron-worker
```

Do not require manual modification of source code on the server.

---

# 38. TESTING

Create tests throughout the project.

At minimum:

## Unit tests

* model router
* agent lifecycle
* task lifecycle
* permission manager
* tool registry
* event bus
* memory manager
* configuration

## Integration tests

* PostgreSQL
* Redis
* Ollama adapter
* API
* WebSocket
* agent creation
* task execution

## End-to-end tests

Example:

```text
API request
 ↓
Core
 ↓
Task
 ↓
Agent
 ↓
Mock tool
 ↓
Verification
 ↓
result
```

External API tests should be mockable.

Do not require paid APIs to run the test suite.

---

# 39. CODING STANDARDS

Use:

* type hints
* async where appropriate
* Pydantic models
* dependency injection
* small functions
* clear interfaces
* docstrings for important public interfaces
* structured logging
* tests

Avoid:

* giant classes
* giant files
* circular dependencies
* global mutable state
* hard-coded API keys
* hard-coded model names
* hidden subprocesses
* direct database access from agents

---

# 40. AGENT/TOOL SEPARATION

Maintain this distinction:

```text
MODEL
Reasoning engine

AGENT
Goal-oriented worker

TOOL
Capability

TASK
Unit of work

MEMORY
Persistent/contextual information

CORE
Orchestration

CLIENT
User interface

NODE
Machine/device interface
```

Never merge these concepts unnecessarily.

---

# 41. MULTI-AGENT EXECUTION

The server must support multiple simultaneous agents.

Example:

```text
                    ULTRON CORE
                         |
              +----------+----------+
              |          |          |
              ▼          ▼          ▼
          Coding       Research    Browser
          Agent         Agent       Agent
              |          |          |
              ▼          ▼          ▼
          OpenCode      Web       Playwright
```

One agent failure must not terminate the others.

Agent state must be isolated.

---

# 42. FUTURE COMPUTER CONTROL

Design the server so that Windows automation can later be connected without redesign.

Target:

```text
ULTRON SERVER
      |
 Computer Agent
      |
 Computer Node Protocol
      |
 Windows Desktop Node
      |
 +----+---------+
 |              |
Windows-Use   Native APIs
```

Windows-Use should be an implementation behind the Computer Node abstraction.

Do not make ULTRON Core directly dependent on Windows-Use.

---

# 43. FUTURE DESKTOP CLIENT

Do not build the full desktop UI now.

However, expose everything needed by a future client.

The future client will provide:

```text
ULTRON Orb
Agent windows
Chat
Voice
Task status
System dashboard
Notifications
```

The server should already provide the WebSocket events and API contracts needed for these.

---

# 44. SCHEDULER

Create a scheduler subsystem.

Support:

```text
one-time tasks
recurring tasks
delayed tasks
event-triggered tasks
```

Examples:

```text
Every day at 8 AM
When ESP32 wakes
When disk < 10%
When a GitHub event occurs
```

Use APScheduler initially or an equivalent lightweight scheduler.

Persist important scheduled jobs.

> **Amended after §65 (added with §66):** §66.11 places autonomous background
> agents on this scheduler and the existing task DAG (T040/T041, T044 manager
> lifecycle) - scheduling, cancel, pause/resume, retry, timeout, resource
> limits, logs, notifications - bounded by a max-iteration/timeout policy so a
> background agent cannot loop forever. §65.18 also lands scheduled phone calls
> here: "call me at 8 PM" is an ordinary job whose action is
> `telephony.initiate_call`. No second scheduler exists or is needed.

---

# 45. NOTIFICATION SYSTEM

Create an abstraction:

```text
NotificationProvider
```

Future implementations:

```text
Desktop
Push
Telegram
WhatsApp
Email
ESP32
```

Do not tightly couple notification logic to one provider.

---

# 46. GIT WORKSPACE MANAGEMENT

Create a workspace manager.

Responsibilities:

* register projects
* locate repositories
* create isolated workspaces
* create branches
* inspect status
* generate diffs
* clean up temporary workspaces

Example:

```text
/workspaces/
    campuscare/
    ultron/
    deskai/
```

Coding agents must be explicitly assigned workspaces.

---

# 47. SERVER DIRECTORY LAYOUT

When deployed to Ubuntu, target:

```text
/opt/ultron/
    server/
    deployment/
    config/
    workspaces/
    logs/
    data/
```

Persistent databases should preferably use Docker volumes or dedicated persistent paths.

Do not store critical data only inside ephemeral containers.

---

# 48. RESOURCE MANAGEMENT

The server may initially have limited hardware.

Do not assume a powerful GPU.

Agents must support:

* CPU operation
* local Ollama
* cloud model fallback
* configurable concurrency
* timeouts
* queueing

Implement basic resource-aware scheduling.

Example:

```text
GPU unavailable
 ↓
use CPU model

GPU busy
 ↓
queue task

large model unavailable
 ↓
fallback model
```

Do not automatically download huge models.

---

# 49. MODEL SELECTION STRATEGY

The model router should consider:

```text
task type
capability
latency
availability
resource usage
provider
user preference
```

Example:

```text
simple command
 → fast local model

coding
 → coding-capable model

deep reasoning
 → reasoning model

vision
 → vision model
```

This must remain configurable.

---

# 50. FINAL ULTRON FLOW

The completed server should support:

```text
USER
 ↓
CLIENT
 ↓
API / WebSocket
 ↓
ULTRON CORE
 ↓
INTENT
 ↓
PLAN
 ↓
TASK GRAPH
 ↓
AGENT MANAGER
 ↓
AGENT
 ↓
MODEL ROUTER
 ↓
MODEL
 ↓
TOOL ROUTER
 ↓
PERMISSION
 ↓
TOOL
 ↓
OBSERVE
 ↓
VERIFY
 ↓
MEMORY
 ↓
EVENT BUS
 ↓
RESULT
 ↓
CLIENT
```

> **Amended after §67/§68 (spatial + perception addenda):** two additive
> changes to this flow. (1) **Interface selection** after RESULT→CLIENT: the
> response planner may render text, the 2D component registry, or a spatial
> scene (§67.2) - three modes of the same client, never a second UI system.
> (2) **A second, observation-only entry path** feeds INTENT from the world:
> CAMERA/SCREEN → local vision service → `PERCEPTION_*`/`GESTURE_*` events
> (§68) → context engine → INTENT. Perceptions observe; they never reach
> TOOL without passing PERMISSION on the action path above (§68.12). The
> motto of the extended system: **AI decides, agents reason, tools act,
> nodes execute, events communicate, UI renders, spatial UI visualizes,
> vision detects, gestures interact, permissions govern, verification
> confirms.**

---

# 51. DEVELOPMENT PHASES

Do NOT attempt to implement every subsystem simultaneously.

Build in phases.

## PHASE 1 — FOUNDATION

Implement:

* repository
* configuration
* FastAPI
* PostgreSQL
* Redis
* database models
* migrations
* logging
* health checks
* API structure
* WebSocket foundation

The server must start successfully.

---

## PHASE 2 — CORE

Implement:

* Core
* context
* intent routing
* task manager
* event bus
* agent manager
* permission manager
* tool registry

Create mock agents/tools first.

---

## PHASE 3 — MODEL SYSTEM

Implement:

* Model interface
* Ollama adapter
* OpenAI adapter
* Gemini adapter
* Anthropic adapter
* Model Router
* streaming

The system must function with only Ollama configured.

---

## PHASE 4 — CODING AGENT

Implement:

* Coding Agent
* Coding Engine interface
* OpenCode adapter
* workspace manager
* Git integration
* subprocess management
* output streaming
* verification

Test using a small sample repository.

---

## PHASE 5 — TOOLS

Implement:

* filesystem
* terminal
* Git
* Python
* web
* system tools
* browser

Apply permissions.

---

## PHASE 6 — RESEARCH + BROWSER

Implement:

* Research Agent
* Browser Agent
* Playwright
* source collection
* browser session management

---

## PHASE 7 — MEMORY

Implement:

* short-term
* long-term
* episodic
* semantic
* project memory
* pgvector
* retrieval

---

## PHASE 8 — COMPUTER NODE

Implement:

* Computer Agent
* node registration
* authentication
* command protocol
* WebSocket communication
* Windows node API contract

Prepare for Windows-Use integration.

---

## PHASE 9 — VOICE

Implement:

* STT
* TTS
* wake events
* streaming transcript
* voice sessions
* interruption handling

Prepare for ESP32.

---

## PHASE 10 — ESP32 / IOT

Implement:

* device registry
* device authentication
* ESP32 protocol
* events
* wake-word event handling
* device state

---

## PHASE 11 — AUTOMATION

Implement:

* scheduler
* event-triggered automation
* notifications
* persistent workflows

---

## PHASE 12 — OBSERVABILITY

Implement:

* metrics
* Prometheus
* optional Grafana
* agent monitoring
* task monitoring
* model usage
* resource monitoring

---

## PHASE 13 — PRODUCTION DEPLOYMENT

Implement:

* Docker Compose
* systemd
* Ubuntu deployment
* backup documentation
* environment configuration
* security hardening
* startup scripts
* health checks

---

# 52. GIT WORKFLOW

Never make destructive changes to main blindly.

Use:

```text
main
develop
feature/*
```

For major features:

```text
feature/core
feature/models
feature/coding-agent
feature/browser-agent
feature/memory
feature/voice
```

Before declaring a feature complete:

```text
format
 ↓
lint
 ↓
type check
 ↓
unit tests
 ↓
integration tests
 ↓
build
 ↓
health check
```

---

# 53. IMPORTANT DEVELOPMENT RULE

You are working inside a real repository.

Before modifying code:

1. inspect the repository
2. understand existing files
3. identify existing architecture
4. preserve useful work
5. avoid unnecessary rewrites
6. implement incrementally
7. run tests
8. verify results

Do not blindly generate an entirely new project if an existing implementation already exists.

---

# 54. DO NOT FAKE FEATURES

Never create fake implementations just to make the UI appear complete.

If a subsystem isn't implemented yet:

* create a proper interface
* create a safe stub/mock
* document the missing implementation
* add a test

Do not pretend an agent can control Windows if the computer node isn't connected.

Do not pretend Ollama is available if it isn't.

Do not report a task as successful unless verification confirms it.

---

# 55. DOCUMENTATION

Create:

```text
README.md

docs/
├── architecture.md
├── development.md
├── deployment.md
├── configuration.md
├── agents.md
├── tools.md
├── models.md
├── memory.md
├── security.md
├── computer-nodes.md
├── voice.md
├── esp32.md
├── troubleshooting.md
└── api.md
```

Include architecture diagrams using Mermaid where useful.

---

# 56. DEFINITION OF DONE

ULTRON server foundation is considered complete only when:

```text
✓ Server starts
✓ PostgreSQL connects
✓ Redis connects
✓ API works
✓ WebSocket works
✓ Health endpoint works
✓ Agent Manager works
✓ Multiple agents can run concurrently
✓ Task persistence works
✓ Event bus works
✓ Permissions work
✓ Tool registry works
✓ Ollama integration works
✓ Model Router works
✓ Coding Agent works
✓ OpenCode integration works
✓ Browser Agent works
✓ Research Agent works
✓ Memory works
✓ Verification works
✓ Logs work
✓ Configuration works
✓ Tests pass
✓ Docker deployment works
✓ Ubuntu deployment documented
```

Do not mark the entire project complete until the implemented phases have actually been tested.

---

# 57. HOW YOU SHOULD WORK

Operate as a senior autonomous engineer.

For each phase:

1. inspect
2. plan
3. implement
4. test
5. fix
6. verify
7. document
8. summarize

Do not ask for permission for every trivial coding action.

However, stop and request confirmation before:

* deleting significant existing work
* destructive database operations
* removing project history
* changing credentials
* performing irreversible system changes

Keep the repository clean.

Keep architecture modular.

Prefer boring reliable infrastructure over clever unnecessary abstractions.

---

# 58. FIRST ACTION

Start by inspecting the current repository.

Do NOT immediately write code.

Determine:

* current files
* current framework
* current package configuration
* existing code
* existing Git state
* operating system
* installed runtimes
* available services

Then produce a concise implementation assessment.

After assessment, begin **PHASE 1 — FOUNDATION**.

Build the foundation completely before moving to Phase 2.

At the end of each phase:

```text
PHASE STATUS
Implemented:
Tested:
Known limitations:
Files changed:
Commands executed:
Next phase:
```

Continue through the phases systematically.

The final goal is a complete, modular ULTRON server that can be developed on Windows, committed to Git, cloned onto Ubuntu Server, configured through environment variables, started through Docker/systemd, and eventually controlled from Windows, mobile, web, voice, and ESP32 clients.





ARCHiTECTURE DIAGRAMS AND DESIGN:

ULTRON — Complete End-to-End Architecture
╔══════════════════════════════════════════════════════════════════════╗
║                         ULTRON ECOSYSTEM                            ║
╚══════════════════════════════════════════════════════════════════════╝

          CLIENT / PHYSICAL LAYER
┌─────────────────────────────────────────────────────────────────────┐
│                                                                     │
│  Windows PC          Phone/Web          ESP32          Future Nodes  │
│      │                   │                │                │         │
│      ├── Orb             ├── App         ├── Wake word    ├── IoT   │
│      ├── Agent Windows   ├── Voice       ├── Mic          └── ...   │
│      └── Computer Node   └── Dashboard   └── Sensors                │
│                                                                     │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
                     HTTPS / WebSocket / MQTT
                                │
                                ▼
╔══════════════════════════════════════════════════════════════════════╗
║                         ULTRON SERVER                               ║
╠══════════════════════════════════════════════════════════════════════╣
║                                                                      ║
║                         API GATEWAY                                  ║
║                  FastAPI + WebSocket + Auth                          ║
║                              │                                       ║
║                              ▼                                       ║
║                     ┌──────────────────┐                             ║
║                     │   ULTRON CORE    │                             ║
║                     └────────┬─────────┘                             ║
║                              │                                       ║
║       ┌──────────────────────┼────────────────────────┐              ║
║       ▼                      ▼                        ▼              ║
║  Conversation           Intent / Router          Context Manager     ║
║  Manager                / Planner                                     ║
║                              │                                       ║
║                              ▼                                       ║
║                      TASK / WORKFLOW ENGINE                          ║
║                              │                                       ║
║                              ▼                                       ║
║                       AGENT MANAGER                                  ║
║                              │                                       ║
║        ┌─────────────┬───────┼────────┬─────────────┐                ║
║        ▼             ▼       ▼        ▼             ▼                ║
║     Coding       Research  Browser  Computer     System             ║
║      Agent        Agent     Agent     Agent       Agent              ║
║        │             │       │         │             │               ║
║        ▼             ▼       ▼         ▼             ▼               ║
║     OpenCode        Web    Playwright Windows      Server            ║
║                                      Node          Monitoring         ║
║                                                                      ║
║        ┌────────────┬────────────┬────────────┬────────────┐          ║
║        ▼            ▼            ▼            ▼            ▼          ║
║      File         Vision      Automation     IoT       Verification  ║
║      Agent         Agent        Agent       Agent         Agent       ║
║                                                                      ║
║                              │                                       ║
║                              ▼                                       ║
║                         MODEL ROUTER                                 ║
║                              │                                       ║
║              ┌───────────────┼────────────────┐                      ║
║              ▼               ▼                ▼                      ║
║           Ollama          OpenAI           Gemini                    ║
║                              │                                       ║
║                           Anthropic                                    ║
║                                                                      ║
║                              │                                       ║
║                              ▼                                       ║
║                         TOOL RUNTIME                                 ║
║                              │                                       ║
║     ┌──────────┬──────────┬──────────┬──────────┬────────────┐       ║
║     ▼          ▼          ▼          ▼          ▼            ▼       ║
║   Browser    Terminal   Filesystem   Git       Python      Web       ║
║     │          │          │          │          │            │       ║
║     └──────────┴──────────┴──────────┴──────────┴────────────┘       ║
║                              │                                       ║
║                       PERMISSION MANAGER                             ║
║                              │                                       ║
║                       VERIFICATION ENGINE                            ║
║                                                                      ║
║        ┌─────────────────────┼──────────────────────┐                ║
║        ▼                     ▼                      ▼                ║
║     MEMORY               EVENT BUS              SCHEDULER             ║
║        │                     │                      │                 ║
║        ▼                     ▼                      ▼                 ║
║ PostgreSQL + pgvector      Redis                Jobs/Triggers         ║
║                                                                      ║
║        ┌─────────────────────┼──────────────────────┐                ║
║        ▼                     ▼                      ▼                ║
║      VOICE                DEVICE GATEWAY        OBSERVABILITY         ║
║        │                     │                      │                 ║
║ faster-whisper          ESP32 / Nodes       Logs / Metrics            ║
║ Piper                   MQTT / WS           Prometheus                ║
║                                                                      ║
╚══════════════════════════════════════════════════════════════════════╝
1. The five fundamental layers

ULTRON should be split into five major layers:

┌──────────────────────────────┐
│ 1. INTERFACE LAYER           │
│ Orb / Web / Mobile / Voice   │
├──────────────────────────────┤
│ 2. ULTRON CORE               │
│ Orchestration / Tasks / Auth │
├──────────────────────────────┤
│ 3. INTELLIGENCE LAYER        │
│ Models / Agents / Memory     │
├──────────────────────────────┤
│ 4. EXECUTION LAYER           │
│ Tools / Browser / OS / IoT   │
├──────────────────────────────┤
│ 5. INFRASTRUCTURE LAYER      │
│ DB / Redis / Docker / Linux  │
└──────────────────────────────┘

The crucial rule is:

The UI never becomes the brain, and the LLM never becomes the operating system.

2. Interface layer

These are clients.

They don't contain the main intelligence.

Windows Desktop Node
ULTRON Desktop
├── Floating Orb
├── Agent Windows
├── Voice interface
├── Notifications
└── Computer-control node

The orb behaves like the Gemini/Google-style assistant trigger you described:

ESP32 wake
     ↓
ULTRON server
     ↓
WAKE_DETECTED
     ↓
Windows Orb appears
     ↓
Listening
     ↓
Transcript

The orb is not responsible for inference.

3. Agent windows

This is your specific requirement.

Each agent gets its own standalone window.

Windows
│
├── ULTRON Orb
│
├── Coding Agent #01
│
├── Research Agent #02
│
├── Browser Agent #03
│
└── System Agent #04

But the computation happens on the server:

Windows Agent Window
        │
        │ WebSocket
        ▼
Ubuntu Server
        │
        ▼
Agent Instance

So an agent window is essentially a remote visual client for an agent instance.

4. API Gateway

The server's front door.

Internet/LAN
     │
     ▼
Reverse Proxy
     │
     ▼
FastAPI
     │
 ┌───┴───────────┐
 ▼               ▼
REST           WebSocket

REST handles:

authentication
configuration
tasks
agents
projects
memory
devices

WebSocket handles:

live agent status
transcripts
streaming responses
tool activity
notifications
orb events
task progress
5. ULTRON Core

This is the actual brain of the platform.

ULTRON CORE
│
├── Conversation Manager
├── Context Manager
├── Intent Router
├── Planner
├── Orchestrator
├── Task Manager
├── Agent Manager
├── Tool Router
├── Permission Manager
├── Event Bus
├── Scheduler
└── Verification Engine

Example:

"Check my CampusCare project and fix the login bug."

Core does:

Understand
   ↓
Identify coding task
   ↓
Create task
   ↓
Create Coding Agent
   ↓
Select model
   ↓
Assign workspace
   ↓
Give agent OpenCode
   ↓
Monitor
   ↓
Test
   ↓
Verify
   ↓
Report
6. Agent system

The agent system is intentionally modular.

                    Agent Manager
                         │
       ┌─────────────────┼─────────────────┐
       │                 │                 │
       ▼                 ▼                 ▼
     Coding           Research          Browser
       │                 │                 │
    OpenCode             Web           Playwright

Additional agents:

Core Agent
Coding Agent
Research Agent
Browser Agent
Computer Agent
System Agent
Vision Agent
Automation Agent
File Agent
IoT Agent
Verification Agent

Agents can be:

persistent
temporary
parallel
delegated
paused
resumed
cancelled
7. Coding Agent

Your OpenCode integration sits here.

Coding Agent
      │
      ▼
Coding Engine Interface
      │
      ▼
OpenCode Adapter
      │
      ▼
OpenCode CLI
      │
 ┌────┼─────┐
 ▼    ▼     ▼
Files Git Terminal

This means you can eventually replace OpenCode without rebuilding ULTRON.

Coding Engine
├── OpenCode
├── Aider
├── Claude Code
└── Future engine
8. Model layer

Models are replaceable reasoning engines.

                    MODEL ROUTER
                         │
        ┌────────────────┼─────────────────┐
        ▼                ▼                 ▼
      Local            Cloud             Cloud
        │                │                 │
     Ollama           OpenAI            Gemini
                         │
                      Anthropic

The router decides based on:

task
capability
speed
availability
configured preference
resource usage

Example:

Simple command → fast local model

Coding → coding-capable model

Deep reasoning → reasoning model

Vision → vision model
9. Tool runtime

This is one of the most important parts.

The LLM does not directly control Linux.

Instead:

LLM
 ↓
Tool request
 ↓
Tool Router
 ↓
Permission Manager
 ↓
Tool Executor
 ↓
Verification
 ↓
Result

Tools:

Browser
Terminal
Filesystem
Git
Python
Web
System
Computer
Notifications
IoT
10. Permission architecture

Every tool has a permission level.

LEVEL 0
Read

LEVEL 1
Safe actions

LEVEL 2
Project modification

LEVEL 3
Program execution

LEVEL 4
System configuration

LEVEL 5
Destructive actions

This becomes especially important when you let ULTRON control your Windows computer.

11. Computer-control architecture

Don't put Windows-Use directly inside Core.

Use:

Computer Agent
       ↓
Computer Node Protocol
       ↓
Windows Desktop Node
       ↓
Windows-Use
       ↓
Windows

And use this hierarchy:

Application API
      ↓
Browser DOM / Playwright
      ↓
Windows UI Automation
      ↓
Keyboard shortcuts
      ↓
Mouse automation
      ↓
Vision

That keeps GUI automation as a fallback instead of making it the foundation.

12. Browser architecture

Browser Agent:

Browser Agent
      ↓
Browser Tool
      ↓
Playwright
      ↓
Browser Context
      ↓
Website

Each agent gets its own browser context.

This allows:

Research Agent → Browser #1

Shopping Agent → Browser #2

Automation Agent → Browser #3

without mixing sessions.

13. Memory architecture

Memory is separate from the database concept.

                  MEMORY
                    │
      ┌─────────────┼─────────────┐
      ▼             ▼             ▼
 Short-term      Long-term      Episodic
      │             │             │
      └─────────────┼─────────────┘
                    ▼
                 Semantic
                    │
                    ▼
              Project Memory

Storage:

PostgreSQL
     +
pgvector
     +
Redis

Example namespaces:

user
project:ultron
project:campuscare
agent:coding
agent:research
14. Task engine

Tasks are more important than individual conversations.

                    TASK
                      │
                  Task Graph
                      │
       ┌──────────────┼──────────────┐
       ▼              ▼              ▼
   Research       Implementation   Testing
       │              │              │
       └──────────────┼──────────────┘
                      ▼
                  Verification

This allows genuinely agentic workflows instead of:

message → answer

ULTRON becomes:

goal → plan → execute → observe → verify → adapt → complete
15. Event bus

Everything important generates events.

USER_MESSAGE
WAKE_DETECTED
TASK_CREATED
TASK_STARTED
AGENT_CREATED
AGENT_STARTED
TOOL_STARTED
TOOL_COMPLETED
MODEL_REQUEST
MODEL_RESPONSE
BUILD_FAILED
TEST_FAILED
TASK_COMPLETED
DEVICE_CONNECTED
DEVICE_OFFLINE
DISK_LOW
GPU_HIGH

Event flow:

Agent
 ↓
Event Bus
 ↓
Subscribers
 ├── UI
 ├── Logger
 ├── Memory
 ├── Notifications
 ├── Scheduler
 └── Monitoring

This is what makes ULTRON feel like a living system rather than a collection of scripts.

16. Voice architecture
ESP32
 │
 ├── Microphone
 └── Wake word
       │
       ▼
   Device Gateway
       │
       ▼
       STT
       │
       ▼
   ULTRON Core
       │
       ▼
     Agent
       │
       ▼
      TTS
       │
       ▼
ESP32 / Windows / Phone

Initial local stack:

STT → faster-whisper
TTS → Piper

Later cloud providers can be added behind adapters.

17. ESP32 architecture

ESP32 isn't part of the AI brain.

It's a physical node.

                    ULTRON SERVER
                         │
                   Device Gateway
                         │
             ┌───────────┼───────────┐
             ▼           ▼           ▼
          ESP32 #1    ESP32 #2    ESP32 #3
             │
          Sensors
          Mic
          Speaker
          Buttons

Each device has:

device_id
capabilities
status
last_seen
firmware
authentication
18. Scheduler

ULTRON can run tasks without the user talking to it.

Scheduler
   │
   ├── one-time
   ├── recurring
   ├── delayed
   └── event-triggered

Example:

Every morning
     ↓
Create Research Agent
     ↓
Research topic
     ↓
Summarize
     ↓
Notify user
19. Verification

This deserves its own subsystem.

Agent says:
"Done."

        ↓

Verification Engine
        │
   ┌────┼────┐
   ▼    ▼    ▼
Tests Files State
   │    │    │
   └────┼────┘
        ▼
 VERIFIED

ULTRON should never blindly trust an agent's statement that something worked.

20. Database architecture

PostgreSQL becomes the persistent source of truth.

PostgreSQL
│
├── users
├── sessions
├── conversations
├── messages
├── agents
├── tasks
├── task_steps
├── tool_executions
├── events
├── memories
├── projects
├── devices
├── schedules
├── audit_logs
└── model_usage

Redis handles temporary/high-speed state:

Redis
├── queues
├── cache
├── pub/sub
├── locks
└── transient state
21. Infrastructure

Your Ubuntu machine eventually looks like:

Ubuntu Server
│
├── Docker
│
├── ULTRON API
│
├── ULTRON Workers
│
├── PostgreSQL
│
├── Redis
│
├── Ollama
│
├── Playwright
│
├── Prometheus
│
└── Grafana

Persistent data:

/opt/ultron/
├── server/
├── data/
├── workspaces/
├── logs/
└── config/
22. Development → Production

Your exact workflow:

              DEVELOPMENT
┌──────────────────────────────┐
│ Windows                      │
│                              │
│ VS Code                      │
│ OpenCode CLI                 │
│ Git                          │
│ Python                       │
│ Docker                       │
└──────────────┬───────────────┘
               │
             Git
               │
               ▼
          GitHub Repo
               │
             clone
               │
               ▼
             Ubuntu
┌──────────────────────────────┐
│ ULTRON SERVER                │
│                              │
│ Docker Compose               │
│ PostgreSQL                   │
│ Redis                        │
│ Ollama                       │
│ ULTRON Core                  │
└──────────────────────────────┘

So Windows is your development workstation, not the final ULTRON brain.

23. Complete runtime flow

A normal voice request:

User
 │
 │ "ULTRON, check my project"
 ▼
ESP32
 │
 │ wake word
 ▼
Device Gateway
 │
 ▼
STT
 │
 ▼
ULTRON Core
 │
 ├── Context
 ├── Intent
 ├── Planning
 └── Task creation
          │
          ▼
     Agent Manager
          │
          ▼
     Coding Agent
          │
          ▼
      OpenCode
          │
     ┌────┼────┐
     ▼    ▼    ▼
    Git Files Tests
     │    │    │
     └────┼────┘
          ▼
      Verification
          │
          ▼
        Memory
          │
          ▼
       Event Bus
          │
          ▼
         TTS
          │
          ▼
        User
24. Multi-agent flow

For a complex request:

"Research the latest technology, compare it with our project, implement the change and test it."

ULTRON can create:

                    ULTRON
                       │
                    TASK #42
                       │
          ┌────────────┼────────────┐
          ▼            ▼            ▼
      Research      Coding       Testing
       Agent        Agent         Agent
          │            │            │
        Web         OpenCode      Tools
          │            │            │
          └────────────┼────────────┘
                       ▼
                  Verification
                       │
                       ▼
                     Result

All three can run on one Ubuntu server.

25. Final physical architecture

Eventually your setup becomes:

                         INTERNET / LAN
                               │
            ┌──────────────────┼──────────────────┐
            │                  │                  │
         PHONE              WINDOWS             ESP32
            │                COMPUTER              │
            │                  │                  │
            │             Orb + Agent UI       Mic/Sensors
            │                  │                  │
            └──────────────────┼──────────────────┘
                               │
                         Secure Connection
                               │
                               ▼
╔══════════════════════════════════════════════════════════╗
║                    ULTRON SERVER                         ║
║                                                          ║
║  API Gateway                                             ║
║       │                                                  ║
║  ULTRON CORE                                             ║
║       │                                                  ║
║  Agent Manager                                           ║
║       │                                                  ║
║  ┌────┼────┬──────┬──────┬──────┬──────┐               ║
║  │    │    │      │      │      │      │               ║
║ Code Research Browser Computer System Voice IoT         ║
║  │    │    │      │      │      │      │               ║
║ Open Web Playwright Node   Linux   STT   ESP32          ║
║ Code  Tools        Tools   Tools   TTS   Gateway        ║
║                                                          ║
║                 MODEL ROUTER                             ║
║             /       |       \                            ║
║        Ollama    OpenAI    Gemini/Anthropic              ║
║                                                          ║
║                 TOOL RUNTIME                             ║
║                      │                                   ║
║            Permission + Verification                     ║
║                                                          ║
║              MEMORY / TASKS / EVENTS                     ║
║                      │                                   ║
║            PostgreSQL + pgvector + Redis                 ║
║                                                          ║
║              SCHEDULER / NOTIFICATIONS                   ║
║                                                          ║
║              MONITORING / LOGGING                        ║
║                                                          ║
╚══════════════════════════════════════════════════════════╝
The core idea

The cleanest mental model for the whole project is:

                  ULTRON
                     │
             ┌───────┴────────┐
             │                │
          THINKS           ACTS
             │                │
          Models            Tools
             │                │
          Agents          Execution
             │                │
             └───────┬────────┘
                     │
                  CORE
                     │
          ┌──────────┼──────────┐
          ▼          ▼          ▼
       Memory      Tasks      Events
          │          │          │
          └──────────┼──────────┘
                     │
                 Interfaces
                     │
       ┌─────────────┼─────────────┐
       ▼             ▼             ▼
      Orb          Phone          ESP32


==========================================================================
# 59. CAPABILITY EXTENSIONS (ADDITIVE)
==========================================================================

This section extends the specification above. It does not replace any of it.

Everything in sections 1-58 remains in force. Where this section restates a
requirement that already exists, it is listed in the extension map (59.1) as
**extend**, not **new**, and the original section stays authoritative. The
build log is `tasks.md`; extension tasks are T220+.

## 59.0 NON-DESTRUCTIVE INTEGRATION RULE

This is the rule the rest of section 59 depends on.

The project already exists and large parts of it are built. Treat sections 1-58
and the current repository as the source of truth.

```text
DO NOT
  - replace, restart, or redesign the project from scratch
  - create a second agent runtime, tool registry, model router or database
  - re-open a decision already recorded in the tasks.md Decision log
  - mark a task done because a capability is described, not because it works

DO
  - inspect the repository and architecture before implementing anything
  - check whether the capability already exists, and extend it if so
  - preserve compatible existing components
  - modify an existing component only where integration requires it
  - record every deviation from sections 1-58 in the Decision log
```

Two failure modes this exists to prevent, both of which have already been paid
for once in this codebase:

- **A parallel implementation.** A second tool registry that does not enforce
  the permission layer is not a partial success; it is a hole with the same
  reach as the real one. Everything goes through `app/tools/` (§14, §16).
- **A specification that disagrees with the code.** Every capability below must
  end with the code, the test and the honest status line. §54 and §56 apply
  unchanged.

## 59.1 EXTENSION MAP

Read this first. Most of the requested surface is already specified; the right
column is the actual work.

| Requested capability | Existing section | Verdict | Extension work |
|---|---|---|---|
| Coding agent | §8, §9 | extend | free-model routing for cloud inference (§59.2) |
| Git tools | §46, §14 | extend | three-tier permission levels (§59.3) |
| Filesystem layer | §14, §15, §17 | extend | enforced sandbox layering (§59.4) |
| Server management agent | §12 | extend | diagnostic allow-list (§59.5) |
| Unified tool registry | §14, §16 | extend | required registry fields, categories (§59.6) |
| OpenRouter provider | §3, §20, §21 | **new** | free-models-only adapter (§59.7) |
| Model capability registry | §20, §49 | **new** | capability + free/paid fields (§59.8) |
| Model fallback chain | §49 | **new** | failure taxonomy, no endless retry (§59.9) |
| Research agent | §10 | extend | citation tracking (§59.10) |
| Decision-support agent | -- | **new** | neutrality contract (§59.11) |
| Health-information agent | -- | **new** | informational, never diagnostic (§59.12) |
| Floating orb UI | §27, §28 | extend | visual state set, gestures (§59.13) |
| Desktop client | §43, §13 | extend | orb as part of the client (§59.14) |
| Windows computer control | §42, §13 | extend | permission-gated bridge (§59.15) |
| Voice via LiveKit | §25 | **new** | LiveKit as transport (§59.16) |
| Local STT | §25 | extend | utterance-boundary requirement (§59.17) |
| Local TTS | §25 | extend | engine-swappable interface (§59.18) |
| Voice streaming / barge-in | §25 | **new** | barge-in, cancellation (§59.19) |
| Local-first voice fallback | §21, §25 | **new** | offline voice path (§59.20) |
| Unified agent architecture | §6, §7 | extend | routing topology (§59.21) |
| Agent registry | §7, §43 | extend | new agent types (§59.22) |
| Scoped context | §22 | **new** | agents do not receive all memory (§59.23) |
| Observability fields | §32 | **new** | authorization level, token usage (§59.24) |
| 8 GB RAM budget | §48 | reaffirm | load-shedding order (§59.25) |

## 59.2 CODING AGENT

Extends §8 and §9.

The coding agent already exists in the spec: read a repository, understand
architecture, create and edit files, refactor, debug, run tests, linters and
builds, plan, implement features, review its own changes, use Git, branch,
commit, review diffs, revert when authorized, and work across repositories.

What is added: when cloud inference is selected for a coding task, the model
comes from the model registry (§59.8) rather than being hard-coded, and it must
be a **free** OpenRouter model when the OpenRouter provider is in use (§59.7).

Agent code must never contain a model name. Changing the coding model is a
configuration change (§59.8), not a code change.

## 59.3 GIT TOOLS AND PERMISSION LEVELS

Extends §46 and uses §14-§17.

Git operations are exposed as **structured tools**, one per operation. The tool
runtime must not be able to reach a general shell to do this (§59.4).

Operations in scope:

```text
status diff log branch create-branch switch commit inspect-commit
compare-branches merge revert stash remotes pull push
```

Every Git tool declares one of three permission levels. These are ULTRON
permission levels (§15), not a second parallel scheme.

```text
SAFE                    read-only, runs without confirmation
  status, diff, log, branch listing, show, blame, remote listing

CONTROLLED              mutates the working tree; confirm unless pre-authorized
  create branch, switch branch, commit, pull, stash

EXPLICIT AUTHORIZATION  refused unless the request names the operation and the
                        user authorizes this specific invocation
  push, force push, reset --hard, clean -fd, branch -D, rebase, filter-branch,
  any operation that can discard unreferenced work
```

`push` and `force-push` are the reason this table exists. A coding agent that
can push without asking is an agent that can overwrite shared history on a
machine the user also works on by hand.

The classification belongs in the tool's declared permission level, so
enforcement is the existing pipeline (§16) rather than a check inside the
handler. A tool cannot raise its own privilege at runtime.

## 59.4 FILESYSTEM LAYER

Extends §14, §15 and §17.

Filesystem access is a permission-aware layer, not an agent capability:

```text
Agent
  |
Tool Router            (registry lookup, schema validation -- §16)
  |
Permission Layer       (permission level + workspace policy -- §15)
  |
Filesystem             (the only component that touches a path)
```

Capabilities: read, write, list, stat, search, glob, move, copy, delete, and
metadata. File watching and indexing are tools in their own right (§59.6), not
side effects of the read tool.

**The boundary is mandatory.** `WorkspaceSettings.allowed_paths()` (§34, §48)
is the authority. Every path is resolved to a real path -- symlinks followed,
`..` collapsed -- *before* the allow-list test, because a boundary checked
against the unresolved string is not a boundary. A path outside the workspace is
refused by the layer that holds the path, not by the caller.

Do not give every agent unrestricted filesystem access. An agent that needs a
path outside its workspace gets an explicit grant for that path, recorded and
revocable, in the same way a permission level is.

Deletion and any write outside the assigned workspace are at least CONTROLLED.
Deletion is irreversible and is not undoable by the agent.

## 59.5 SERVER MANAGEMENT AGENT

Extends §12 (System Agent).

The system agent inspects and manages the Ubuntu host. In scope:

```text
cpu, ram, disk, temperature   where the sensor exists
processes, services           inspect, start, stop, restart
logs                          inspect
ports, network, uptime, storage
docker                        container status where Docker is installed
diagnostics                   bounded, read-only by default
```

Layering, matching §59.4:

```text
Server Management Agent
  |
System Tool Layer             (one tool per operation)
  |
Permission / Safety Layer     (§15)
  |
Ubuntu Server
```

Rules:

- Inspection is SAFE. Mutating operations are CONTROLLED at minimum.
- **Restarting or stopping a service is EXPLICIT AUTHORIZATION.** Taking down
  PostgreSQL, Redis or ULTRON itself is a self-inflicted outage, and the user
  may be on the other end of the connection that did it.
- `deploy approved service` means acting on an already-approved deployment
  definition. It is not a general install path and must not accept an arbitrary
  image, command or script.
- **No arbitrary shell.** Each diagnostic is a tool that returns structured
  data. `free` is a tool, not `sh -c free`.
- Every system tool declares a timeout. A diagnostic that hangs is itself a
  fault, and an unbounded read of `/var/log` can exhaust memory on an 8 GB host
  (§48, §59.25).

## 59.6 UNIFIED TOOL REGISTRY

Extends §14 and §16.

One registry, reached through one router, for every tool in the system. This is
what lets a future agent gain capabilities without reimplementing them.

Categories:

```text
filesystem git terminal process network server docker browser
search database http notifications voice audio display automation
```

Every registered tool has:

| Field | Purpose |
|---|---|
| `name` | unique, stable, used in events and audit rows |
| `description` | what it does; what the agent reads to choose it |
| `input_schema` | validated before execution (§16 stage 1) |
| `permission_level` | §15 level, enforced by the pipeline (§59.3, §59.4) |
| `execute` | the handler |
| `verify` | post-execution check (§17) |
| `timeout` | bounded, always declared |
| `logging` | what is recorded, without content that must not be |
| `error handling` | maps to a typed error from `app/core/errors.py` |
| `audit` | whether the call is an audited sensitive operation (§15) |

Agents request tools through the Tool Router. An agent does not import a tool
module and call it directly; a direct call would bypass schema validation,
permission checks and verification, which is the entire value of the pipeline.

> **Amended after §65 (added with §66):** §66.14 adds registry fields this
> section's pipeline increasingly needs: `output_schema`, `node_requirements`
> (which §64.10 capability the target node must advertise), `risk_level`,
> `availability`, `version`, and `reversibility` (§66.17). The interface,
> validation-first ordering and Tool Router rule above are unchanged; tools
> declare, the pipeline decides.

## 59.7 OPENROUTER PROVIDER — FREE MODELS ONLY

Extends §3 (External AI), §20 (Model Router) and §21 (Ollama). **New provider.**

```text
LLM Provider
   |
   +-- Ollama          local, §21
   +-- OpenRouter      cloud, free models only, this section
   +-- OpenAI / Gemini / Anthropic   optional, §3
```

**The OpenRouter integration is free-models-only by design.** The system must
not be built around paid inference, and no code path may silently spend money.
Concretely:

- The adapter rejects a configured model that is not free, at configuration load
  time, with a `ConfigError`. Failing at startup is the only outcome that gets
  noticed.
- `ModelProviderCosts` records free/paid status per model (§59.8). A paid model
  is visible and refused, not merely unused.
- A free-tier rate limit (HTTP 429) is an expected condition, not an outage.
  It is handled by §59.9, which is why the free-only rule is workable rather
  than aspirational.
- No OpenRouter API key is required for local development, consistent with §3.

The agent must not depend on OpenRouter. The dependency is:

```text
Agent
  |
LLM Router            (§20, §49)
  |
Model Registry        (§59.8)
  |
Provider              (Ollama | OpenRouter | ...)
  |
Model
```

## 59.8 MODEL CAPABILITY REGISTRY

Extends §20 and §49. **New.**

Model names must never be hard-coded (§20). This is stronger than it looks:
OpenRouter's free model set changes over time, so a hard-coded name is wrong
before it is merely inflexible.

Per-model record:

```text
model_id                provider-qualified identifier
provider
context_length
supports_tools
supports_reasoning
supports_coding
supports_vision
approx_resource_requirement    memory / cpu class, for local models
availability            discovered health, not a guess
cost_class              free | paid
```

Two properties matter:

- **Routing is by capability, not by name.** §20's `select(capability=...)`
  queries this registry. Agent configuration names a capability, not a model.
- **Availability is discovered.** `availability` is set by a probe, so a
  delisted free model degrades to the next candidate instead of failing the
  request (§59.9).

Conceptual agent configuration -- illustrative, not a literal schema:

```yaml
agents:
  coding:            { capability: coding }
  research:          { capability: research }
  reasoning:         { capability: reasoning }
  decision_support:  { capability: reasoning }
  general:           { capability: general }
  local_fallback:    { capability: general, provider: ollama }
```

Changing what an agent uses is a configuration change. Editing agent code to
change a model is a defect.

## 59.9 MODEL FALLBACK AND FAILURE TAXONOMY

Extends §49. **New.**

```text
primary free OpenRouter model
        |  timeout | rate limit | provider error | model unavailable
        |  invalid response | context overflow
        v
secondary free OpenRouter model
        |  same taxonomy
        v
local Ollama model
```

Requirements:

- **Classify the failure, do not just count attempts.** The six causes above are
  not interchangeable. A context overflow is not fixed by retrying on a
  different model of the same size; a rate limit is not fixed by a shorter
  timeout. The classifier maps a provider error to the typed errors already
  declared in `app/core/errors.py` -- `ModelTimeout`, `ModelRateLimited`,
  `ModelUnavailable`, `ModelResponseInvalid`, plus `ProviderError` -- and the
  fallback chain keys off those types rather than off a catch-all.
- **Bounded retries with exponential backoff.** Never infinite. A request that
  has exhausted its chain returns a typed error and does not keep trying.
- **Context overflow is not a retry.** It is reported, with the limit that was
  exceeded, so the caller can act on it.
- **The fallback chain is configurable per capability** and is recorded in
  `model_usage` (T067) with the reason for each hop, so "why did this use
  Ollama" is answerable after the fact.
- Degrading to a local model is a **visible** state, surfaced to the user and in
  the observability record (§59.24). A silently weaker answer is worse than an
  error.

## 59.10 RESEARCH AGENT

Extends §10.

Adds explicit provenance requirements to the existing research capability:

```text
Research Agent
    |
Search
    |
Source collection        (every source recorded with URL and fetch time)
    |
Extraction               (per-source, before any reasoning)
    |
Reasoning model
    |
Fact / Summary layer     (each claim bound to the source it came from)
    |
Cited result
```

- Every factual claim in the output carries a citation to a collected source.
- A claim that no collected source supports is either omitted or marked
  unsupported. It is never presented as established.
- Model-generated text is **not** treated as a source. The research agent does
  not cite itself.
- Extraction happens per source, before synthesis. Summarising a whole result
  page at once is what produces claims that appear in none of the documents.
- Reports are structured and stored as a first-class artifact, so a later review
  can distinguish what was retrieved from what was concluded.

The research agent uses a free OpenRouter model when the router selects one
(§59.7); it does not name a model.

## 59.11 DECISION-SUPPORT AGENT

**New.** Not previously specified.

A neutral agent that structures a decision instead of making it:

```text
options -> criteria -> documented facts -> tradeoffs -> unknowns
        -> missing constraints -> alternatives
```

Its job is to identify options, extract criteria, compare documented facts,
surface tradeoffs, name what is unknown, ask for the constraints it is missing,
and present alternatives with their costs.

**It must not make the decision.** Specifically:

- It presents evidence and tradeoffs; it does not resolve them silently.
- Where a decision is consequential or safety-relevant, it says which facts are
  missing and what the tradeoffs are, rather than producing a recommendation
  that reads as settled.
- Any recommendation it does offer is labelled as one, with the reasoning and
  the uncertainty exposed, so the user can disagree with a specific claim rather
  than with an unexplained verdict.
- It never presents a decision it did not make as the user's.

This is a behavioural contract and therefore testable: the test suite asserts
that output is structured as options/criteria/tradeoffs/unknowns, and that a
missing-constraint case asks rather than assumes.

## 59.12 HEALTH INFORMATION AGENT

**New.** Not previously specified. Note this is unrelated to `HealthService`
(§18), which is ULTRON's own liveness/readiness reporting.

An **informational** system about health and medicine. It is explicitly not a
diagnostician and not a clinician.

May:

```text
explain medical terminology
summarise reliable, referenced health information
explain general symptoms and the categories they commonly fall into
help organise information to take to a doctor
summarise medical documents the user supplied
explain general medication information
identify when professional evaluation may be appropriate
```

Must not:

```text
present itself as a doctor
diagnose, or state a diagnosis with certainty
replace or defer to professional care in place of it
give dangerous or specific treatment instructions
make autonomous high-stakes medical decisions
```

Required behaviour:

- **Safety boundaries are structural, not a prompt instruction.** The
  high-stakes categories are refused by the tool/agent layer, so the boundary
  holds regardless of what the model was asked. A prompt is a preference; a
  refusal at the layer is a guarantee.
- Emergency and crisis content routes to appropriate help immediately, ahead of
  any model call.
- Symptom and medication output is informational framing ("this can be
  associated with..."), never diagnostic framing ("you have...").
- Model routing uses a capable model (§59.8) and the local fallback applies
  (§59.20) -- but if no capable model is available, the honest answer is a
  refusal to answer, not a weaker guess.
- Every informational response names its limits and points to a professional.

## 59.13 ULTRON FLOATING ORB UI

Extends §27 (desktop orb backend support) and §28 (agent window protocol).

The orb is the primary quick-interaction surface:

```text
        +-------------+
        | ULTRON ORB  |
        +-------------+
               |
     Voice / Text / Agent
               |
          ULTRON CORE
```

Visual states, communicated by animation so that state is legible without
reading text:

```text
IDLE  LISTENING  THINKING  SPEAKING  PROCESSING  ERROR  NOTIFICATION
```

- Orb events travel over the existing agent-window protocol (§28) and the orb
  reflects state from the server rather than predicting it. An orb that
  animates "thinking" because it guessed is worse than one that shows nothing.
- `ERROR` and `NOTIFICATION` are visually distinct from `IDLE`, because the
  difference between "nothing is happening" and "something needs you" is the
  whole reason a persistent surface is worth having.

Gestures:

| Gesture | Action |
|---|---|
| click | open ULTRON |
| hold | voice input |
| double click | configurable quick action |
| right click | quick tools |
| drag | reposition |

- The quick action and the quick-tool list are **configuration**, per §34, not
  compiled-in behaviour.
- The orb stays lightweight: it must remain visible and responsive while the
  user works in other applications, and must not become a memory or CPU
  liability on an 8 GB host (§59.25).

## 59.14 ULTRON DESKTOP CLIENT

Extends §43 and §13.

The orb is one part of a lightweight Windows client that talks to the Ubuntu
server over the local network:

```text
Windows ULTRON Client
        |
        +-- Floating Orb          (§59.13)
        +-- Voice Interface       (§59.16 - §59.20)
        +-- Notifications         (§45)
        +-- Desktop Integration
        +-- Local Computer Tools  (§59.15)
                 |
                 v
          ULTRON Ubuntu Server
                 |
        +--------+--------+
        |                 |
      Agents            Tools
```

The client is a **client**. It holds no agent logic and no model access; it
renders state and forwards intent. This is what keeps the server authoritative
for permissions (§15) -- a permission decision made on the client is a permission
decision nobody audited.

> **Amended after §63 (added with §64):** the Windows client is a client **and**
> a node (§64.3). Its `Local Computer Tools` box above becomes the Windows
> node's registered capability set (§64.6.2): the client still holds no agent
> logic and no model access, but it *does* expose an authenticated local
> runtime that the server routes tool calls to (§64.11). Permission decisions
> remain server-side (§64.12); the node executes what it is told, after
> re-validating the call locally - it never decides policy itself.

## 59.15 WINDOWS COMPUTER CONTROL BRIDGE

Extends §42 (future computer control) and §13 (Computer Agent).

Capabilities the Windows client will eventually expose as tools:

```text
launch application            read active window
keyboard / mouse automation   screenshot
clipboard                     filesystem access
browser automation            process management
notifications
```

```text
ULTRON Server
      |
Windows Tool API             (§29 - a real API, not an inbound socket)
      |
Permission Layer             (§15)
      |
Windows Client
      |
Windows OS
```

Rules, and they are the reason this is separate from §59.14:

- **Every capability is permission-controlled.** Not "the node is trusted, so it
  is allowed". Trusted means authenticated and authorised per request.
- **No unrestricted remote control is exposed to any agent.** An agent that can
  automate the mouse can click anything the user can, including the permission
  dialog. If an agent can drive the desktop, the desktop is the trust boundary,
  and §15 must be enforced before the request leaves the server.
- The bridge is an **outbound** connection from the client to the server. The
  server does not accept inbound connections on the LAN, which is also what
  makes §31's requirement that ULTRON expose no public database or Redis
  hold on the desktop side.
- Screenshot and clipboard are **high-sensitivity** and are audited as such.
  They capture whatever is on screen, including whatever else is secret.
- Each capability is separately enable-able, so a deployment can offer
  notifications without offering keystrokes.

## 59.16 VOICE - LIVEKIT TRANSPORT

Extends §25. **LiveKit as the real-time transport, cloud-hosted.**

LiveKit is **cloud-hosted** (LiveKit Cloud). It is the transport and
orchestration layer for voice sessions, not the STT or TTS engine. ULTRON also
**supports** self-hosting LiveKit - the client speaks the LiveKit protocol, so a
self-hosted deployment is a configuration change rather than a rewrite - but
self-hosting is **deferred**: the development laptop is 8 GB and the server VM
is 4 GB, and a self-hosted media server is the wrong thing to run on either.
No LocalStack, no self-hosted LiveKit server, no bundled media service.

> **Corrected after the first draft of §59.** This section originally read
> "LiveKit is **self-hosted**", which contradicted both the stated hardware and
> the online-first policy. Self-hosting is still supported and still has tasks
> (T254); cloud hosting is the default.

```text
Microphone
   |
LiveKit            cloud-hosted transport, room/session orchestration
   |
Voice Agent
   |
STT                online provider, §59.17
   |
LLM / Agent        §20, §59.8
   |
TTS                online provider, §59.18
   |
LiveKit
   |
Speaker
```

Constraints on this choice:

- **Self-hosted LiveKit is a service with a memory cost.** It is budgeted
  explicitly in §59.25 and is one of the first things to shed under pressure,
  because §59.20's offline path needs no transport at all.
- LiveKit's own auth is a *transport* concern. It is **not** ULTRON
  authentication (§30) and must not be a way around it: a room token is not an
  ULTRON credential, and a voice session still resolves to an authenticated
  principal before any agent runs.
- The voice subsystem stays provider-independent (§25). LiveKit is the
  transport, replaceable, behind the existing voice interface.

## 59.17 LOCAL STT

Extends §25. Hardware target: Intel i5-1235U, 8 GB RAM, integrated graphics,
Ubuntu Server. **No GPU acceleration assumed.**

```text
Whisper / faster-whisper, smallest model with acceptable accuracy and latency
```

Design for this hardware:

- Smallest viable model. On an integrated-GPU-free i5, model size is the
  dominant latency term.
- No GPU assumption anywhere: no CUDA path is required for correctness, and
  nothing may depend on one.
- Chunked/streaming transcription where practical; partial results, final
  results, voice activity detection, silence detection.

**Utterance-boundary requirement.** This is a specific defect to design against,
so it is stated as a requirement rather than left to implementation:

> A pause must not cause the preceding audio to be retransmitted as a new
> utterance.

The failure mode is specific: without an explicit utterance boundary, a
silence timeout closes the chunk, and the next chunk begins overlapping the
previous audio. The result is duplicated or re-transcribed sentences -- the same
content billed and processed twice, and a transcript that disagrees with what
the user said.

Therefore the STT layer must:

- Maintain explicit utterance boundaries and an overlap policy, so chunk
  boundaries are not utterance boundaries.
- Deduplicate overlap, and make the policy testable in isolation.
- Distinguish a pause from an end-of-turn by VAD plus a silence threshold
  (§59.19), not by a chunk boundary.
- Report a partial result that can be *corrected* by later audio, and a final
  result that will not change.

This is testable with recorded audio fixtures: assert that a fixture containing
a mid-sentence pause produces one utterance with no duplicated text.

## 59.18 LOCAL TTS

Extends §25. **Piper is the primary local engine.**

Priority order, and the order is a requirement:

```text
1 low latency        2 low RAM usage     3 CPU compatibility
4 adequate naturalness  5 offline operation  6 chunked playback where possible
```

```text
TTS Interface
   +-- Piper                current
   +-- future local engines
   +-- future cloud engines
```

- The voice agent must not be coupled to Piper. Synthesis is behind the
  interface, and engine selection is configuration.
- Sentence-level synthesis, so playback can begin before the full response is
  generated (§59.19).
- Voice choice is configuration, not a code change.

## 59.19 VOICE PIPELINE AND BARGE-IN

Extends §25. **New: interruption handling.**

Target pipeline, streaming at every stage that permits it:

```text
Mic -> VAD -> LiveKit -> streaming STT -> agent/LLM -> streaming TTS -> output
```

- **Never wait for a complete conversation before processing.** Where a stage
  can stream, it must.
- **Barge-in is required.** When the user starts speaking:
  - stop TTS output immediately,
  - cancel the in-flight response,
  - discard the queued synthesis,
  - return to listening.
- Cancellation must actually cancel. A cancelled synthesis that continues to
  fill a buffer is worse than no barge-in, because it plays stale audio over
  the user.
- Partial STT feeds the agent as it arrives; sentence-level TTS starts playback
  early.
- Conversation history is maintained across a session, and a barge-in does not
  corrupt it: the interrupted partial turn is marked, not silently dropped, so
  the next turn has the right context.
- Interruption detection shares the VAD used for silence detection (§59.17) so
  the two cannot disagree about whether the user is speaking.

## 59.20 LOCAL-FIRST VOICE FALLBACK

**New.**

```text
online:   LiveKit + local STT + OpenRouter or Ollama + Piper
offline:  local STT + Ollama + Piper
```

- Voice continues to function without internet where the hardware allows it.
- If OpenRouter is unavailable, the agent falls back to the local model where
  capability allows (§59.9).
- **Not all capabilities survive the fallback.** A capability the local model
  lacks is reported as unavailable rather than answered more weakly. This is the
  same rule as §59.12 and §59.9: a degraded path must be visible.
- Offline mode requires no transport at all (§59.16), so it is also the mode
  that degrades most gracefully when memory is short (§59.25).

## 59.21 UNIFIED AGENT ARCHITECTURE

Extends §6 and §7. The requirement is that these capabilities integrate into the
**existing** runtime, not form a parallel system.

```text
                         ULTRON
                           |
                      ULTRON CORE
                           |
              +------------+------------+
              |                         |
          Agent Router              Tool Router
              |                         |
    +---------+---------+      +--------+--------+
    |         |         |      |        |        |
  Coding  Research  Reasoning  Git  Filesystem  Server
    |         |         |      |        |        |
    +---------+---------+      +--------+--------+
              |
          LLM Router
              |
      +-------+--------+
      |                |
  OpenRouter        Ollama
  (free, §59.7)    (local, §21)
```

Voice is another interface onto the same agents and tools, not a parallel agent
stack:

```text
        +--------------+
        | Voice System |
        +------+-------+
               |
             LiveKit
               |
           STT / TTS
               |
               v
          ULTRON CORE
```

The same agents and tools must be reachable from the web UI, the desktop orb,
the CLI, voice, the ESP32 interface (§26) and future clients. A capability
available through only one interface is not integrated, and one interface
reimplementing a permission check is a bypass.

## 59.22 AGENT REGISTRY ADDITIONS

Extends §7 and §43.

Additional agent types to plan for, to be added only where they do not duplicate
an existing one:

```text
CodingAgent          §8, §9        extends to free-model routing
ResearchAgent        §10           extends to citations
ReasoningAgent       --            new, or a capability profile of CodingAgent
DecisionSupportAgent §59.11        new
HealthInfoAgent      §59.12        new
GitAgent             §59.3         tools, composed of the tool runtime
FilesystemAgent      §59.4         tools, composed of the tool runtime
ServerManagementAgent §59.5        extends §12
BrowserAgent         §11
VoiceAgent           §59.16        new, voice interface
SystemAgent          §12
```

- **Where possible, an agent is a capability profile over existing tools**, not
  a new implementation. `GitAgent` and `FilesystemAgent` are almost entirely
  tools (§59.6); building them as agents with their own tool copies is exactly
  the duplication §59.0 forbids.
- Where an existing agent is a strict superset, extend it and configure the
  profile rather than adding a near-duplicate type.

## 59.23 MEMORY AND SCOPED CONTEXT

Extends §22. **New: the scoping rule.**

Memory tiers already specified: short-term, conversation, task, project,
long-term, episodic, semantic (§22). Added: tool history, agent state, voice
session state.

**Agents must not automatically receive all available memory.** Context is
scoped per agent and per task:

```text
Agent
  |
Context policy         (what this agent may see for this task)
  |
Assembled context      (short-term + scoped long-term + task state)
  |
LLM call
```

- The context policy is explicit and inspectable. Assembling an agent's context
  is itself an auditable, testable step, not a side effect of a memory query.
- Broad memory is opt-in per agent. The default is the narrowest context that
  can complete the task.
- A long-term memory read that crosses into another project's context is a
  boundary violation (§59.4's reasoning, applied to data), not a retrieval
  detail.
- Voice session state is scoped to its session, and a barge-in (§59.19) must not
  leak the interrupted turn into another session's history.

On an 8 GB host this is also a correctness matter: a context assembly with no
scope bound is a context-length failure waiting for §59.9's context overflow.

## 59.24 OBSERVABILITY ADDITIONS

Extends §32. **New: two fields, one rule.**

Every agent and tool execution records:

```text
timestamp, agent, task_id, tool, model, provider,
duration, success/failure, error, token_usage (where available),
authorization_level          <- new
```

- **`authorization_level`** records the permission level the call was evaluated
  at *and* what it was granted as. A denied call is recorded with the level it
  was refused at. Without this, an audit trail says what happened and not
  whether it was allowed -- which is the question §15 exists to answer.
- **`token_usage`** is recorded where the provider reports it, and is absent
  rather than estimated where it does not. A fabricated number in a cost column
  is worse than a blank one.

Rules carried over from §32:

- **Do not log sensitive content unnecessarily.** Credentials and secrets are
  never logged; §30's session and API-key tokens are never logged at any level.
- Per-execution records must be correlatable with the audit rows of §15 and the
  request correlation id, so one user action can be followed from the HTTP
  request through the tool call to the model call and back.

## 59.25 RESOURCE MANAGEMENT - 8 GB LAPTOP, 4 GB SERVER

Reaffirms and extends §48. **The two machines are not the same size, and the
difference changes the design.**

| Machine | RAM | Role |
|---|---|---|
| Windows development laptop (Intel i5-1235U, Iris Xe, 512 GB SSD) | **8 GB** | Build and test only. ULTRON is **never run here** (§61). |
| Ubuntu server VM | **4 GB** | The actual runtime host. |

This is a design constraint, not a deployment detail. Note that §61 makes the
laptop a non-runtime, so its 8 GB is a *budget for tests and tooling*, never a
target to design towards - the number that constrains the architecture is 4 GB.

Do not assume: a dedicated GPU, large local models, several large models at
once, Kubernetes-style infrastructure, many containers, a huge database, or many
background services.

Do prefer: lightweight services, asynchronous workers, model unloading, **online
inference rather than local inference**, CPU-friendly work, caching, queues,
lazy loading.

Do not assume: a dedicated GPU, large local models, several large models at
once, Kubernetes-style infrastructure, many containers, a huge database, or many
background services.

Do prefer: lightweight services, asynchronous workers, model unloading, **one
heavy local model at a time**, CPU-friendly inference, caching, queues, lazy
loading.

**Load-shedding order.** When memory is short, shed in this order, and say so:

```text
1. unload the local model, if one is loaded   (§59.8; usually a no-op, since
                                             inference is online by default)
2. drop voice sessions                       (voice goes offline-first, §59.20)
3. stop background agents and schedules       (§44)
4. reduce concurrent agent fan-out            (§7, §41)
5. report degraded capability to the user
```

On the 4 GB server the dominant cost is almost never the model - there is not one
by default. It is Python process size, database connection pools, browser
sessions, and concurrent agent fan-out, so steps 3 and 4 are where the memory
actually comes back. Step 1 is retained because self-hosted LiveKit (T254) and a
local fallback model (T061) are still supported, but it must not be mistaken for
the expected path.

Shedding must be observable (§59.24) and must never silently reduce the quality
of a security check or skip a permission evaluation. **Correctness is never
shedding material** -- §59.25 pressure must not become §15 pressure.

## 59.26 IMPLEMENTATION STRATEGY

Maps onto §51's phases rather than replacing them. Phases A and B are already
done: §51 Phase 0 (T001-T008) and most of Phase 1.

```text
A  Inspect             DONE -- §51 Phase 0/1; repository, architecture,
                            task graph and environment all established
B  Architecture        this section + tasks.md; registry and permission
                       systems already specified in §14-§17
C  Core infrastructure  T030-T053: Tool Router, Agent Router, LLM Router,
                       Model Registry, Permission Layer, observability
D  Agents              Phase 4-8, plus §59.22's additions
E  Voice               Phase 9, extended by §59.16-§59.20
F  Desktop             Phase 8 / §59.13-§59.15
G  Testing             §38, extended per capability
```

## 59.27 DEVELOPMENT RULE FOR EXTENSIONS

Reaffirms §53 and §53's task discipline. For every extension capability:

```text
1  inspect the existing implementation
2  determine what can be reused
3  determine dependencies
4  update the architecture (this section, then tasks.md)
5  implement the smallest stable version
6  test it
7  integrate it
8  update the documentation
9  continue to the next dependency
```

Task states, carried from `tasks.md`:

```text
PLANNED    IN_PROGRESS    BLOCKED    IMPLEMENTED    TESTED
```

A task reaches `IMPLEMENTED` when the code exists and lints; `TESTED` when the
tests pass; `[x]` only when both, per `tasks.md`. Not-before is mandatory: no
capability is marked available because an interface exists (§54).

## 59.28 UPDATED IMPLEMENTATION ROADMAP

Where the project stands against this section, and what comes next.

### Already built

```text
T010 configuration        T014 models            T018 health checks
T011 structured logging   T015 repositories      T019 DI composition
T012 typed errors         T016 migrations        T020 FastAPI factory
T013 async session layer  T017 Redis wrapper     T021 dependencies + auth (in progress)
```

Also specified and designed but not yet coded: §14-§17 tool runtime and
permissions, §20-§21 model router, §22 memory, §25 voice, §30 authentication.

### New, from this section

```text
OpenRouter free-only adapter      §59.7
Model capability registry         §59.8
Fallback + failure taxonomy       §59.9
Research citations                §59.10
Decision-support agent           §59.11
Health-information agent         §59.12
LiveKit transport                 §59.16
Utterance-boundary STT rule       §59.17
Barge-in / cancellation           §59.19
Offline voice path                §59.20
Scoped context policy             §59.23
Authorization level + token usage §59.24
Git permission tiers              §59.3
Windows tool bridge               §59.15
Orb visual states                 §59.13
```

### Dependency order

```text
Model Registry (§59.8)
   -> OpenRouter (§59.7)
   -> fallback taxonomy (§59.9)
   -> agent model profiles (§59.8 config)

Permission Layer (§15, T033)
   -> Tool Router (§14, T035/T036)
   -> Git / Filesystem / Server tools (§59.3-§59.5)

Voice interface (§25)
   -> local STT (§59.17)
   -> Piper TTS (§59.18)
   -> barge-in (§59.19)
   -> LiveKit transport (§59.16)
   -> offline fallback (§59.20)

ULTRON CORE
   -> decision-support (§59.11)   (needs no tools; can be early)
   -> research citations (§59.10) (needs tools)
   -> health-info (§59.12)        (needs the safety boundary, not tools)

Orb (§59.13)
   -> desktop client (§59.14)
   -> Windows bridge (§59.15)     (last: highest blast radius)
```

### Security boundaries to hold throughout

```text
§30  every interface authenticates; voice and desktop are not exceptions
§15  no agent reaches a tool without the permission layer
§31  no public PostgreSQL/Redis/Ollama; client connects outbound
§59.4  no agent gets unrestricted filesystem access
§59.3  destructive Git needs explicit authorization
§59.5  destructive system operations need explicit authorization
§40  agent and tool logic stay separated
§59.25 correctness is never shed under resource pressure
```

### Where to resume

Continue from the next unfinished task in `tasks.md` (T021 is in progress; T022
follows), then the extension tasks T220+ in the dependency order above.

---

## 60. MANAGED IDENTITY AND OFFLINE CONTINUITY (ADDED AFTER T021)

### 60.1 Intent

Two related requests, recorded here before any of it is built:

1. Use **Firebase Authentication** instead of self-hosting every credential.
2. Keep a copy of the data in **Firestore**, so the product still does something
   useful when the server is incomplete, rebooting, or powered off entirely.

The intent is availability, and it is a legitimate goal. The two halves carry
very different risk, so they are specified separately and get different verdicts.
Neither is Phase 1 work. Both are preserved here so the decision is deliberate
rather than inherited from whichever library is easiest to reach for.

### 60.2 Firebase Authentication as an upstream identity provider

Accepted in shape, with one constraint that decides the design.

Firebase issues a signed **ID token** (RS256, Google-signed, verified against
Google's published JWKS). That token is not revocable on its own -- it is valid
until it expires. §30 requires revocable sessions, immediate revocation on
logout, refresh rotation, reuse detection, and an audit row per decision. None of
that can be delegated to a token that says only "this was true an hour ago".

So the ID token is treated as **proof of identity at the door, not as the
session**. The flow:

```text
client                  Firebase Auth              ULTRON server
  |  sign in (email/password, Google, phone)              |
  |------------------------------------------------------>|
  |                        ID token (JWT, short-lived)    |
  |<------------------------------------------------------|
  |  POST /auth/firebase/exchange  { id_token }          |
  |------------------------------------------------->      |
  |                        verify signature, audience,   |
  |                        issuer, expiry against cached |
  |                        JWKS                         |
  |                        upsert or resolve the local   |
  |                        `users` row                  |
  |                        mint ULTRON's own opaque      |
  |                        access + refresh pair (§30)   |
  |<------------------------------------------------------|
```

Consequences worth stating plainly:

- §30 stands unchanged. ULTRON keeps Argon2id passwords for local accounts,
  opaque 256-bit tokens, SHA-256 indexed digests, rotation, family revocation,
  reuse detection, and the audit trail. Firebase replaces *how a person proves
  who they are*, not *what a session is*.
- Every §30 audit row still exists, with `actor_type=user` and the Firebase UID
  recorded as the upstream subject. Login-by-Firebase is auditable on the same
  terms as login-by-password.
- Account linking becomes a real problem: the same human arriving by password
  and by Google must resolve to one `users` row, and the rules for that merge
  (`uid` uniqueness, email-verification requirement, what happens to the local
  password) are unspecified and must be written before an implementation.
- JWKS caching, clock skew, key rotation, and "identity provider unreachable"
  all need defined behaviour. A login path that fails when Google is down is a
  new availability dependency in the one place the product cannot afford one.
- Revoking a Firebase session does not revoke a ULTRON session. Both are needed,
  and only the second one is immediate.
- Cost and dependency review is required before adoption: Firebase free tiers
  exist, but sustained use moves onto the pay-as-you-go (Blaze) plan, which
  conflicts with the free-only constraint in §59.7. This must be verified against
  current Firebase pricing rather than assumed, and it must be re-checked at the
  point of adoption, not now.

### 60.3 Firestore must not become a mirror of the PostgreSQL database

Rejected as stated. "A copy of our database in Firestore" means duplicating the
authoritative relational store -- 17 tables with foreign keys, constraints and
migrations -- into a document store, and then keeping the two in agreement. That
is not a feature, it is a permanent second source of truth with a synchronisation
protocol nobody has specified. The specific failure modes:

- **Revocation goes stale.** A revoked session, a disabled account or a consumed
  refresh token mirrored into Firestore stays valid there until the mirror
  catches up. The mirror becomes a way to use credentials the server has
  already refused, which is strictly worse than having no mirror: the audit log
  will show the denial while the token keeps working.
- **Task state goes stale.** `tasks`, `task_steps` and `events` are exactly the
  rows that must not be replayed from a stale copy. Double execution of a task
  that already ran is not a display bug.
- **Two-way merge is unspecified.** Conflicts on `memories`, `messages` and
  `conversations` have no defined resolution, and "last write wins" is a data
  loss policy, not a design.
- **Security rules become the real authorisation layer.** Anything a client can
  write is client-controlled data. Owner-scoped rules are mandatory and must be
  reviewed as carefully as §31, because a permissive rule set leaks the whole
  dataset regardless of what the server enforces.
- **It doubles the write cost and the schema surface.** Every change must now be
  written twice, and every migration needs a document equivalent.
- **It costs money at exactly the wrong time**, for the tier of user who needs
  the free tier.

PostgreSQL remains the single source of truth (§23). Nothing in this section
changes that.

### 60.4 The shape that does work: client-owned cache, one-way outbox

The availability goal is real, and it does not need a server mirror. The client
is user-side (§59.1), so the durable offline store belongs on the client, where
it costs nothing on the 4 GB VM and is available exactly when the server is not.

```text
ONLINE   client --write--> ULTRON server --write--> PostgreSQL   (authoritative)

OFFLINE  client --write--> local cache (SQLite / IndexedDB)     (client-owned)
         client --append--> outbox (Firestore, one direction)   (optional)
                              |
                              +--replayed idempotently when the server returns--> server
```

- **Local cache** holds what the user can read offline: recent conversations,
  messages, project metadata, cached model settings. Owned by the client, scoped
  to one user, and clearly labelled as a possibly-stale copy. It is a cache, not
  a mirror, so there is nothing to reconcile -- it is overwritten, never merged
  authoritatively.
- **The outbox is one-directional and bounded**: client to server, a queue of
  pending operations, replayed on reconnect. Client to server only, so there is
  no merge problem. Bounded by count and age, with entries expiring rather than
  growing without limit, and every entry carrying a client-generated idempotency
  key so a replay after an ambiguous failure cannot double-apply.
- **Firestore is optional in this design, and optional is the default.** A
  client that can reach the ULTRON server does not need it. Its value is narrow
  and specific: letting a *second* device pick up queued work from a phone whose
  server is down. If that is not wanted, ship the local cache alone and skip
  Firestore entirely -- at no cost to the offline goal.
- **Staleness must be visible.** Cached data is shown with its age, and a command
  queued while offline is visibly pending rather than optimistically reported as
  done. §59.25 applies in spirit: a correct answer late beats a confident answer
  that is wrong.
- **Offline is read-mostly and honesty-first.** Writes that need server authority
  -- anything touching permissions, models, devices, or destructive actions --
  queue or refuse offline. They do not pretend to have succeeded.

### 60.5 Status

| Item | Verdict | Status |
|---|---|---|
| Firebase Auth as upstream IdP (§60.2) | Accepted in principle, constrained to token exchange | `FUTURE` - tasks T300+ |
| Local client-side cache (§60.4) | Accepted; replaces the mirror request | `FUTURE` - tasks T306+ |
| Bounded one-way outbox in Firestore (§60.4) | Accepted as optional | `OPTIONAL` - tasks T307+ |
| Firestore as a full copy of PostgreSQL (§60.3) | Rejected | `SKIP` - retained here so it is not re-proposed |

No Phase 1 code, dependency, package, or infrastructure follows from this section.
Nothing here changes the T021 authentication implementation, which stands as
specified in §30.

---

## 61. ULTRON IS NEVER RUN ON THE DEVELOPMENT MACHINE

### 61.1 The rule

The Windows development laptop is a **build-and-test machine**. ULTRON is not
run on it, at any point, for any reason. When the build is complete, ULTRON is
**cloned to the author's own server and run there**.

```text
THIS LAPTOP (Windows, 8 GB)          THE SERVER (Ubuntu, 4 GB VM)
─────────────────────────────        ─────────────────────────────
write code   ruff   mypy   pytest    <- the deployed runtime
alembic migrations                    serves every real request
read files
PostgreSQL 17 (tests/migrations)      owns the real data

ULTRON IS NEVER EXECUTED HERE         ULTRON RUNS ONLY HERE
```

### 61.2 What this forbids on the development machine

- Starting the API server by any route: `uvicorn`, `fastapi run`,
  `python -m app`, a packaged binary, a scheduled task, or a Windows service.
- Running the web client, the Orb desktop client (§59.13–§59.15), a voice
  session, a device bridge, the scheduler, or any long-lived background worker.
- Enabling `BROWSER_ENABLED`, `SCHEDULER_ENABLED`, `COMPUTER_NODES_ENABLED`,
  `DEVICES_ENABLED`, `VOICE_ENABLED`, or `REDIS_EVENT_BRIDGE` on this host.
- Binding `API_HOST` to anything but `127.0.0.1`, or allowing the API to reach
  any address outside this machine.
- Treating this machine's PostgreSQL as anything but a scratch schema. It holds
  no real user data and is loopback-only.

### 61.3 What remains allowed

Writing code, `ruff`, `mypy`, `pytest`, Alembic migrations against the local
PostgreSQL, and reading files. Those exercise the code under supervision and
leave nothing running afterwards. This is exactly why the local database is
PostgreSQL rather than a full Docker stack: the test suite needs a real
PostgreSQL, and it does not need the rest of the runtime.

### 61.4 Why

§14–§16 make agent code able to run shells, write files, drive a browser, and
change system state. That capability is the product, and it is also the reason
the machine that holds the author's own files, credentials, and development
environment is the wrong place to demonstrate it. A bug that writes outside a
workspace, or a shell tool that runs with real authority, should hit a server
the author controls end to end — not a laptop that also holds everything else.

There is no cost to this boundary, because the runtime target is already a
separate machine (§59.1, §59.25). The boundary only removes the temptation to
"just try it locally".

### 61.5 Effect on the build

The run-and-health-check steps of §53 apply **on the server**, not here. Local
verification is `format → lint → typecheck → unit tests → integration tests →
build`. This is a deviation from the literal §53 wording and is recorded in the
Decision Log for that reason.

---

## 62. RUNTIME PLATFORM MATRIX - WHERE ULTRON RUNS

### 62.1 Two halves, and why they are not the same thing

ULTRON is a **server** plus **clients**. They run on different operating
systems. Keeping them apart matters, because parts of this document reason about
a Windows laptop and could easily be misread as Windows-first.

| Component | Runs on | Never runs on |
|---|---|---|
| ULTRON server (API, workers, scheduler, database) | **Ubuntu Server OS** (Ubuntu Server 26.x, §37) | Windows, macOS, mobile |
| ULTRON desktop client | **Windows** - the primary desktop platform | - |
| ULTRON mobile client | Android and iOS | - |
| ULTRON Orb | Windows, inside the desktop client | - |

> **Amended after §63 (added with §64):** §64.3 splits these rows by *role*
> rather than by *product*: the desktop client row is client **and** Windows
> node; the server row is client **and** server node; the ESP32 (§26) is a
> node with no client UI; the mobile row stays client-only (D022). Nothing is
> added to the "never runs on" column - Core still never runs on Windows or
> mobile (§64.4).

### 62.2 The server host is Ubuntu Server OS

The server host is **Ubuntu Server OS**. Ubuntu Server 26.x is the only
supported server platform (§37). This is restated explicitly because §61 and
§59.25 both reason about the author's Windows machine, and a reader could
reasonably conclude the repository is Windows-first. It is not. Windows appears
in this project as a *development* host (§36) and as a *client* platform
(§62.3); it is not a server platform.

Nothing in the server is Windows-specific. No registry access, no Win32 APIs, no
Windows path assumptions, no Windows service wrappers. The `.ps1` scripts in §36
are a developer convenience on a platform that will never host the server.

### 62.3 The application is installed and operated from Windows

The installed application - the ULTRON client - **is installed on Windows and is
operated from Windows**. Windows is the primary desktop platform for the
product, and the ordinary case a user sees.

### 62.4 The application is also used on mobile

Mobile is a first-class client target, not a later afterthought. A mobile
install talks to the same Ubuntu server over the same API and consumes the same
event stream as the desktop client (§27, §43). Anything mobile cannot support is
**reported unavailable** (§33), never silently hidden, so a capability gap stays
honest rather than becoming a bug report.

### 62.5 The client requires no Windows-specific operations

Operating ULTRON on Windows must not require Windows-specific *server*
operations. Installing and using the client must not require any of:

```text
WSL or WSL2
Docker Desktop, or any container runtime
a Python toolchain, virtualenv, or pip
a Rust or Node toolchain, or a compiler
cloning the repository
administrator or elevated privileges
a local PostgreSQL, Redis, or model runtime
```

The client is installed as an ordinary application and talks to the Ubuntu server
over the network. The repository, Python toolchain, Alembic migrations, and test
suite are **developer** concerns on a developer machine. They are never part of
what a user installs.

> **This resolves the apparent conflict with §61.** "The app runs on Windows"
> and "ULTRON is never run on the author's Windows laptop" are **both correct**,
> because they are statements about different components. The author's Windows
> machine develops the server and is not a server host (§61). That same machine
> will run the Windows client, once the client exists.

### 62.6 Client technology: decided — see §63

The framework **is chosen**: a **Next.js PWA**, deployed to **Vercel** from the
GitHub repository, serving both the mobile client and the ESP32 control panel
(§63). §63 also records why a native React app is deferred, and the four
platform constraints that follow from hosting on Vercel.

§43, §59.14, §27 and T261 remain the behavioural requirements any client must
satisfy.

---

## 63. CLIENT ARCHITECTURE - NEXT.JS PWA ON VERCEL

### 63.1 The decision

The mobile client and the ESP32 control panel are **one Next.js PWA**, deployed
to **Vercel** automatically from the GitHub repository.

| Decision | Choice |
|---|---|
| Mobile client form | **PWA** - installable to the home screen, OS-independent (§62.4) |
| ESP32 interface | **the same PWA, in a separate control-panel mode** (§63.4) |
| Framework | **Next.js** |
| Deployment | **Vercel**, connected to GitHub so a merge deploys |
| Native app | **Deferred.** A native React app only if the PWA proves insufficient |

A PWA was chosen over a native app because it installs to the home screen,
works from one codebase on Android and iOS, needs no app-store review, and
ships by pushing to GitHub.

### 63.2 A native React app is deferred, not rejected

If a native React app is ever built, it is an **additional** client, not a
replacement. The PWA remains the always-working baseline, because a native build
adds signing, store review, and a per-platform release to a solo author's
maintenance load (§59.25). Reconsider only if the PWA genuinely cannot deliver a
capability, and record why.

### 63.3 What "no Windows-specific operations" means on mobile

The client installs as an ordinary application and needs no WSL, Docker, Python,
compiler, repository clone, or administrator rights (§62.5). Beyond that, mobile
has real platform limits that **must be revoked rather than faked**. A PWA runs
in a sandboxed browser origin on someone else's phone, so these do not exist:

| Capability | Windows desktop client | Mobile PWA | Why |
|---|---|---|---|
| Playwright / browser automation | yes | **revoked** | No process spawning, no separate browser profile |
| Launch local applications | yes | **revoked** | Sandbox has no access to installed apps |
| Active-window detection | yes | **revoked** | No concept of a foreground window |
| Keyboard and mouse control | yes | **revoked** | No synthetic input outside the page |
| Arbitrary screenshots | yes | **revoked** | Cannot capture other apps |
| Local filesystem traversal | yes | **limited** | Sandboxed; OPFS only, no arbitrary paths |
| Shell / CLI execution | yes | **revoked** | No subprocess access at all |
| Web Serial / WebUSB | yes | **revoked** | Desktop Chromium only |
| Local model inference | no (online-first, §59) | **revoked** | No compute budget worth using |
| Sustained background work | yes | **limited** | OS suspends the tab |

Every one of these must be **reported unavailable**, never silently hidden or
left to fail confusingly (§33). The client's UI asks the server what it can do
and greys out the rest with a reason - it does not ship a list and hope. This
is the same honesty rule as §15 for security checks: never degrade silently.

The Windows-specific computer-control bridge (§59.15) is therefore **desktop
only**, and stays deferred and last (§59.15) - it cannot be the mobile client's
answer to anything.

> **Amended after §63 (added with §64):** this table still holds, with one
> reframing from §64.8. Those capabilities were listed as *Windows desktop
> client* features; §64.6 moves them onto the **Windows node** - the same
> machine, an explicitly registered target. Mobile's column is unchanged: every
> **revoked** row stays revoked, because revocation describes what the *phone*
> can do, and a phone cannot do it by asking a node to do it for it. The
> difference is that mobile can now *direct* Windows-node capabilities the
> server has authorised (§64.18.1), while holding none itself (D022).

### 63.4 The ESP32 mode is control-panel only

The PWA has a **separate mode dedicated to the ESP32**. That mode is a
**control panel**: read device state, send commands, see telemetry, configure
settings.

It is explicitly **not** a programming interface, and it does not flash
firmware. Web Serial and WebUSB are desktop-only (§63.3), so a browser cannot
drive a serial programmer anyway. The mode reaches the device the same way
everything else does: the PWA → ULTRON server → the ESP32's existing
JSON-over-WebSocket transport (T162). The device connects **out** to the server,
so no inbound ports, no LAN discovery, and no local network access are required
from the phone.

**Wi-Fi provisioning is a separate, local flow** and is out of the PWA's scope.
An ESP32 with no network cannot be reached from Vercel, and a Vercel page cannot
join a device's SoftAP (mixed content, §63.5). Provision the device once, over
its own access point or a USB cable, then the control panel works.

### 63.5 Four consequences of hosting the client on Vercel

These are the constraints that follow from the decision, and each one changes
server work. They were checked against Vercel's current documentation rather
than assumed.

**1. The ULTRON server must be reachable over HTTPS.** The PWA is served over
HTTPS from Vercel. A browser will refuse to let an HTTPS page call an `http://`
server - mixed content, blocked, with no user-facing override. So the Ubuntu
server **must** sit behind a real certificate and a domain name before any mobile
client can work. This is not optional polish; without it the client cannot talk
to the server at all. Tracked as T318.

**2. The API needs an explicit CORS policy** for the Vercel origin. Allow the
production client origin, allow `Authorization` and `Content-Type`, and use
credentials deliberately. Everything else stays denied - this is server-to-server
API surface, not a public one.

**3. The live event stream must not be routed through Vercel.** Vercel supports
WebSockets, but in **public beta**, with connections that close when the function
hits its maximum duration (300s on Hobby) and that are **pinned to one
instance**, meaning instances share no memory. That is a poor fit for ULTRON's
33-event stream, and routing it through Vercel would drag Redis back in purely
to fan out between instances.

Instead: the PWA takes its API calls **and its live event stream directly from
the Ubuntu server** (receive-only **SSE**, T023 — see the T311 decision and
`todo.md` §G), and uses Vercel only for the app shell. This
keeps chat content inside the author's own infrastructure instead of routing it
through a third party, avoids a beta dependency, and avoids inventing a Redis
requirement the rest of the project has deferred. The client **must** reconnect
and re-fetch state after any disconnect, regardless (§60.4).

Authentication of that stream is **still open**: a receive-only SSE connection has
no "first frame" to authenticate with, and `EventSource` cannot send headers. The
recommended shape is `fetch()` + `ReadableStream` with an `Authorization` header
and hand-written reconnect, which keeps the token out of URLs and access logs.
Confirm before implementing.

**4. Deployment is a build concern only.** Vercel holds the client; it must never
hold ULTRON secrets. The client speaks to the author's server with the user's
own session token and holds no privileged credential.

### 63.6 The PWA must work offline in the degraded way

Installable implies it will be opened on bad networks. Per §60.4 the client owns
a local cache (SQLite/IndexedDB) for recent conversations and a bounded one-way
outbox for commands, shows cached data with its age, and marks queued commands
as pending. It must never present queued work as completed.

---

==========================================================================
# 64. DISTRIBUTED NODE ARCHITECTURE — CORE, NODES, CLIENTS (ADDENDUM)
==========================================================================

## 64.0 STATUS AND NON-DESTRUCTIVE RULE

This section is **additive**, on the same terms as §59. Sections 1-63 remain in
force. Where this section restates an existing requirement it is marked
**extend**, and the original section stays authoritative. Nothing here deletes,
rewrites or competes with an existing decision.

```text
DO NOT
  - treat the Ubuntu server as the only execution environment
  - route a Windows operation through Ubuntu merely because a server exists
  - grant the mobile client Windows-local control
  - treat the ESP32 as an AI computer
  - build a second tool registry, a second permission layer, a second event
    system, or a second agent runtime for the node architecture
  - renumber or re-time the existing task list

DO
  - extend the existing tool registry (§14, §59.6), permission layer (§15),
    event bus (§19), node gateway (§13, T141) and device registry (§26, T161)
  - execute each operation on the node that owns the relevant capability
  - report an unavailable capability honestly (§33, §54, §63.3)
  - record new work as tasks T330+ in tasks.md
```

The two failure modes §59.0 names still apply: a parallel implementation is a
hole with the same reach as the real one, and a specification that disagrees
with the code is a defect.

## 64.1 EXTENSION MAP

Read this first. Most of the requested surface already exists; the right-hand
column is the actual work.

| Requested capability | Existing section / tasks | Verdict | Amendment work |
|---|---|---|---|
| Distributed core + node vision | §1, §5, §40 | extend | §64.2-§64.5, T330 |
| Core decides *what*, node decides *where* | §5, §59.21 | extend | §64.4 |
| Client vs node distinction | §40, §59.14, §62 | extend | §64.3 |
| Windows node (Electron, local execution) | §13, §42, §59.14, §59.15, Phase 8 (T140-T148), T261, T262 | extend | §64.6, T340-T347 |
| Server node | §5, §12, §47, §59.5, §62.2 | extend (clarify) | §64.7, T348 |
| Mobile web app | §62.4, §63, T315-T322 | extend | §64.8, T350-T351 |
| ESP32 node | §26, §27, §59.13, Phase 10 (T160-T167), §63.4 | extend | §64.9, T352 |
| Shared ULTRON Core | §5, §59.21 | extend | §64.4 |
| Node capability model | §59.6, T141, T161 | extend | §64.10, T331-T332 |
| Node registration / discovery | T141, T161 | extend | §64.10, T332 |
| Node availability / heartbeat | T141 (`heartbeat`), T161 (`last_seen`) | extend | §64.14, T333 |
| Node-targeted tool routing | §14, §16, §59.6 | extend | §64.11, T334 |
| Permissions: user/client/node/risk/confirmation | §15, §59.3-§59.5 | extend | §64.12, T353 |
| Node authentication | §30, T142, T161 | extend | §64.13, T353 |
| Cloud vs nodes vs clients | §3, §60, §63 | extend | §64.3, §64.17 |
| Availability / offline behaviour per node | §33, §60.4, §63.3 | extend | §64.14, T333, T336 |
| Cross-node event protocol | §19, §27, §28, T030/T031, T023, T162 | extend | §64.15, T335, T352 |
| Example workflows | — | **new** | §64.18 |
| Cross-node testing | §38, T147, T166, T290 | extend | T354 |

## 64.2 THE ARCHITECTURE PRINCIPLE

Three statements, and they govern every decision in this section:

> **ULTRON is a distributed personal agent system. Intelligence/orchestration
> and execution are logically separated. Nodes own capabilities; clients
> provide interfaces; the Core routes authorized operations to the appropriate
> node.**

> **Having an Ubuntu server does not mean Windows operations should execute on
> Ubuntu.** The server is *a* node, not *the* node. An operation executes on
> the node that owns the relevant capability.

> **The same ULTRON system can operate through Windows, Ubuntu, mobile, and
> ESP32 interfaces.** One Core, one permission layer, one event bus, one tool
> registry — several interfaces and several execution surfaces.

```text
                            ULTRON
                               |
                        ULTRON CORE            intelligence / orchestration
                               |                (hosts on the server node)
          +--------------------+--------------------+
          |                    |                    |
          v                    v                    v
   WINDOWS NODE           SERVER NODE          ESP32 NODE
   Electron app           Ubuntu Ultron        Desk device
   Windows OS             Linux OS             Physical UI
   local tools            local tools          display/mic/speaker/buttons
          |                    |                    |
          +--------------------+--------------------+
                               |
        CLIENTS (interfaces)  |  CLOUD SERVICES (not nodes)
   Windows Electron UI        |  Firebase Auth (§60.2)
   Mobile web app (§63)       |  Vercel app hosting (§63)
   Future desktop/web clients |  cloud AI APIs (§3, §59.7)
                              |  notifications, sync, storage
```

The diagram is conceptual. It does not move any component: the Core, agents,
model router, memory and event bus stay where §4 and §59.21 already put them.

## 64.3 CLIENTS, NODES, AND CLOUD SERVICES

Three categories, kept distinct on purpose. Collapsing any two of them is how a
permission decision ends up being made in a place nobody audits.

| | CLIENT | NODE | CLOUD SERVICE |
|---|---|---|---|
| Definition | sends requests, displays results | **owns capabilities, executes operations** | third-party/remote support, not an execution surface |
| Holds agent logic | no | no (the Core does) | no |
| Holds model access | no | no (the Core does) | it *is* the model provider |
| Can execute a tool | no | **yes**, after §15 | no |
| Examples | Windows Electron UI, mobile PWA (§63), future web/desktop clients | Windows node, server node, ESP32 node | Firebase Auth, Vercel, OpenRouter/OpenAI/Gemini/Anthropic |
| Offline effect | shows cached state (§60.4) | its capabilities disappear (§64.14) | dependent features degrade (§59.9) |

**The Electron application is both a client and a node.** One installed
artefact, two roles:

```text
Windows Electron app
  ├── CLIENT role
  │     renders server state, orb, agent windows, chat, notifications
  │     forwards intent to the Core            (§59.14, §28)
  │     holds NO agent logic, NO model access  (§59.14 unchanged)
  │
  └── NODE role
        owns Windows capabilities
        executes structured Windows tool requests locally (§64.6)
        a tool executor, not a second agent runtime
```

These do not conflict. §59.14 forbids *intelligence* on the client; the node
role is *execution*, and it goes through the same tool schema (§14), the same
permission levels (§15) and the same audit (§31, §59.24) as every other tool.

## 64.4 ULTRON CORE — WHAT VERSUS WHERE

The Core (§5) already owns orchestration. What this section adds is the
explicit split of questions:

```text
ULTRON CORE asks:   "What needs to happen?"
                     understand -> plan -> task -> agent -> model -> tool
                     memory, permissions, sessions, events, verification

TARGET NODE asks:   "Where does it execute?"
                     which node owns this capability
                     is that node online
                     is this principal authorized for it on that node
```

The Core therefore gains two responsibilities it did not previously state,
both of which are routing concerns rather than new subsystems:

```text
CAPABILITY DISCOVERY      which node advertises this capability, now
EXECUTION ROUTING         resolve capability -> node -> tool executor
```

Everything else the Core does — intent, planning, task graphs, agent
management, model routing, memory, permissions, events, authentication,
sessions, verification — is unchanged and stays where §5, §59.8 and §59.21
placed it. **No component is moved into a new "core" package.** `app/core/`
is the Core; the node work extends it with a router, not a second brain.

The Core does not assume it runs "on the server" as a matter of principle; it
runs where it is deployed, which today is the Ubuntu server (§62.2). A future
host for the Core is a deployment question, not a reason to redesign §5.

## 64.5 NODE INVENTORY

| Node | Platform | Role | Specification |
|---|---|---|---|
| **Windows node** | Windows, Electron | first-class execution node for Windows-local operations; also the primary UI client | §64.6 |
| **Server node** | Ubuntu Server | always-on Linux execution, hosts Core/agents/services, coordinates other nodes | §64.7 |
| **ESP32 node** | ESP32 | physical desk interface: display, audio, buttons, wake state — **not** an AI computer | §64.9 |
| Future nodes | Linux desktop, other devices | added by registering with the same registry (§64.10) | — |

A node is any machine/device that owns capabilities and can execute a
structured tool request. Registration, capability advertisement and heartbeat
are what make it a node rather than an unnamed client (§64.10).

## 64.6 WINDOWS NODE

Extends §13 (Computer Agent), §42 (future computer control), §59.14 (desktop
client) and §59.15 (Windows tool bridge). **New: Windows operations execute on
Windows.**

### 64.6.1 The rule

The primary Windows experience is an Electron application installed on
Windows. It executes Windows-specific operations **locally**, through
controlled local tools.

```text
CORRECT          User -> ULTRON Core/agent -> Windows node -> permission
                 layer -> Windows -> Chrome

WRONG            User -> Ubuntu server -> Ubuntu Chrome -> "somehow control
                 the Windows machine"
```

The Ubuntu server is not required for an actual Windows application launch.
The server may *route* a request to the Windows node, but it is not in the
execution path of a local operation, and a Windows operation is never
performed by the server's own OS.

### 64.6.2 Windows-local capabilities

```text
filesystem              git                 PowerShell / terminal
application launch      application manage   browser launch
browser automation      Windows UI automation
process management      screenshots          system information
system controls         notifications        audio / device interaction
other approved Windows tools
```

Every one of these is a **structured tool** (§14, §59.6) with a declared
permission level (§15). None of them is a general shell handed to a model.

### 64.6.3 Two authorized paths, one tool contract

```text
PATH A — local (server not required, works offline)

  Electron UI / local trigger
        |
  Windows node runtime          local capability discovery
        |
  local permission layer        §15 levels, local policy, audit
        |
  Windows tool executor
        |
  Windows OS


PATH B — routed (another client or the Core asks for a Windows capability)

  Mobile / web / agent
        |
  ULTRON Core                   capability discovery -> node = "windows"
        |
  permission check (server)     §15 + node authorisation + confirmation
        |
  structured tool request       { "tool": ..., "node": "windows", ... }
        |
  Windows node (outbound connection, §59.15)
        |
  node permission check         a request the node refuses is refused
        |
  Windows tool executor
        |
  Windows OS
```

Both paths use the **same** tool schema, permission levels and audit records.
Path B re-checks on the node because a request that reached the wire is not
the same thing as a request that was authorized (§64.13).

### 64.6.4 Shape of the application

```text
Windows Electron App
  ├── main process      window lifecycle, IPC, local tool executor
  ├── preload           an explicit, minimal bridge — no raw Node to renderer
  ├── renderer          ULTRON UI: orb, chat, agent windows, notifications
  └── node runtime      registration, heartbeat, tool dispatch, local audit
```

- The **renderer never executes tools.** It renders state and forwards intent
  (§59.14). The main process is the tool executor, which is what makes the
  preload boundary meaningful.
- The node runtime is a client of the server's node gateway (§13, T141) over
  an **outbound** connection (§59.15): no inbound LAN listener, consistent
  with §31.
- Windows-specific execution sits behind Windows-specific adapters (§36,
  §64.20). Nothing in `server/` imports a Windows API.

### 64.6.5 What the Windows node is not

- It is not a second agent runtime. Agents run where the Core runs (§64.16).
- It is not a second permission system. It implements §15 locally.
- It is not required to be online for server, cloud or mobile capabilities to
  work (§64.14).

## 64.7 SERVER NODE

Extends §5, §12, §47, §59.5 and §62.2. **The Ubuntu server remains a
first-class ULTRON node. It is not removed, and it is not required to perform
Windows-local operations.**

The server node provides the always-on Linux execution environment and the
persistent/background half of the system:

```text
run ULTRON services, Core and agents       run long-running background agents
execute Linux applications                 run scheduled tasks (§44)
launch/control Linux browsers              maintain persistent services
filesystem and Git operations              maintain server-side state
terminal and server administration tools   coordinate other nodes (§64.10)
browser automation                          communicate with the ESP32 (§26)
APIs / SSE / device WebSocket              host optional local AI (§21, §48)
provide execution when Windows is offline  schedule + automation (§44)
```

Two clarifications this section exists to make:

1. **Windows execution is not a server duty.** The server *routes* Windows
   requests (§64.6.3 Path B) and enforces permissions before routing. It does
   not perform them.
2. **The server is one node among several from the router's point of view.**
   Its tools carry `node = "server"` and are selected by capability like any
   other node's (§64.10). Nothing in the tool pipeline special-cases the
   server — which is what keeps "the server is the only execution
   environment" from re-entering the code through the back door.

## 64.8 MOBILE CLIENT

Extends §62.4 and §63. The mobile-first web client is the Next.js PWA already
decided in §63; this section adds its **capability scope** as a ULTRON
interface.

```text
Phone -> ULTRON mobile web app -> ULTRON server/node -> Linux tool
```

The mobile client is **a client** (§64.3). It has no node role of its own. It
is a remote interface to the server and to other *authorized* capabilities.

In scope:

```text
chat                                 voice interaction where supported
viewing agent activity               viewing tasks
starting/stopping authorized tasks   server system status
server application control           server filesystem operations
server browser operations            server Git operations
server terminal/tool operations      memory/conversation access per permissions
device status                        ESP32 control (the §63.4 control panel)
notifications / events where supported
```

Out of scope, and stated as a rule rather than a omission:

> **The mobile client is granted no Windows-local control.** A Windows
> capability is never exposed through the PWA unless a future explicit
> remote-Windows feature is added with its own authentication, authorisation
> and audit. §63.3's revocation table stands unchanged.

**§63.3's revocations are about client-local execution, not server
capabilities.** "Launch local applications: revoked" means the PWA sandbox
cannot launch an app *on the phone*. It says nothing about asking the server
to open Firefox *on the server*. Both facts are reported to the UI by the
capability matrix (T316), which now reports per **node** rather than per
client only.

## 64.9 ESP32 NODE

Extends §26 (device gateway), §27 (orb backend), §59.13 (orb states) and
§63.4 (control panel). **The ESP32 is a dedicated physical ULTRON node. It is
not a full AI computer and not a server.**

```text
ULTRON SERVER
      |
Device Gateway / node registry        §26, T161, T332
      |
ESP32 node                            JSON-over-WebSocket (T162), dials OUT
      |
display   microphone   speaker   buttons/joystick/touch   sensors
```

Role:

```text
physical ULTRON desk interface        real-time ULTRON events
display / status UI                   server and node status
wake / listening state                visual feedback
microphone and speaker where fitted   physical controls
device status / heartbeat
```

**One event system.** The ESP32 consumes the same event bus (§19) as every
other surface; it does not get a parallel one. Transports differ, events do
not:

| Consumer | Transport |
|---|---|
| Windows Electron UI, mobile PWA | receive-only SSE (T023, T320) |
| ESP32 node | device JSON-over-WebSocket (T162) |
| Windows/other execution nodes | node gateway connection (T141) |
| internal subscribers | in-process bus (T031) |

Event types this section adds to the canonical set in T030 (existing types in
§19 and §27 are not renamed):

```text
TASK_PROGRESS        node lifecycle        NODE_ONLINE  NODE_OFFLINE
                                             SERVER_ONLINE  SERVER_OFFLINE
                                             WINDOWS_ONLINE WINDOWS_OFFLINE
orb / agent states   LISTENING  THINKING  SPEAKING  ORB_SHOW  ORB_HIDE
                                             AGENT_STOPPED
```

`TASK_STARTED`, `TASK_COMPLETED`, `TASK_FAILED`, `AGENT_STARTED` and
`DEVICE_CONNECTED` / `DEVICE_DISCONNECTED` already exist in §19; `ORB_SHOW`,
`ORB_HIDE` and the agent-window events already exist in §27. They are listed
here so the ESP32's display contract can be written against one name per fact
— §19 (`DEVICE_DISCONNECTED`) and the §58 checklist (`DEVICE_OFFLINE`) already
disagree, and a third spelling is not welcome.

## 64.10 NODE CAPABILITY MODEL

Extends §59.6 (unified tool registry) and T141/T161. **One registry, one
router; capabilities are advertised, not assumed.**

```text
WINDOWS NODE                        SERVER NODE
  filesystem                          linux_apps
  powershell                          linux_shell
  windows_apps                        filesystem
  windows_ui                          browser
  browser                             git
  git                                 server_management
  processes                           background_agents
  screenshots                         scheduler
  notifications                       services
  clipboard (high-sensitivity)

ESP32 NODE
  display      microphone     speaker
  buttons      sensors        physical_controls
```

Rules:

- **Capabilities are strings on a node record, not a new subsystem.** The node
  record already exists in two places: `devices` (§26, T161, with a
  `capabilities` column) and the computer-node registration (§13, T141). T332
  unifies them behind **one** node registry. A third registry is forbidden
  (§59.0).
- **The tool registry stays the authority on what a tool is** (§14, §59.6). A
  capability is the *name of a family a node can host*; the tool itself still
  declares `name`, `input_schema`, `permission_level`, `execute`, `verify`,
  `timeout`, `audit`. §59.6's required fields are unchanged; `node_scope` is
  added to them (§64.11).
- **Discovery is live, not a config file.** A node's advertised capabilities
  come from its registration and heartbeat (§64.14), so an offline node stops
  offering them without a configuration change.
- **Discovery is exposed like the rest of the API** (§29):
  `GET /nodes`, `GET /nodes/{id}` — registration, capabilities, status,
  `last_seen`. Read access follows §15; mutating node records requires an
  elevated level.

Conceptual record — illustrative, not a literal schema:

```text
node_id            windows | server | esp32:<id>
node_type          windows | server | esp32 | future
capabilities[]     advertised, refreshed on heartbeat
status             online | offline | degraded
last_seen
auth               node credential reference (§64.13)
client_of          which UI, if any, is co-located (Electron = both)
```

## 64.11 NODE-TARGETED TOOL EXECUTION

Extends §14 (tool system), §16 (execution pipeline) and §59.6 (registry). The
LLM still never reaches the operating system directly.

```text
User
  |
ULTRON Core / Agent
  |
Structured Tool Request          { tool, node?, args }
  |
Capability discovery             resolve node, or confirm the named node
  |
Permission / Policy Layer        §15 + client + node + target + confirmation
  |
Target Node                      "server" | "windows" | "esp32:<id>"
  |
Tool Executor                    local dispatch, or routed to that node
  |
Operating System / Device
  |
Verification -> Event -> Result  §17, §19 — unchanged
```

Examples, following the schema style already used in §59.6 and §14:

```json
{ "tool": "open_application", "node": "windows", "application": "chrome" }
```

```json
{ "tool": "open_application", "node": "server",  "application": "firefox" }
```

```json
{ "tool": "git", "node": "server", "operation": "status", "workspace": "ultron" }
```

Rules:

- **`node` is an extension of the existing tool request, not a second
  protocol.** A request with no `node` is resolved by capability discovery;
  a request with a `node` is checked against that node's advertised
  capabilities. Either way it enters the *same* §16 pipeline.
- **The model expresses a preference; the router decides.** A model that
  names a node it is not authorized for, or a node that is offline, gets a
  typed refusal (§33), not a silent re-route to a node the user did not
  authorize.
- **A capability available on several nodes resolves by explicit policy** —
  node affinity, availability, and the requesting client. The default must be
  deterministic and logged, because "which machine did that run on" has to be
  answerable after the fact (§59.24).
- **Offline node ⇒ typed `NODE_UNAVAILABLE`**, surfaced to the user with the
  capability name. Never a hang, never a quiet success (§54, §63.3).
- Windows dispatch obeys §64.6.3: routed requests are checked on the server
  *and* on the node.

## 64.12 PERMISSIONS

Extends §15. **The LEVEL 0-5 scale in §15 is unchanged and remains the only
permission scale in the project.** What this section adds are the dimensions
over which a level is evaluated, and an explicit confirmation rule.

Dimensions of one decision:

```text
user             who is asking
client           which interface asked (Electron, PWA, voice, device)
node             where it would execute
tool             what capability
operation        what specifically (§59.3's SAFE / CONTROLLED / EXPLICIT)
risk level       the tool's declared §15 level
target resource  workspace, path, service, device
confirmation     does this invocation require an explicit yes
```

Reconciliation with the proposed labelling — the existing scale wins where
they differ:

| Proposed label | Existing §15 level | Verdict |
|---|---|---|
| 0 = answer only | LEVEL 0 (read-only) | **extend**: level 0 covers tool-free answers *and* reads; an answer needs no tool at all |
| 1 = read information | LEVEL 0 | same meaning, already covered |
| 2 = safe local action | LEVEL 1 (safe actions) | same meaning |
| 3 = application/system action | LEVEL 3 (execute programs) | LEVEL 2 (modify project files) stays as its own level and is not merged away |
| 4 = potentially destructive | LEVEL 4 (system configuration) | same meaning |
| 5 = sensitive / high-impact requiring confirmation | LEVEL 5 (destructive / high-risk) | **extend**: confirmation is now explicit — see below |

Confirmation rule, consistent with §59.3, §59.4 and §59.5:

```text
LEVEL 0-1    no confirmation
LEVEL 2      confirmation unless pre-authorized for this workspace
LEVEL 3      confirmation unless pre-authorized for this operation
LEVEL 4      explicit confirmation per invocation
LEVEL 5      explicit confirmation per invocation, naming the target node
```

Node-specific rules:

- **A permission decision is made for a (principal, node, tool, operation)
  tuple.** Granting a principal LEVEL 3 on `server` grants nothing on
  `windows`.
- **Cross-node actions are confirmed on the requesting side and re-checked on
  the executing side** (§64.6.3).
- **Screenshot and clipboard remain high-sensitivity** wherever they run
  (§59.15), and now carry the node in the audit row (§59.24).
- **Nothing is ever shed to save resources.** §59.25's rule is reaffirmed:
  memory pressure never reduces a permission evaluation.

## 64.13 NODE IDENTITY AND AUTHENTICATION

Extends §30, T142 (node authentication) and T161 (device auth). One identity
model for all three node types:

```text
node registers        presents a node credential          §30, T142
server verifies       records the node, capabilities      node registry
heartbeat             re-authenticates, refreshes caps    §64.14
tool request          carries principal + node + tool     §64.11
server check          principal authorized for this node  §15, §64.12
node check            node re-verifies the request        §64.6.3 Path B
audit row             principal, client, node, tool,
                      operation, level, decision          §31, §59.24
```

- **Connections are outbound from the node/client to the server** (§59.15,
  §31): no inbound LAN listener on a user's Windows machine.
- A node credential authenticates *a node*, never a user. Sessions (§30) and
  node identities are different principals and are recorded as such
  (`actor_type`), the way §60.2 already separates a Firebase UID from a
  ULTRON session.
- **Revocation works per node.** Losing a node's credential removes that
  node's access without touching the user's session, and vice versa.
- The ESP32 keeps its existing device auth (§26, `ESP32_REQUIRE_AUTH`); this
  section does not create a second device credential type.

## 64.14 AVAILABILITY AND OFFLINE MODEL

Extends §33 (graceful degradation), §60.4 (offline continuity) and §63.3
(capability reporting). **ULTRON is not one monolithic machine, and capability
availability is explicit rather than assumed.**

| Condition | Effect |
|---|---|
| Windows node online | Windows tools available to every authorized principal |
| **Windows node offline** | Windows tools reported unavailable with a reason; server, cloud, ESP32 and mobile-server capabilities keep working |
| Server node online | Core, agents, server tools, tasks, scheduler available |
| **Server node offline** | Mobile server controls unavailable (honest "server unreachable", §60.4); the Electron client's Windows-local capabilities continue; the PWA shows cached state with its age |
| Cloud unavailable | local capabilities continue; model fallback per §59.9; **no promise of what the implementation cannot do** (§54) |
| ESP32 node offline | device controls and device events unavailable; everything else unaffected |
| Client offline | its session's requests stop; nodes and other clients are unaffected |

Mechanisms (all existing, none new):

```text
heartbeat + timeout       node registry (T332/T333), extends devices.last_seen
NODE_ONLINE / NODE_OFFLINE events    §64.15, on the one event bus
capability matrix         T316, now per node
typed errors              NODE_UNAVAILABLE, §33's degrade-don't-crash
```

An offline node is never simulated. §54 applies unchanged: ULTRON does not
pretend a node is reachable, and does not pretend a capability exists.

## 64.15 EVENTS AND THE CROSS-NODE EVENT PROTOCOL

Extends §19 (event bus), §27 (orb), §28 (agent windows). **One bus, several
transports, no duplicate event system.**

```text
producer (agent / Core / node / device)
      |
   EVENT BUS (T031)                single canonical type set (T030 + §64.9)
      |
      +--> in-process subscribers  logger, memory, scheduler, monitoring
      +--> SSE to clients          T023, T320  (Electron UI, mobile PWA)
      +--> device WebSocket        T162        (ESP32)
      +--> node gateway            T141        (Windows node, future nodes)
      +--> Redis bridge (optional) T031
```

Cross-node protocol requirements:

- **Events carry the node.** An event caused by, or delivered to, a node
  identifies it (`node_id`), so a UI can show *which* machine is offline.
- **Delivery is at-least-once with idempotent consumers**, or is explicitly
  documented as lossy — the choice is made once, in T335, not per consumer.
- **A node that misses events recovers by re-fetching state**, the same rule
  T320 already imposes on the PWA. Events are not a durable log.
- Node lifecycle events (`NODE_ONLINE` / `NODE_OFFLINE` and their
  `SERVER_`/`WINDOWS_`/`ESP32_` forms) are produced by the node registry
  (T333) and are the same events the ESP32 uses to light its status display.

## 64.16 AGENT ARCHITECTURE

Extends §6, §7, §41 and §59.21. **The multi-agent architecture is preserved
exactly as specified.** What changes is that an agent's tools are no longer
implicitly assumed to live on the server.

```text
                        ULTRON CORE
                   /                    \
          Agent Router               Tool Router (§59.6 + §64.11)
                |                          |
     Coding Research Browser ...     capability -> node -> executor
                |                     /          |          \
                +---- agents ------- windows    server     esp32
```

- An agent declares **capabilities it needs**; the Tool Router resolves them
  against the node registry. "Where are my tools?" is a routing question, not
  an agent-code question.
- **The same agent can execute on different nodes for different tasks:**

```text
Coding Agent on the Windows node:
  Windows filesystem + Windows Git + Windows terminal + OpenCode CLI

Coding Agent on the server node:
  server filesystem + Linux Git + Linux terminal + OpenCode CLI
```

- **The OpenCode CLI integration (§8, §9) is unchanged** and remains the
  coding-engine implementation where already planned. It is not replaced,
  duplicated or moved.
- **One agent runtime.** A node executes *tools*; it does not host agents
  (§64.6.5). Agent lifecycle, model routing, memory scoping (§59.23) and
  verification (§17) stay where they are.
- New agent *types* are not implied by this section. §59.22's rule stands: an
  agent is a capability profile over existing tools wherever possible.

## 64.17 CLOUD SERVICES ARE NOT NODES

Cloud services (§3, §60, §63) remain part of the architecture and remain
**services**, not execution surfaces:

```text
Firebase Auth       identity at the door      §60.2 — never a ULTRON session
Vercel              hosts the PWA app shell   §63.5 — never holds secrets
OpenRouter/OpenAI/Gemini/Anthropic
                    model providers           §3, §59.7 — never execute tools
notifications/sync/storage                    §45 — providers behind an interface
```

A cloud service cannot be a target node: it has no ULTRON tool executor, it
is not covered by §15, and it does not advertise capabilities. The three-way
distinction in §64.3 is the test: **if it cannot execute a structured tool
request after a permission check, it is not a node.**

> **Amended after §63 (added with §65):** the telephony provider (Twilio and
> siblings, §65.4) is classified here: a **cloud service**, never a node - it
> has no tool executor and is not a capability target. The **telephony service**
> (§65.3) runs on the server node like any other server-side component
> (§64.7, §65.19); the provider is an outbound dependency behind an adapter,
> in the same category as the model providers above.

## 64.18 EXAMPLE WORKFLOWS

### 64.18.1 Mobile → server: "Open Firefox on the server."

```text
Mobile UI
  -> authenticated API + SSE (§63.5, T023, T320)
  -> ULTRON Core
  -> capability discovery        "browser" advertised by node "server"
  -> server node selected
  -> permission check            §15, client = mobile, node = server
  -> structured tool request     { "tool": "open_application",
                                   "node": "server",
                                   "application": "firefox" }
  -> server browser tool
  -> Firefox opens on Ubuntu
  -> verification (§17) + event (§19)
  -> SSE event/result returned
  -> Mobile UI updates
```

This is **not** Windows automation. No Windows capability is involved, and the
phone's own sandbox is irrelevant to it.

### 64.18.2 Windows: "Open Chrome."

```text
Electron UI (client role)
  -> ULTRON Core / agent (local intent handling)
  -> Windows capability discovery     node "windows" advertises windows_apps
  -> permission check                 §15 + local policy (§64.6.3 Path A)
  -> Windows application tool
  -> Chrome opens on Windows
  -> event / result
```

The Ubuntu server is **not** required for this launch. If the request arrives
from another client instead, it takes Path B: server permission check → routed
to the Windows node → node re-check → execute.

### 64.18.3 ESP32: agent state

```text
Server/agent state: AGENT THINKING

  ULTRON event bus emits THINKING / AGENT_STARTED   (§19, §64.9)
        |
  device gateway fans out over T162
        |
  ESP32 node -> display / LED / UI changes
```

And the reverse direction:

```text
ESP32 wake / button input
        |
  device gateway (T162) -> WAKE_DETECTED (§19)
        |
  voice pipeline (§25, §59.16-§59.20) -> agent/LLM
        |
  response -> TTS -> ESP32 audio + display feedback
```

Both directions use the existing voice/device architecture. No second
pipeline.

### 64.18.4 Coding agent across nodes

```text
"Fix the login bug in the repo on my PC."

  Core -> planning -> Coding Agent
       -> capability discovery: repository lives on node "windows"
       -> permission check (workspace grant, §59.4)
       -> Windows node: filesystem + Git + terminal + OpenCode
       -> verification on the node
       -> result + events to every subscribed client
```

## 64.19 CONFLICTS RECONCILED

Apparent contradictions with sections 1-63, and how each is resolved:

| Apparent conflict | Resolution |
|---|---|
| §28: "The server remains the actual execution environment" | True for **agent instances**: agents run where the Core runs, and an agent window visualises a server-side agent. **Tool execution** is routed to the owning node (§64.11). Both statements hold |
| §59.14: "The client is a client... no agent logic, no model access" | Unchanged. The Electron node role is a **tool executor** (§64.3), which is neither agent logic nor model access |
| §13/§42/§59.15 describe the server calling into a Windows client | Preserved as **Path B** (§64.6.3). Path A (local execution with no server round trip) is the addition; it uses the same schema and permissions |
| §61: "ULTRON is never run on the development machine" | Unchanged, and it still applies to the **server** and to running a live Windows node **on the author's laptop**. The Windows node as a product ships to a Windows target machine (§62.3); on this laptop it is developed and tested through unit/contract tests and the T146 test double |
| §63.3: mobile cannot launch applications | About the **phone's** sandbox. Server-node application control is available to mobile (§64.8). Both are reported by the capability matrix |
| §62.1 table lists the desktop client as a client only | Amended by §64.3: it is a client **and**, in its node role, the Windows execution node |
| §64.9 adds ESP32 events while §19 already lists device events | Additive: §19's set is not renamed; §64.9 adds the missing names so one fact has one spelling (T300/T335) |
| "Server is the only execution environment" reading of §5/§41 | Refuted explicitly by §64.2, §64.7; the tool pipeline does not special-case the server |
| A second architecture could be read into this section | §64.0 forbids it: same registry, router, permission layer, event bus, agent runtime |

## 64.20 DEVELOPMENT, DEPLOYMENT, AND RESOURCE CONSTRAINTS

**Preserved unchanged:** §36 (Windows development, Linux equivalents),
§51/§53 (phased build, inspect first), §59.25 (4 GB server / 8 GB laptop),
§61 (never run ULTRON on the development machine), §62 (Ubuntu is the only
server platform), §21/§48 (resource management).

What the node architecture adds:

```text
Windows development   +  Ubuntu deployment  +  ESP32 development
```

- **No Windows-path dependence.** Platform-specific execution stays behind
  platform-specific adapters and tools (§36, §64.6.4). `server/` never
  imports a Windows API; `clients/` never pretends to be Linux.
- **No virtualenv transfer between operating systems.** The server's Python
  environment is created on the server; the Electron app's dependencies are
  installed by its own toolchain.
- **No heavy services are forced onto the laptop or the server.** Local AI,
  STT, TTS, Docker, MQTT and self-hosted LiveKit remain optional/deferred
  exactly where §3, §59.16, §59.20 and todo.md §F already put them. The
  node architecture adds **no new service**: no message broker, no
  microservices, no Kubernetes, no second API, no second database. It adds a
  registry (data), a router (code), and reuse of the existing transports.
- **The Electron app is lightweight by requirement** — it must not become a
  memory or CPU liability (§59.13's rule, applied to the whole client), and
  it must not run the server's runtime.
- **The Ubuntu server stays lightweight**: the node registry is a table and a
  heartbeat, not a new process.

## 64.21 IMPLEMENTATION ROADMAP

Maps onto the existing task structure rather than replacing it. New tasks are
**T330+**, appended in tasks.md in the same style as T220+ and T300+
(D013/D017). Existing tasks are not renumbered, re-timed or marked complete.

```text
ARCHITECTURE AMENDMENT   T330-T336   spec (this section) + node registry,
                                     heartbeat, node-targeted routing,
                                     cross-node events, availability reporting
WINDOWS NODE             T340-T347   Electron shell, local runtime, Windows
                                     tools, registration, tests  (Phase 8 /
                                     §59.15 client half)
SERVER NODE              T348        advertise server capabilities as a node
MOBILE CLIENT            T350-T351   chat/agent UI, server control (extends
                                     the §63 T315-T322 block)
ESP32 NODE               T352        real-time ULTRON states over T162
SECURITY                 T353        node identity + capability authorization
TESTING                  T354        cross-node test matrix
```

Dependency order:

```text
node registry (T332)
   -> heartbeat / availability (T333)
   -> node-targeted tool routing (T334)
   -> cross-node events (T335) -> availability reporting (T336)
   -> Windows node runtime (T341) -> Windows tools (T342-T345)
   -> server capability registration (T348)
   -> mobile server control (T351)
   -> ESP32 state events (T352)
   -> node authorization (T353)
   -> cross-node test matrix (T354)
```

Security boundaries carried forward unchanged (§59.28's list, plus §64):

```text
§30   every interface authenticates — nodes and clients are no exception
§15   no agent reaches a tool without the permission layer
§31   no public PostgreSQL/Redis/Ollama; nodes connect outbound
§59.3 destructive Git needs explicit authorization
§64.12 a permission is granted for (principal, node, tool, operation)
§64.6  Windows operations execute on Windows, never on Ubuntu
§64.8  the mobile client is granted no Windows-local control
§59.25 correctness is never shed under resource pressure
```

**This section is documentation.** Nothing in §64 is implemented by virtue of
being written here; §54, §56 and tasks.md rule 1 apply unchanged.

---

# 65. TELEPHONY - PHONE CALL INTERFACE (ADDENDUM)

## 65.0 STATUS AND NON-DESTRUCTIVE RULE

This section is appended after §64. It adds a **phone-call interface** to the
existing architecture. It does not replace, rename or rewrite the voice system
(§25, §59.16-§59.20), the agent runtime (§6, §7), the event bus (§19), the
permission system (§15), the task system (§18), memory (§22), or the node
architecture (§64). Existing sections are extended by reference only.

Nothing here is implemented by virtue of being written here (§54, §56).

## 65.1 EXTENSION MAP - WHAT THE AUDIT FOUND

Audited before writing: voice manager/session tasks (T150-T156), STT/TTS/wake
packages (`app/voice/stt`, `app/voice/tts`, `app/voice/wake`), voice extensions
(§59.16-§59.20, T250-T255), event bus (§19, T030/T031), API conventions (§29),
auth (§30, T021/T022), permissions (§15, T033), scheduler (§44, T170-T175),
memory (§22, T120-T129), nodes (§64, T330-T354), config/settings.

| Needed by telephony | Already exists | Verdict |
|---|---|---|
| Voice session + pipeline | T153 `app/voice/manager.py`, §25 | **Reused** - a call is a voice session |
| STT / TTS engines | `STTEngine` / `TTSEngine` ABCs (T150/T151) | **Reused** - calls use the same adapters |
| Realtime transport for audio | §59.16 LiveKit (mic-in path) | **Reused where it fits**; provider call media streams get their own bridge (§65.9) |
| Event bus | §19, T030/T031, SSE fan-out (T023/T320) | **Extended** - `VOICE_CALL_*` names only |
| Sessions / tasks / scheduler | §18, §44, conversations/sessions tables | **Reused** - a call links to a session/task |
| Auth, permissions, audit | §15, §30, §31, T033 | **Reused unchanged** |
| Config/secrets | `config/settings.py`, `.env.example` | **Extended** - `TELEPHONY_*` keys |
| Provider HTTP client conventions | model provider adapters (§20) | **Pattern reused** - provider isolation |

**Not created:** no second voice system, no second agent runtime, no second
event bus, no second memory system (§24 of the feature request = our §64.20
rule, restated in §65.24).

## 65.2 THE FEATURE AND THE PRINCIPLE

User: "Call me." ULTRON creates a call session, dials the destination through
the configured provider, establishes a realtime voice session on answer, and
runs the conversation through the **existing** agent system until the call
ends; state is finalized under existing memory/session rules. The same
architecture accepts inbound calls later (§65.14).

The architectural rule:

```text
Agent / Core
     |
"I need to call this destination"        <- all the agent ever says
     |
Voice Call API  (§65.6)
     |
ULTRON Telephony Service  (§65.3)
     |
Provider Adapter  (§65.4)                <- Twilio specifics live ONLY here
     |
   Twilio / Telnyx / Plivo
     |
   Phone network
```

The agent knows *intent*; the service knows *state*; the adapter knows
*vendor*. No vendor type crosses upward.

## 65.3 SERVICE BOUNDARY AND MODULE LAYOUT

```text
server/app/voice/
├── manager.py                 (T153 - existing voice sessions)
├── telephony/
│   ├── __init__.py
│   ├── service.py             call lifecycle orchestration
│   ├── provider.py            TelephonyProvider ABC
│   ├── sessions.py            call session state machine
│   ├── webhook.py             provider callback intake + verification
│   └── providers/
│       ├── __init__.py
│       ├── twilio.py          first adapter
│       ├── telnyx.py          later
│       └── plivo.py           later
├── stt/  tts/  wake/          (existing, untouched)
```

The telephony service **calls into** `voice/manager.py` to attach the audio
session; it does not fork it. `service.py` is the only module allowed to import
`providers/`.

## 65.4 PROVIDER ABSTRACTION

`TelephonyProvider` ABC, adapted from ULTRON's adapter convention (§1 principle,
§20 provider pattern):

```text
TelephonyProvider
 ├── initiate_call(destination, from_, webhook_url) -> provider_call_id
 ├── answer(...) / hangup(provider_call_id)
 ├── get_status(provider_call_id)
 ├── open_media_stream(provider_call_id)   realtime audio in/out
 ├── parse_webhook(request) -> CallEvent  (signature-verified)
 └── health() -> ok | degraded | down
```

Only **one provider is implemented first** (Twilio, T365). Telnyx and Plivo are
listed as future adapters behind the same ABC; adding one must not touch
service.py, sessions.py or the agent layer. A `MockTelephonyProvider` ships
with the tests (§65.23).

## 65.5 CALL SESSION MODEL

A call is a ULTRON voice session with call fields - not a parallel session
concept:

```text
CallSession
 ├── call_id            provider call id + ULTRON id
 ├── provider           twilio | telnyx | plivo | mock
 ├── direction          outbound | inbound
 ├── destination        stored per §65.13 policy (masked in logs)
 ├── status             CREATING | RINGING | CONNECTED | LISTENING
 │                      | THINKING | SPEAKING | ENDING | ENDED | FAILED
 ├── created_at / connected_at / ended_at
 ├── session_id         link to existing ULTRON session/conversation
 ├── task_id            link to task when schedule-created (§65.18)
 ├── agent_id           agent driving the conversation
 └── metadata           purpose, locale, failure_reason
```

State transitions are guarded and monotonic (CREATING→RINGING→CONNECTED→…
→ENDED; any state →FAILED). LISTENING/THINKING/SPEAKING mirror §59.13's orb
states, so every interface already understands them.

## 65.6 OUTBOUND CALL API

Following §29's plural convention:

```text
POST /voice/calls                 {destination, agent?, purpose?, schedule?}
GET  /voice/calls/{call_id}
POST /voice/calls/{call_id}/end
POST /voice/webhooks/{provider}   (provider callbacks, not user-authenticated)
```

Authenticated by the existing dependencies (§30, T021/T022); call initiation
requires the calling principal to hold the telephony permission (§15); responses
carry ids and status only - never provider credentials or full webhook secrets.
Returns `{call_id, session_id, status}`.

## 65.7 OUTBOUND LIFECYCLE

```text
POST /voice/calls
  → permission check (§15)
  → CallSession CREATING
  → provider.initiate_call(webhook_url = public base + /voice/webhooks/{p})
  → RINGING  (VOICE_CALL_RINGING)
  → answer   → CONNECTED  → realtime bridge open (§65.9)
  → loop: LISTENING → agent (§65.8) → SPEAKING → LISTENING
  → end (user, agent, hangup event, timeout)
  → ENDING → ENDED  (VOICE_CALL_ENDED) → finalize (§65.17)
Any step may go → FAILED with a reason; the API call itself still succeeded
(a failed call is an event and a row, never an unhandled exception - §65.21).
```

## 65.8 AGENT INTEGRATION

There is **no Telephony Agent.** The path is:

```text
phone audio → voice session (T153) → ULTRON Core/orchestrator (T049)
           → existing AI router (§20) → existing agents/tools (§7, §14)
           → result → TTS/realtime audio → phone
```

"Check the status of my server" on a call routes through exactly the same
tool pipeline, permission checks and confirmations as any other interface;
"Start the research task" creates a normal background task (§44) and speaks
the confirmation. Calls grant no implicit permissions (§64.12's scoped grants
apply: (principal, node, tool, operation) with the call recorded in the
session field).

## 65.9 REALTIME VOICE IN A CALL

Target is full-duplex, but implementation follows the phase and the
cloud-first rule (§65.10):

```text
phone mic → provider media stream → ULTRON audio bridge (service)
          → STT or realtime provider → Core/agent → tools/memory/tasks
          → TTS or realtime provider → audio bridge → provider → phone
```

- STT/TTS go through the **existing** `STTEngine`/`TTSEngine` ABCs (T150/T151),
  with cloud adapters configured first; local engines stay optional (§59.20).
- A **realtime provider abstraction** (OpenAI realtime, Gemini Live, others)
  sits behind config-selected adapters; swapping it changes no agent code.
- Barge-in/cancellation follows §59.19; partial transcripts follow T154.
- LiveKit (§59.16) remains the mic-in transport for client UIs; phone media
  streams are provider-specific and terminate in the audio bridge, not in
  LiveKit rooms.

## 65.10 CLOUD-FIRST RESOURCE MODEL

The 4 GB Ubuntu server must not gain heavy models to make calls work (§59.25,
§64.20 preserved):

```text
Ubuntu:  FastAPI + telephony service + webhook intake + audio forwarding
         + session coordination + agent orchestration   (lightweight)
Cloud:   realtime AI / STT / TTS / inference            (as configured)
Local:   optional, never required                        (§59.20)
```

No GPU requirement, no new daemon, no message broker.

## 65.11 WEBHOOK ARCHITECTURE - PUBLIC HTTPS IS MANDATORY

Providers dial **out** to ULTRON over HTTPS. Local addresses
(`localhost`, `192.168.x.x`) are not webhook endpoints and the spec must never
pretend otherwise:

```text
Internet → phone network → telephony provider → HTTPS webhook
        → public endpoint (reverse proxy / TLS / tunnel / cloud ingress)
        → ULTRON gateway → /voice/webhooks/{provider} → service
```

`TELEPHONY_WEBHOOK_BASE_URL` must be a public HTTPS origin; the service
validates this at startup and refuses to start with a local base URL (same
fail-fast philosophy as D011). This **reuses T318** (HTTPS on the server) and
§63.5's reverse-proxy/CORS work - telephony adds no new ingress, it is the
second consumer of it. No specific tunnel vendor is hard-coded.

## 65.12 SECURITY

- Authenticated, permission-checked call initiation (§15, §30).
- Webhook signature verification per provider + timestamp window; forged or
  replayed callbacks are rejected with an audit row (§31).
- Secrets only in environment/config (§65.13); no keys or numbers in source.
- Rate limiting on `POST /voice/calls` and on webhook intake (§32 metrics).
- Phone numbers masked in logs and events; never in exception text.
- Tool use during calls: unchanged permission/confirmation model (§64.12).
- Audit: call start/end, provider, duration, permission decisions, tool calls
  executed under the call's session.
- Audio channels follow the provider's encrypted media path; ULTRON stores no
  raw audio by default (§65.17).

## 65.13 CONFIGURATION AND SECRETS

```text
TELEPHONY_PROVIDER=                twilio | telnyx | plivo | mock | off
TELEPHONY_ACCOUNT_ID=
TELEPHONY_AUTH_SECRET=
TELEPHONY_PHONE_NUMBER=
TELEPHONY_WEBHOOK_BASE_URL=        public HTTPS origin, validated at startup
TELEPHONY_TRANSCRIPT_POLICY=       store_transcript | summary_only | none
```

Realtime AI credentials use the **existing** AI provider configuration (§20,
§59.7-§59.8). Documented in `.env.example`; committed values are placeholders
only. `TELEPHONY_PROVIDER=off` (default) disables the feature entirely.

## 65.14 INBOUND CALLS - ARCHITECTURALLY EXTENSIBLE

Minimum for now: the webhook intake (§65.11) parses `incoming_call` events and
records them; the full flow is specified so it can be completed without
redesign:

```text
phone → provider → ULTRON webhook → validate/verify → lookup greeting/rule
      → create CallSession (direction=inbound) → voice session → agent
      → same realtime loop as outbound (§65.7)
```

Authorization for inbound differs from outbound (a call *to* ULTRON is not a
permission to *use* ULTRON): rule-based auto-answer is deny-by-default and
scoped to allow-listed numbers (§15).

## 65.15 EVENTS

The bus is §19's bus (T030/T031). Additive names only - §19's set is not
renamed (same rule as §64.15):

```text
VOICE_CALL_CREATED    VOICE_CALL_RINGING    VOICE_CALL_CONNECTED
VOICE_CALL_LISTENING  VOICE_CALL_THINKING   VOICE_CALL_SPEAKING
VOICE_CALL_ENDED      VOICE_CALL_FAILED
```

They ride the existing SSE fan-out (T023/T320) to every subscribed interface:
Electron shows "call active", the mobile PWA shows an active-call card, the
ESP32 shows `CALLING` on the desk display over T162, logs/metrics record them
(§32), and the task system may react (§44 automation on `VOICE_CALL_ENDED`).

## 65.16 MULTI-INTERFACE CONSISTENCY

The call is another interface onto the **same** core (§62, §64):

```text
Mobile PWA   → API /voice/calls        → Core → telephony
Electron     → API /voice/calls        → Core → telephony
ESP32        → display/control events  → Core → telephony
Scheduler    → task (§65.18)           → Core → telephony
```

None of them routes through the Windows node (§65.19), and none of them needs
to be online for a scheduled call to happen. There is no separate "phone
ULTRON".

## 65.17 MEMORY AND CONVERSATION FINALIZATION

On ENDED, the call's voice session finalizes under existing memory rules
(§22, T126/T128): call metadata always; transcript/summary/actions-taken per
`TELEPHONY_TRANSCRIPT_POLICY` (§65.13) - including `none`. No audio is kept by
default; no second memory store (§65.1); extraction is intentional (§66.12's
rule applies here too).

## 65.18 TASK AND SCHEDULER INTEGRATION

"Call me at 8 PM" is an ordinary scheduled task (§44, T170) whose action is
`telephony.initiate_call`; "Call me now" is a direct API call. No separate
scheduler, no bespoke cron. Call failure is a task failure (retry policy
applies to *failed dials*, not to *answered calls that drop* - the latter is
reported, not redialed).

## 65.19 NODE PLACEMENT

Telephony lives on the **server node** (§64.7): FastAPI service, webhooks,
session coordination. It is never routed through the Windows node (D024) - the
always-on Ubuntu server must place and hold calls with Electron closed, which
is exactly why the server exists (§62.2). The provider adapter is server-side
code; the ESP32 and clients only *observe or trigger* it through Core.

## 65.20 OPENCLAW RELATIONSHIP

OpenClaw is an architectural reference for this feature **only**. ULTRON does
not install, import, launch, gateway through, or compatibly wrap OpenClaw; it
is not in the dependency graph; no OpenClaw source is copied. What is borrowed
is the shape: provider plugin + call session + gateway + agent loop, rebuilt
natively on ULTRON's own abstractions.

## 65.21 FAILURE HANDLING

A failed call must never crash ULTRON (§33). Handled explicitly: provider
unavailable/unreachable, invalid credentials (fail at startup, D011 style),
webhook timeout or malformed payload, phone unreachable/rejected/busy,
mid-call disconnect, AI/STT/TTS provider failure (§59.9 fallback chains apply
to the conversation loop), agent/tool failure (existing error taxonomy), and
server restart (CallSession rows are durable; orphaned CONNECTED sessions
recover to ENDED with `failure_reason=server_restart`).

## 65.22 OBSERVABILITY

Per-call structured record: call_id, session_id, provider, direction, status
transitions, timestamps, duration, failure_reason. Exposed as metrics
(§32/T180-T181: calls_total, call_duration, webhook_verify_failures). Never
logged: auth secrets, provider tokens, raw audio, unmasked numbers.

## 65.23 TESTING - NO REAL CALLS, EVER

`MockTelephonyProvider` + fake webhook payloads drive the suite: provider ABC
conformance, config validation (including local-base-URL rejection), call
creation, every state transition, webhook signature accept/reject/replay,
provider failure, hangup, permission rejection, event emission, session
cleanup. CI never has credentials and cannot dial; a real-call smoke test is a
manual, documented, opt-in step only.

## 65.24 DEPLOYMENT AND RESOURCE MODEL

§65.11's public HTTPS ingress (T318), reverse proxy or tunnel, production
secrets outside the repo, a `/health`-style readiness check for the telephony
service including provider reachability, and metrics in the existing
Prometheus setup. No Kubernetes, no new microservice, no broker (§64.20
preserved verbatim).

## 65.25 CONFLICTS RECONCILED

| Apparent conflict | Resolution |
|---|---|
| §25's pipeline has no phone stage | §25 stays the mic/client pipeline; §65.2 shows the same Core entry point from a second source. Provider-independent rule (§25) is what makes this fit |
| §59.16 LiveKit vs provider media streams | LiveKit = client mic-in transport; phone media terminates in the audio bridge (§65.9). Both are transports behind the voice subsystem |
| §19 event set vs `VOICE_CALL_*` | Additive, same rule as §64.15; names follow the `VOICE_` prefix family already implied by §19's STT_/TTS_ entries |
| §29 endpoint list vs `/voice/calls` | §29 says "Create REST endpoints for" - it lists examples, not an exhaustive allowlist; naming follows the plural convention |
| §64 node routing vs calls | Calls are server-node work (§65.19, D024); clients trigger, never carry |
| Cloud-first vs realtime models | §65.10: cloud adapters required for the default path, local optional (§59.20 unchanged) |

## 65.26 IMPLEMENTATION ROADMAP

Appended in tasks.md after §66's numbering neighbor as **T360-T377** (D013/D017
precedent; see the telephony block there). Dependency order:

```text
service boundary (T361) → provider ABC + config (T362) → call session (T363)
   → outbound API (T364) → first adapter (T365) → webhooks (T366) → lifecycle (T367)
   → audio bridge (T368) → STT/TTS/realtime (T369) → agent loop (T370)
   → events (T371) → memory finalize (T372) → clients (T373)
   → security (T374) → mock-provider tests (T375) → deployment (T376)
   → inbound (T377, FUTURE)
```

---

# 66. CAPABILITY INTEGRATION - CONTEXT, LIFECYCLE, ORCHESTRATION (ADDENDUM)

## 66.0 STATUS AND NON-DESTRUCTIVE RULE

This section appends after §65 and integrates twenty requested capabilities
into the architecture that already exists. For every capability the authority
is the map in §66.1: **if a subsystem already exists, it is extended; nothing
here creates a V2 of anything** (no MemoryV2, AgentRuntimeV2, EventBusV2,
VoiceV2, TaskEngineV2). Existing sections are not rewritten; where this
section formalizes something the spec already implied, it says so.

Documentation and roadmap only. §54, §56 and tasks.md rule 1 apply unchanged.

## 66.1 CAPABILITY MAP - THE TWENTY FEATURES

| # | Requested capability | Lives in | Verdict |
|---|---|---|---|
| 1 | Context engine | T046 `core/context.py`, §22, §7 | **Extended** - §66.3, T380 |
| 2 | Goal→Plan→Execute→Verify | §1, §17, §18, T048-T050 | **Formalized** - §66.4, T381 |
| 3 | Multi-agent orchestration | §7, §41, §59.21, T042-T045 | **Extended** - §66.5 |
| 4 | AI model routing & fallback | §20, §59.7-§59.9, T220-T224 | **Extended** - §66.6, T383 |
| 5 | Permission & security engine | §15, §31, §64.12, T033 | **Extended** - §66.7 |
| 6 | Event bus | §19, T030/T031 | **Extended** - §66.8, T384 |
| 7 | Multi-node discovery & routing | §64, §13, T330-T336 | **Done by §64** - gap task T385 |
| 8 | Computer vision & computer use | §13, §42, §59.15, T143-T145, `agents/vision` | **Extended** - §66.10, T386 |
| 9 | Autonomous background agents | §18, §44, T170-T172, T044 | **Extended** - §66.11, T387 |
| 10 | Advanced memory | §22, T120-T129 | **Extended** - §66.12, T388 |
| 11 | Knowledge / RAG | T124/T127 retrieval + pgvector (`memories.embedding`, `ENABLE_PGVECTOR`) | **Extended** - §66.13, T389 (ingestion is the gap) |
| 12 | Universal tool & plugin system | §14, §59.6, T230-T232 | **Extended** - §66.14, T390 |
| 13 | Self-diagnostics & self-healing | §32, §33, T180-T183 | **Extended** - §66.15, T391 |
| 14 | Action history & audit | §31, `tool_executions`, T041 events | **Extended** - §66.16, T392 |
| 15 | Undo / rollback | not present | **New, constrained** - §66.17, T393 |
| 16 | Continuous voice & wake word | §25, §59.17-§59.20, T150-T155, T250-T255 | **Unchanged** - pointed to, no VoiceV2 |
| 17 | Phone call interface | §65, T360-T377 | **Done by §65** - referenced in §66.19 |
| 18 | Agent-to-agent communication | §41 passes results; no message contract | **New** - §66.20, T382 |
| 19 | Multi-interface support | §62, §63, §64 | **Done by §62-§64** - §66.21 records it |
| 20 | Node capability management | §64.10, §64.14, T331-T336 | **Done by §64** - §66.9 binds it to planning |

Reading the Verdict column: five are already delivered by an existing section
(§62-§65 and §64); twelve extend a named subsystem in place; one formalizes the
lifecycle the spec already implies; two add genuinely new contracts (rollback
and agent-to-agent). Not one of the twenty creates a second system of
anything - see §66.23.

## 66.2 TARGET ARCHITECTURE - UNCHANGED PRINCIPLES

```text
                         ULTRON CORE
        context · memory · planner · orchestrator · AI router
        permissions · event bus · verification
                              |
          +-------------------+-------------------+
          |                   |                   |
       AGENTS               TOOLS               TASKS
          |                   |                   |
   coding research system   files git browser   scheduler
   voice decision vision    os computer web     background
          |
   NODE CAPABILITY LAYER (§64.10 capability registry)
          |            |            |
      Windows        Ubuntu        ESP32        (+ telephony, a
       node          node          node          service on the node)
```

The central principle is the one already written (§1, §64.2), restated once:
**AI decides. Agents reason. Tools act. Nodes execute. Events report. Memory
persists. Permissions govern. Verification confirms. Interfaces communicate.**
Every capability below must be traceable to one of those verbs; anything that
would add a new verb is out of scope.

## 66.3 CONTEXT ENGINE (extends T046)

One context layer, one instance. `app/core/context.py` (T046) grows from
"conversation state + namespaces" into the ranked, bounded provider of record:

```text
ContextManager
 ├── collect()              conversation, task, agent, node, project/repository,
 │                          recent actions/tool calls, device state, preferences,
 │                          memory (§22), knowledge (§66.13), background tasks,
 │                          current session, voice session, phone call (§65),
 │                          effective permissions
 ├── resolve()              which sources apply to this request
 ├── rank()                 task-specific relevance, never everything
 ├── compress()             bounded size before the model (§59.25 budgets)
 ├── build_prompt_context() the only shape agents hand to a model
 └── clear_expired_context()
```

Rules: context is gathered per-namespace and filtered by permission before it
is ever ranked (a principal cannot surface another principal's memory through
context); callers are agents, planner, voice sessions, tools and UI - all
through this one interface; no per-feature context copies.

## 66.4 EXECUTION LIFECYCLE - GOAL → PLAN → EXECUTE → VERIFY (formalizes §1/§17/§18)

Not a new runtime - this is §1's Core lifecycle plus §17's verification and
§18's task graph, written as one contract and implemented by the already-planned
T048 (planner), T049 (orchestrator), T050 (executor retry/replan):

```text
USER GOAL → UNDERSTAND → CONTEXT(§66.3) → PLAN → PERMISSION CHECK(§15)
  → AGENT SELECTION(§7) → NODE SELECTION(§64.10) → TOOL EXECUTION(§16)
  → OBSERVATION → VERIFY(§17) → success? yes → COMPLETE
                                      no  → retry / replan / escalate
```

The planner supports single-step, multi-step, dependent, parallel-where-safe
steps, retries, replanning, verification, human confirmation, cancellation and
pause/resume - as graph operations on the existing task DAG (T040/T041), not as
an isolated planner agent.

## 66.5 MULTI-AGENT ORCHESTRATION (extends §7, §41, §59.21)

The agent inventory (coding, research, browser, system, git, decision, voice,
iot, server, computer, vision, file, automation - §59.22, `app/agents/`) is
complete as *types*. What §66 adds is the orchestration contract on top of
T044 (manager) and T049 (orchestrator): selection criteria, sequential and
parallel invocation, structured results passed between agents (§66.20),
verification of agent output, retry/terminate, permission enforcement per
invocation, and an execution history per agent (§66.16). Each agent keeps its
identity, capabilities, tools, model policy, permissions, context needs, scope,
timeout, resource limits and result format - the 12 attributes §6 already
declares.

## 66.6 AI MODEL ROUTING AND FALLBACK (extends §20, §59.7-§59.9)

One router (§20), extended - never a second one. Selection inputs grow to: task
type, agent type, capability, latency, cost, context size, quality,
availability, privacy and local/cloud preference (§59.8's registry already
carries capability and availability). Fallback chains (§59.9's
capability-by-capability chains, T223) gain the operational half: **provider
health tracking and temporary circuit breaking** - a provider failing fast is
skipped for a cooldown instead of being retried on every call. Providers stay
config-selected (OpenRouter free-tier first per §59.7, Ollama optional,
OpenAI/Gemini/Anthropic/cloud realtime behind the same adapter interface); no
provider is hard-coded, and no provider type leaks into agents.

## 66.7 PERMISSION AND SECURITY ENGINE (extends §15, §31, §64.12)

Still **one** engine, evaluated as (principal, agent, tool, node, action,
target resource, session, confirmation) - §15's LEVEL 0-5 scale and §64.12's
node scope, combined into the single decision the Permission Manager already
makes (T033). Added clarifications, not new subsystems: risk is an attribute
the tool declares (§66.14), confirmation is required for level 4-5 and for
everything marked irreversible (§66.17), and every decision - allow, deny,
confirm-required - produces an audit row (§31). Tools do **not** grow their own
permission logic; they declare, the engine decides.

## 66.8 EVENT CATALOG (extends §19)

Additive names on the one bus (T030/T031), same no-rename rule as §64.15 and
§65.15. Requested names that do not yet exist are added: `CONTEXT_UPDATED`,
`PLAN_CREATED`, `PLAN_UPDATED`, `NODE_CAPABILITIES_UPDATED`,
`VOICE_STARTED/LISTENING/THINKING/SPEAKING/ENDED`, `VOICE_CALL_*` (§65.15),
`MODEL_SELECTED`, `MODEL_FAILED`, `PERMISSION_REQUESTED/GRANTED/DENIED`,
`SYSTEM_ERROR`. Names already in §19 (`TASK_*`, `AGENT_*`, `TOOL_*`, `DEVICE_*`,
`MODEL_REQUEST/RESPONSE`, `WAKE_DETECTED`, `STT_*`, `TTS_*`, `CPU_HIGH`…,
`SCHEDULE_TRIGGERED`) and in the §64.9/§64.15 canonical additions
(`NODE_ONLINE`/`NODE_OFFLINE` and their `SERVER_`/`WINDOWS_` forms) keep their
spellings; the `DEVICE_*` spelling mismatch is the one §64.9 names - one
event, one spelling, decided in T335. Consumers stay as they are: UI, ESP32,
logging, memory, notifications, automation, monitoring, task engine, agents -
no message broker is introduced.

## 66.9 NODE DISCOVERY AND CAPABILITY MANAGEMENT (binds §64 to planning)

§64.5/§64.10/§64.14 define registry, capabilities and availability; T331-T336
implement them. The remaining gap is **binding them into planning** (T385): the
planner and executor must answer, at run time - which node can perform this
action, is it online, is the capability available, does the permission exist
for that (node, tool), is another node better, and what is the fallback if it
goes offline - from the registry, never from a hard-coded node assumption.
Each node advertises exactly the shape §64.10 already specifies (node_id, type,
status, platform, capabilities, tools, version, connection state, health,
permissions, metadata).

## 66.10 COMPUTER VISION AND COMPUTER USE (extends §13, §42, §59.15)

Structured tools only - the §16 path holds: MODEL → STRUCTURED TOOL REQUEST →
PERMISSION → NODE TOOL EXECUTOR → OS → OBSERVATION → MODEL. Screenshot, OCR,
element detection, click/type/scroll/window management are the T143 tool set on
the Windows node (§64.6) with the T145 capability fallback chain (API → DOM →
UIA → shortcuts → mouse → vision). The addition is the **verification loop**
(T386): act → screenshot again → confirm expected state → else retry/escalate -
so "Click Download" is verified, not assumed (§17). Ubuntu exposes equivalent
Linux capabilities through the server node when needed. An LLM never reaches
the OS directly.

> **Amended after §68 (perception addendum):** §66.10 is the **action** side
> of vision - a tool in the §16 pipeline, invoked to *do* something. §68 adds
> the **observation** side - cameras and sensors producing context events on
> their own. The split is deliberate and binding: perception observes and
> feeds the context engine; computer-use tools act and require permission.
> Neither replaces the other, and no code path goes camera → OS (§68.12).

## 66.11 AUTONOMOUS BACKGROUND AGENTS (extends §18, §44)

Long-running agents (scheduled research, server/Git monitoring, maintenance,
reports, backups, health watch, notification agents) run on the **existing**
scheduler (T170-T172) and task DAG (T040/T041) with the T044 manager lifecycle:
scheduling, cancellation, pause/resume, retry, timeout, resource limits, logs,
result storage, notifications (§45) and permission enforcement. Two explicit
guards against uncontrolled loops: every background agent runs under a bounded
plan (§66.4) with a max-iteration/timeout policy, and emits events so
self-diagnostics (§66.15) can stop a runaway.

## 66.12 ADVANCED MEMORY (extends §22)

The §22 layers already exist as tasks (T120-T129: short-term, long-term,
episodic, semantic, project, manager facade). The extension (T388) is the type
set and the discipline: preference, task, device and agent memory as
**namespaces/scopes on the same store**, not new stores; creation, retrieval,
ranking, updating, deduplication, importance, timestamps, source tracking,
confidence and decay where appropriate; privacy boundaries enforced by the
existing namespace rules (`user`, `project:*`, `agent:*`). Memory extraction
is intentional - conversation messages are not memorized by default (§65.17's
transcript policy is the telephony instance of this rule).

## 66.13 KNOWLEDGE / RAG (extends T124/T127, existing pgvector)

Retrieval exists (semantic memory + pgvector behind `ENABLE_PGVECTOR`); the gap
is **ingestion** (T389):

```text
SOURCE (PDF, docs, project files, Git repos, websites, notes, manuals)
  → INGEST → PARSE → CHUNK → EMBED → INDEX   (into the existing vector store)
  → RETRIEVE → RERANK → agent context (§66.3)
```

One vector database: PostgreSQL/pgvector (`memories.embedding`, `Vector(768)`
or jsonb fallback) - no second vector store (D030). Embeddings go through the
existing provider abstraction (T127). Retrieval is permission-scoped: a chunk
is only retrievable by a principal who could read its source.

## 66.14 UNIVERSAL TOOL AND PLUGIN SYSTEM (extends §14, §59.6)

The registry (§59.6) gains the fields that the rest of §66 needs (T390):
`output_schema`, `node_requirements` (which capability the target node must
advertise), `timeout`, `risk_level`, `availability`, `version`, and
`reversibility` (§66.17). Plugins - telephony among them, and OpenClaw-shaped
integrations generally - stay isolated from core logic behind this interface;
adding one changes no core module. The inventory (filesystem, browser,
web_search, git, powershell, linux_shell, computer_use, notifications, ESP32,
telephony, calendar/email later) is a registry list, not a code fork.

## 66.15 SELF-DIAGNOSTICS AND SELF-HEALING (extends §32, §33)

One health matrix over what is already monitored (T180-T183 metrics, §32):
server, agents, nodes, WebSockets/SSE, API, database, AI providers, tool
providers, voice, telephony (§65.22), ESP32, background jobs. Recovery follows
FAILURE → DIAGNOSE → RECOVER → VERIFY → NOTIFY, with a bounded action menu:
reconnect (WebSocket/node/ESP32), retry with backoff, switch provider on the
§59.9 fallback, restart a failed worker, recover an interrupted task. Recovery
respects permissions: self-healing may repeat *failed* work through the same
checks; it may not grant itself new capabilities, and every recovery action
emits an event and an audit row.

## 66.16 ACTION HISTORY AND AUDIT (extends §31, `tool_executions`)

The rows exist (`tool_executions` with permission_level/decision, audit log,
T041 task events). T392 makes them **queryable as one execution record**:
user request → plan → agents → tools → nodes → permission decisions → results
→ errors → timestamps → model/provider → verification outcome, correlatable by
request/correlation id (already emitted by T021's middleware). This feeds
debugging, the UI, and audit review - it is a view over existing tables, not a
new logging system.

## 66.17 UNDO / ROLLBACK (new, constrained)

```text
ACTION → SNAPSHOT or REVERSIBLE OPERATION → EXECUTE → VERIFY
```

- Every tool declares `reversible | partially_reversible | irreversible`
  (§66.14 field); the registry shows it before execution.
- Reversible where technically possible: file modifications (snapshot/copy),
  Git operations (branch/ref state, §59.3 already gates destructive ones),
  configuration changes.
- Irreversible actions require the stronger confirmation path (§66.7).
- **No universal undo is claimed.** §66.17 exists so the boundary is declared,
  not to promise reversal of anything with side effects outside ULTRON's reach.

## 66.18 CONTINUOUS VOICE AND WAKE WORD (existing voice, unchanged)

Wake word, VAD, streaming/partial STT, barge-in, streaming TTS, voice states
and session memory are §25 + §59.17-§59.20 + T150-T155/T250-T255 - all already
planned, with the SLEEP → WAKE → LISTEN → THINK → SPEAK → LIST loop being
§59.13's state machine plus §59.19's pipeline. Voice sessions run through the
normal agent/task system; there is no voice-only intelligence layer. This
section adds no tasks of its own beyond §66.3's context hook.

## 66.19 PHONE CALL INTERFACE (§65)

Telephony is specified in §65 (provider abstraction, call sessions, webhooks,
realtime, security, T360-T377). Repeated here only to fix its place in the
map: phone is one more interface on the same core, its provider is a cloud
service and never a node (§64.17 amended), and OpenClaw remains reference-only
with zero dependency (§65.20).

## 66.20 AGENT-TO-AGENT COMMUNICATION (new contract)

Structured messages, passed through the orchestrator - not free-form agent
chat:

```text
AgentMessage
 ├── task_id · sender · receiver · objective
 ├── input · result · evidence · confidence
 ├── errors · requested_next_action
```

Planner → Research → Coding → Testing → Review → Planner is a chain of these
messages on one task graph (§66.4), each hop subject to permissions and
recorded in the execution history (§66.16). The orchestrator owns lifecycle;
uncontrolled agent-to-agent conversation loops are out of scope.

## 66.21 MULTI-INTERFACE SUPPORT (records §62-§64 as the answer)

Windows Electron (interface + Windows node), mobile web (control client), web
UI, ESP32 (physical interface + device node), phone calls (§65), future
interfaces - all reach one core through API / SSE / device WebSocket / voice
gateway. Interfaces never duplicate intelligence: UI → API/SSE/Voice gateway →
Core → agents/tools/nodes. This is §62-§64 written as a principle; no new work
beyond the interface tasks already listed there (T315-T322, T260-T262,
T160-T167, T350-T351, T373).

> **Amended after §67:** the spatial 3D UI (§67) joins this set as one more
> interface on exactly the same principle - a renderer inside the existing
> Electron client, reached through API/SSE, holding no intelligence of its
> own. It is a mode of the client, not a new client (§67.2).

## 66.22 SECURITY, RESOURCES, DEPLOYMENT - PRESERVED

Unchanged and binding on every capability above: least privilege; explicit
permissions; node authentication (§64.13); API authentication (§30); secure
streams (SSE/T162/provider media); credential isolation and secrets in config
(§34); audit logs (§31); schema validation (§16); rate limiting; confirmation
for sensitive actions; safe failure (§33); **never LLM → arbitrary shell →
OS** (§16 path, stated in §1). Cloud-first and lightweight: cloud AI /
OpenRouter, optional local models, 4 GB Ubuntu server, 8 GB Windows laptop,
ESP32 constraints (§59.25, §64.20). No Kubernetes, no unnecessary
microservices, no new brokers - modular monolith as before.

## 66.23 DUPLICATE-SYSTEM CHECK AND CONFLICTS

| Check | Result |
|---|---|
| Second memory system for knowledge? | No - pgvector store reused (D030) |
| Second agent runtime for planning? | No - planner/orchestrator/executor T048-T050 on the existing manager |
| Second event bus for new events? | No - §19 bus, additive names |
| VoiceV2 for calls/continuous voice? | No - §25 + §65 both attach to T153's sessions |
| TaskEngineV2 for background agents? | No - §44 scheduler + T040/T041 graph |
| Node registry beside §64's? | No - §64.10 is the only registry (D021) |
| New permission scale alongside LEVEL 0-5? | No - §15 scale, scoped grants (D020) |
| OpenClaw as a dependency? | No - reference only (§65.20) |
| Phase numbering replaced with the requested 0-21 phases? | No - mapped onto existing Phases 1-13 + blocks (D031) |

## 66.24 ROADMAP AND DEPENDENCY ORDER

New work is **T380-T394**, appended in tasks.md after the telephony block
(D012/D013/D017 precedent). Existing tasks referenced rather than duplicated:
T046/T048-T050 (core), T042-T045 (agents), T030/T031/T033 (bus, permissions),
T120-T129/T124/T127 (memory, vectors), T150-T155/T250-T255 (voice),
T170-T175 (scheduler), T180-T186 (observability), T220-T224/T223 (routing),
T230-T232 (tools), T330-T354 (nodes), T360-T377 (telephony), T143-T145
(computer use).

```text
context engine (T380)
   → execution lifecycle (T381)
       → agent-to-agent contract (T382)
       → capability-aware node selection (T385)
       → model routing hardening (T383)
       → tool registry hardening (T390)   [before large plugin expansion]
           → computer-use verify loop (T386)
           → background agent lifecycle (T387)
           → knowledge ingestion (T389)   [after context reads exist]
           → memory type expansion (T388)
       → event catalog additions (T384)   [before deep autonomy]
       → action history (T392) → rollback (T393)
       → self-diagnostics (T391)          [after events + health metrics]
   → cross-capability integration test (T394)
```

Security ordering preserved: permission work (§66.7, T033) precedes powerful
OS tools; node capability management precedes advanced multi-node planning;
event additions precede autonomous/background integration.

**This section is documentation.** Nothing in §66 is implemented by virtue of
being written here.

---

# 67. ULTRON SPATIAL INTERFACE (USI) (ADDENDUM)

A 3D / holographic-style spatial interface inside the existing Electron
client, controlled by mouse, keyboard, touch, voice, camera-based hand
gestures, and future sensors. This section specifies an **extension layer**:
it adds a renderer and an interaction vocabulary to the client that §63/§64
already define. It creates no new core, no second agent system, no second
event bus, no second component architecture, and no second permission system.

## 67.0 STATUS AND NON-DESTRUCTIVE RULE

Same rule as §64.0/§65.0/§66.0: **additive only.** Everything here attaches
to a subsystem that already exists or is already tasked:

| USI piece | Attaches to |
|---|---|
| Spatial renderer host | Electron client shell (T340, §59.14) |
| Scene / component data | API + SSE event stream (T023/T320, §19, §29) |
| Gesture + spatial events | the one §19 bus, additive names (§66.8 rule) |
| Interaction authority | permission system (§15, §64.12, §66.7) via the §16 pipeline |
| Voice + gesture fusion | context engine (§66.3, T380) + existing voice sessions (§25, §59.19) |
| Selection / scene state | context engine session state + existing memory scopes (§22, §66.12) |
| Persistent workspaces | existing workspace/project persistence (§46, `workspaces/`) |
| Metrics | §32 observability |

**Honest audit note:** this repository contains **no "Dynamic UI" engine
today** - the phrase in the request ("the existing Dynamic UI system")
refers to a 2D component layer that does not yet exist in `server_arc.md`,
`tasks.md`, or `clients/` (the Electron shell T340 is still an unbuilt
placeholder). Rather than inventing two registries later, §67.5 defines
**one component protocol** that both the 2D layer and the spatial layer
implement. Whichever arrives first, the other extends it - there will never
be a dynamic-UI registry *and* a spatial registry.

**This section is documentation.** Nothing in §67 is implemented by virtue of
being written here (§54: do not fake features).

## 67.1 PURPOSE

ULTRON should be able to *show* - as spatial objects the user can walk around
in - 3D models, images, graphs, charts, datasets, diagrams, flowcharts,
system information, agent graphs, task timelines, documents, dashboards,
simulations, UI panels, and arbitrary spatial objects. The spatial interface
must feel like an extension of ULTRON: same identity, same permissions, same
events, same voice, same tools. Not a separate application.

## 67.2 INTERFACE MODES AND WHEN SPATIAL IS CHOSEN

Three render modes for one client, selected per response:

```text
USER → ULTRON CORE → INTENT / CONTEXT → RESPONSE PLANNER
   → INTERFACE SELECTION
        ├── TEXT          (chat)
        ├── COMPONENT     (2D: the shared component registry, §67.5)
        └── SPATIAL       (3D scene graph, §67.4)
               → SpatialScene → Electron renderer
```

Rules:

1. **Spatial is chosen when it improves understanding or interaction**, never
   by default. "Plot this function" → 2D may suffice; "show me this molecule"
   → 3D; "explain ULTRON's architecture" → interactive spatial diagram;
   "show my server status" → spatial dashboard. Do not force everything into
   3D.
2. The **response planner decides the mode** as part of planning (§66.4) -
   the model proposes a mode with the response, and the *client* is free to
   fall back (§67.15). The model never ships renderer code (§67.13, §30).
3. Modes are also switchable by voice/command independent of any response:
   "enter spatial mode" / "exit spatial mode" (§67.20).
4. This is §66.21 multi-interface support with one more member: a **mode of
   the existing client**, reached through the existing API/SSE, holding no
   intelligence of its own.

## 67.3 SPATIAL UI ENGINE

A logical engine inside the Electron renderer process - a set of modules, not
a microservice (§59.27: modular monolith discipline applies to the client
too):

```text
SpatialUIEngine (renderer process, one per window)
    ├── SceneManager        load/validate/swap scenes, scene state
    ├── SceneGraph          the §67.4 document, live-edited
    ├── ObjectManager       object lifecycle, groups, labels, isolate/hide
    ├── CameraController    orbit/pan/zoom/focus/reset, bounded moves
    ├── InteractionManager  pointer + touch + gesture dispatch → resolver
    ├── GestureInputAdapter subscribes to §68 GESTURE_* on the bus (§67.9)
    ├── VoiceInteractionAdapter  structured spatial commands (§67.13)
    ├── AnimationManager    bounded, interruptible, reduced-motion (§67.14)
    ├── LayoutEngine        panel placement, flowchart/timeline layout
    ├── SelectionManager    single source of selection truth (§67.12)
    ├── StateManager        mode, workspace, undoable view state
    └── RendererAdapter     thin seam over the chosen 3D library (§67.6)
```

The engine **consumes** events and **emits** structured actions (§67.13). It
has no model access, no tool access, and no direct database or filesystem
access - it is a renderer, exactly as §66.21 requires of every interface.

## 67.4 SPATIAL SCENE GRAPH

A `SpatialScene` is a **strict, versioned JSON document** - data, never code.
It travels over the existing API (persist) and SSE (live updates).

```json
{
  "scene": { "id": "server-overview", "type": "spatial_scene", "version": 1,
             "mode": "SYSTEM_VIEW", "workspace": "ultron-server" },
  "camera": { "position": [0, 2, 6], "target": [0, 0, 0], "fov": 50 },
  "environment": { "lighting": "neutral", "background": "grid" },
  "objects": [
    { "id": "server.cpu", "type": "spatial_panel", "position": [0, 1, 0],
      "rotation": [0, 0, 0], "scale": [1, 1, 1], "interactive": true,
      "props": { "source": "metric:cpu", "refresh_ms": 2000 } }
  ],
  "relationships": [ { "from": "server", "to": "server.cpu", "kind": "contains" } ],
  "interactions": [ { "object": "server.cpu", "on": "pinch", "action": "spatial.focus" } ],
  "animations": [],
  "permissions": { "read": "any", "mutate": "LEVEL_3" },
  "state": { "selected": null, "expanded": [] }
}
```

Rules (each testable):

- **Schema-validated end to end**: one JSON Schema for the scene, one for
  each object type, Draft 2020-12, `additionalProperties: false`. Validation
  runs on the server before persist/emit *and* on the client before render -
  the same discipline as §16 gives tools. Unknown object `type` → rejected
  (§67.5 fallback, never a blank canvas).
- **No executable content**: no JavaScript, HTML, shaders, URLs-to-execute,
  or arbitrary expressions inside a scene. `props` are data bound to
  *approved* component inputs only. This is the prompt-injection firewall
  (§30): the AI produces a validated scene *specification*; the renderer
  interprets approved schemas.
- **Stable object ids** (`server.cpu`, `agent.planner`, `task.T041`): the
  vocabulary voice and gestures use to refer to things (§67.12).
- **Object types** (initial closed set): `model`, `spatial_image`,
  `spatial_text`, `spatial_graph`, `spatial_chart`, `spatial_panel`,
  `spatial_button`, `flowchart_node`, `spatial_timeline`, `spatial_document`,
  `spatial_video`, `simulation`, `agent`, `task`, `device`,
  `system_resource`. New types are a registry entry (§67.5), never an open
  string.
- **Permissions inside the document** are advisory display metadata; the
  authority is always §15/§64.12 (§67.16). A scene cannot grant itself rights
  it does not have.

## 67.5 COMPONENT REGISTRY - ONE PROTOCOL, TWO RENDERERS

To avoid the duplicate architecture the brief forbids, **one component
protocol** serves 2D and spatial:

```text
ComponentSpec (discriminated union: { type, id, props, children?, ... })
    ├── COMPONENT registry (2D renderers: text, table, graph, form, flowchart, ...)
    └── SPATIAL registry (3D renderers:)
            model · spatial_image · spatial_graph · spatial_panel
            spatial_chart · spatial_diagram · spatial_timeline
            spatial_dashboard · spatial_simulation
```

- A registry entry declares: `type`, JSON Schema for `props`, renderer class
  (2D or spatial), capability requirements (e.g. WebGL), and a **2D
  fallback renderer** where one exists (a `spatial_graph` degrades to the 2D
  graph component - §67.15 is a property of the registry, not a hope).
- The spatial registry is a **namespace under the same registry
  architecture**, not a parallel one: same registration validation, same
  schema check, same "unknown type is an error" rule the tool registry
  (§59.6) and the prompt's Dynamic UI both rely on.
- Because neither registry exists in code today, the S1 phase (tasks.md)
  lands the *protocol* first, so both future renderers implement it.

## 67.6 3D RENDERING

The renderer lives in the **Electron application** (T340's renderer process),
not on any server node - interactive UI workload, local by design (§64.6).

```text
Electron (T340)
    → SpatialUIEngine (§67.3)
        → RendererAdapter
            → WebGL/WebGPU-capable rendering layer
                → 3D Scene
```

**Technology selection is a task with a gate (S2.1), not an assumption:**
inspect the frontend stack when T340 lands, then choose the *simplest*
library that (a) renders WebGL, (b) loads glTF/GLB via established loaders,
(c) supports picking/selection, (d) has a maintenance record. Candidates:
**three.js** (smallest, most boring, framework-free), **Babylon.js**, or
**React Three Fiber - only if T340 chose React** (the Next.js PWA §63 is
React; the Electron renderer is T340's call). No custom 3D engine, no
hand-written loaders (§54).

Renderer must support: camera movement, object transforms, lighting,
materials, textures, model loading, animations, selection, highlighting,
grouping, spatial panels, labels, connection lines, optional particle/effect
layers, and responsive resize. **Culling, level-of-detail, and frame budget
are §67.17 requirements**, not afterthoughts.

## 67.7 MODEL AND ASSET SUPPORT

Established libraries parse established formats: **GLTF, GLB** first, **OBJ**
if required, **STL** where relevant. The user-facing behaviours to support:
rotate, pan, zoom, focus, reset, inspect, select, highlight, isolate,
hide/show, and explode view where the asset allows it.

Example: "Show me this engine." → the planner emits a scene containing the
model → renderer loads it, centers the camera, enables interaction. Asset
fetching uses the existing API/file paths with the normal permission checks;
assets are cached client-side per §67.17.

## 67.8 SPATIAL IMAGES, DATA, FLOWCHARTS, DASHBOARDS

- **Images** become spatial objects: floating panels, galleries, comparison
  walls, stacks, zoomable images, selection-enlargement. Gestures: point →
  hover/select, pinch → select, pinch+move → move, two-hand spread → zoom,
  swipe → next. Same interaction protocol as every other object (§67.10).
- **Data visualization**: existing 2D graphs/charts gain *spatial versions*
  (3D scatter, bar structures, network graphs, time-series landscapes,
  clusters, heatmaps, system metrics) as additional registry entries. The 2D
  versions are never replaced - spatial is offered where it helps (§67.2).
- **Flowcharts**: the component-level flowchart (§67.5) can enter spatial
  mode - nodes float in 3D, relationships become 3D connections, active
  nodes highlight (live status from `TASK_*`/`AGENT_*` events), nodes expand,
  the scene rotates. The **task DAG (T040/T041) and agent graph** are natural
  scene sources: the planner's own structure, visualized.
- **Dashboards** are *composed* from reusable spatial components - server
  (CPU/RAM/disk/network/processes/services), ULTRON (agents/tasks/tools/
  memory/providers/devices), project (git/build/tests/files/issues). No
  hard-coded dashboard; a dashboard is just a scene document (§67.4).

## 67.9 GESTURE INPUT PIPELINE

Hand gestures arrive from the **§68 perception layer** - USI does not run its
own computer vision (the prompt's camera sections are merged into §68; see
§67.23):

```text
Camera → Python vision service (local, §68.4) → hand tracking →
semantic gesture events (§68.6) → §19 bus (GESTURE_*) →
GestureInputAdapter (this client) → InteractionManager →
Interaction Resolver (§67.10) → spatial action (§67.13) → renderer
```

**Raw camera coordinates never reach the UI as the interaction API** - that
is the whole point of §68.6's semantic layer. The adapter consumes semantic
events (`point`, `pinch_start`, `pinch_end`, `grab`, `release`, `swipe_*`,
`open_palm`, `fist`, `two_finger`, `rotate`, `zoom_in`, `zoom_out`, `hold`)
with normalized positions (0-1), hand (left/right), confidence, timestamp.

Mouse, keyboard, and touch flow into the **same InteractionManager** through
the same resolver - gestures are an input device, not a parallel input
system. If the camera, the vision service, or hand tracking is unavailable,
the pipeline simply has one fewer device (§67.15).

## 67.10 INTERACTION RESOLVER AND CONTEXT-AWARE GESTURES

The resolver is the critical piece: **a gesture means different things in
different contexts**, and that mapping must be deterministic and testable -
never an LLM call per frame.

```text
gesture + current scene + selected object + object interaction rules +
current ULTRON mode + pointer state
        → Interaction Resolver (local, synchronous)
        → structured spatial action
```

A pinch, by context: on a model → grab/rotate; on a graph → pan; on an
image → select; on a menu → click; on a slider → adjust; on free space →
select nearest object. **Priority order (documented, tested):** active drag >
focused control > selected object's rules > scene default > global default.
The resolver is a pure rule table over (gesture, context) - unit-tested
without hardware, parametrized over the rule matrix (§67.22).

Object interaction rules live **in the scene document** (§67.4
`interactions`), validated against the action protocol (§67.13) - an object
can only declare actions that exist.

## 67.11 LOCAL VS AI RESPONSIBILITY

Binding split (the prompt's "do not send every hand movement to the LLM"):

| Handled locally (renderer, synchronous) | Handled by AI/core (semantic, slow) |
|---|---|
| 30 Hz gesture stream → resolver → immediate feedback | "explain this object" |
| select/grab/rotate/zoom/pan/navigate | "open this dataset" |
| hover, highlight, focus, camera moves | "compare these" |
| drag/drop, slider adjust, button click | mode changes by conversation |
| animation of its own results | planning which scene to build |

Only **meaningful semantic events** cross into the core (§68.11's
throttling): a completed selection, an explicit object gesture with
confidence above threshold, a spatial command. The architecture:

```text
Camera → Vision → local gesture interpreter → Interaction Resolver →
immediate UI response        (no core round-trip, no LLM)

semantic events only → §19 bus → context engine → ULTRON reasoning
```

## 67.12 VOICE + GESTURE FUSION AND SPATIAL REFERENCES

Fusion uses the **existing** context engine (§66.3/T380) - not a new memory
system. Example: user says "Show me the CPU." → scene built with object
`server.cpu`; user points → `SelectionManager` sets `selected =
server.cpu` and publishes selection as context; user says "Zoom into that."
→ core resolves "that" from (voice context `CPU` + gesture POINT +
selection `server.cpu`) and emits `spatial.focus`.

Requirements:

- **Stable object ids** are the reference vocabulary (§67.4).
- **Selection state is exposed to the core** as session context (context
  engine, §66.3): current scene, selected object, expanded groups, workspace
  - a handful of typed values, not a new store (§22 remains the only memory).
- **Spatial commands** from voice/text: "show this in 3D", "rotate it",
  "zoom in", "move that to the left", "hide this", "show details", "compare
  these", "expand this", "focus on this", "explain this", "open this",
  "go back", "reset the scene" - each compiled to a validated action from
  §67.13, never to free-form renderer code.

## 67.13 SPATIAL ACTION PROTOCOL

All mutations of the scene go through one structured protocol:

```json
{ "action": "spatial.select", "scene_id": "server-overview",
  "object_id": "server.cpu", "source": "gesture|voice|ui|agent",
  "request_id": "…", "confidence": 0.94 }
```

Initial vocabulary: `spatial.select`, `spatial.focus`, `spatial.move`,
`spatial.rotate`, `spatial.scale`, `spatial.hide`, `spatial.show`,
`spatial.expand`, `spatial.collapse`, `spatial.inspect`, `spatial.reset`,
`spatial.navigate`, plus `spatial.set_mode` (§67.20).

Rules: **schema-validated** (a bad action is a 422, never a renderer
exception); **audited** through the same §31/§66.16 action-history path when
it crosses into core-side effects; and **permission-checked** when the action
leads to anything beyond view state (§67.16). View-only actions (select,
focus, rotate camera) are local and free; object- or system-affecting actions
are not.

## 67.14 ANIMATION SYSTEM

Transitions, object movement, camera movement, highlighting, expand/collapse,
flow animations, data updates, spawn/remove. Hard requirements, each
testable: **bounded duration** (no open-ended animations), **interruptible**
(a new input supersedes), **reduced-motion honoured** (accessibility +
`prefers-reduced-motion`), **never blocking interaction**, and **cleaned up**
(no orphan timers/listeners on scene swap). Animation must never be required
to understand content - the final state is reachable with animation off.

## 67.15 FALLBACK BEHAVIOR (mandatory)

Spatial UI must never be a single point of failure:

```text
3D → 2D component registry → text
gesture → mouse/touch/keyboard
camera / vision service / hand tracking unavailable → normal input only
renderer init fails or WebGL absent → 2D immediately, flag reported
```

The renderer adapter reports capability at startup; the client enters 3D
only when the capability probe passes. A mid-session renderer failure
degrades to 2D with the scene still available as data (the document is the
truth, the renderer is a view). The fallback chain is part of each registry
entry (§67.5) and is tested by simulating failure (§67.22).

## 67.16 SECURITY - SPATIAL NEVER BYPASSING PERMISSIONS

Mandatory pipeline, identical for every entry route (gesture, voice, UI click,
agent):

```text
gesture/voice/UI → spatial action → schema validation → §15 permission check
(LEVEL 0-5, §64.12) → [confirmation UI if required] → tool (§16 pipeline)
→ node (§64) → execution → §17 verification → §31 audit
```

- A gesture can never become a shell command, filesystem access, or app
  launch: gestures produce *actions*, actions pass permissions like any
  other request.
- Destructive examples get **explicit confirmation** rendered in the scene
  (LEVEL 4-5 and irreversible per §15/§66.17): pinch on "Shutdown Ubuntu
  server" → scene shows "Shutdown Ubuntu server?" [Cancel] [Confirm] →
  only then the tool pipeline runs.
- **Electron hardening** is part of this phase: `contextIsolation` on, no
  `nodeIntegration`, preload whitelist for IPC, scenes loaded only through
  validated IPC messages (T340's main/preload/renderer split).
- The AI generates scene **specifications** only (§67.4); no generated
  JavaScript/HTML/shaders are ever executed (§30), closing the
  prompt-injection route into the renderer.

## 67.17 PERFORMANCE

Budget: an 8 GB development laptop (§59.25) sharing duties with everything
else. Requirements: steady frame rate with a frame-time metric exported to
§32; low input latency for gestures (resolver is local, §67.11); lazy model
loading; client-side asset caching; object culling; level-of-detail where
assets justify it; bounded particle effects; GPU when available; **frame
rate and scene complexity degrade gracefully** (quality tiers). Spatial
features must be **disable-able or defer-able** by configuration
(§67.19) - and the system must be fully usable when they are.

## 67.18 PRIVACY

Camera processing is local by default (§68.13): camera → local Python vision
→ gesture metadata → bus. **Raw frames never go to cloud AI by default.**
The renderer never sees raw frames either - only semantic events. A visible
vision indicator lives in the client chrome whenever the camera is active
(§68.13 states), with an off switch that is honoured immediately
(`VISION_OFF`).

## 67.19 NODE INTEGRATION

- **Windows node (§64.6)**: the Electron application *is* the spatial UI host
  and the owner of camera, local vision, screen, filesystem, browser,
  screenshots, UI automation. Camera and vision service run here; nothing
  Windows-specific moves to Ubuntu.
- **Ubuntu (§64.7)**: remains a pure execution node. It *feeds* spatial
  scenes (metrics, services, processes via existing events) but renders
  nothing - the primary spatial UI stays in Electron.
- **ESP32 (§64.9)**: remains the physical device node. A future spatial
  scene can *show* ESP32 topology (ULTRON → ESP32 → mic/speaker/OLED/
  sensors/battery); the ESP32 never renders 3D.
- **Mobile PWA (§64.8)**: consumes 2D/text of the same scene documents; a
  mobile 3D viewer is future work, not assumed.

## 67.20 SPATIAL MODES AND PERSISTENT WORKSPACES

Modes: `NORMAL`, `SPATIAL`, `PRESENTATION`, `MODEL_VIEWER`, `DATA_EXPLORER`,
`SYSTEM_VIEW`, `AGENT_VIEW`, `IMMERSIVE` - scene/viewport state in the
document (§67.4 `scene.mode`), switched by `spatial.set_mode` from voice,
text, or UI.

A **spatial workspace** is a saved scene bundle (e.g. "ULTRON Server
Workspace": server model, CPU/RAM panels, agents, tasks, logs, network
graph). Persistence reuses existing storage: scene documents live with
project/workspace artifacts (§46, `workspaces/`), session/selection state in
context (§67.12), durable preferences in the §22 memory store under the
existing `user`/`project:*` namespaces. **No new storage system** (§67.23).

## 67.21 FEATURE FLAGS AND CONFIGURATION

All default **off** in the current lightweight environment (§59.25):

`SPATIAL_UI_ENABLED` (master), `SPATIAL_RENDERER_AUTO`, `SPATIAL_ASSETS_*`,
`SPATIAL_GESTURES_ENABLED`, `SPATIAL_REDUCED_MOTION` (follows the OS,
overridable), `VISION_ENABLED` (§68.19, shared). Flags are settings (§34), honoured by the
client through the existing config/status endpoints - no new configuration
mechanism.

## 67.22 TESTING PHILOSOPHY

Unit tests never need hardware or a GPU:

- scene/object schema validation (valid, invalid, unknown type, malformed,
  oversized, executable-content attempts);
- interaction resolver as a pure rule table, parametrized over
  (gesture × context × object rules) including priority conflicts;
- action protocol validation and the permission/confirmation path (LEVEL
  matrix, 403/409 shapes, audit rows) with fake inputs;
- fallback: simulate WebGL absence, renderer crash, vision service down →
  assert 2D/text and mouse-only paths;
- reduced-motion and accessibility states;
- voice + gesture reference resolution from *simulated* context;
- performance boundaries as asserted limits (object count, frame budget
  counters), not FPS promises;
- gesture fixtures come from §68's simulated landmark data - **no camera in
  unit tests, ever** (same rule as §65.23's "no real calls").

## 67.23 DUPLICATE-SYSTEM CHECK

| Check | Result |
|---|---|
| Second event bus for gestures/spatial? | No - additive `GESTURE_*`/`SPATIAL_*` names on §19 (§66.8 rule) |
| Second component architecture beside a "Dynamic UI"? | No - **one** ComponentSpec registry, two renderer namespaces (§67.5) |
| Second AI/agent system for spatial? | No - response planner + context engine decide; renderer is dumb |
| Second memory system for selection/scene? | No - context engine session state + §22 scopes (§67.12) |
| Second WebSocket system? | No - existing API/SSE/device WS; the vision service's localhost link (§68.4) is a local transport, not a bus |
| Own computer vision inside USI? | No - USI consumes §68; camera sections of the USI request are merged into Phase V (V2-V4) |
| LLM in the gesture loop? | No - local resolver (§67.11) |
| Gesture → shell / filesystem? | No - §67.16 pipeline |
| Custom 3D engine or model parser? | No - established libraries only (§67.6/§67.7) |
| Duplicate storage for scenes? | No - §46 workspaces + §22 scopes (§67.20) |

## 67.24 ROADMAP - PHASES S1-S9

Specified in tasks.md as **T395-T451** (the S block, with perception's V
tasks T452-T491 following), appended after the §66 block (D012/D013/
D028 precedent), in nine phases: **S1** architecture (schema, registry,
engine skeleton, events, renderer abstraction) → **S2** 3D renderer
(integration gate, camera, objects, models, selection, animation) →
**S3** spatial components (panels, images, graphs/charts, flowcharts,
dashboards, timelines, agent graphs) → **S4** gesture input adapter
(consumes V2-V4; no duplicate vision work) → **S5** interaction (resolver,
selection, grab/rotate/scale, navigation, context rules) → **S6** multimodal
(voice+gesture, references, commands) → **S7** security (validation,
confirmation, camera privacy, IPC) → **S8** performance (metrics, caching,
culling/LOD, fallback renderer, flags) → **S9** advanced (persistent
workspaces, agent visualization, simulations, advanced viz, multi-user and
future-hardware roadmapping). Every task carries objective, dependencies,
implementation requirements, testing, and acceptance criteria in tasks.md.

## 67.25 DOCUMENTATION-ONLY DISCLAIMER

**This section is documentation.** Nothing in §67 is implemented by virtue of
being written here. No renderer, scene, gesture, or spatial component exists
until its tasks.md entry is `[x]` with a passing gate (§54).

---

# 68. ULTRON PERCEPTION LAYER - COMPUTER VISION (ADDENDUM)

ULTRON gains the ability to **perceive** - cameras, the screen, and future
sensors producing structured observations about the physical and visual
world. This is a general Perception Layer, not a camera feature and not only
a hand-gesture system: hand tracking is one capability module among many
(object detection, OCR, scene understanding, screen understanding are
others). It extends the existing system; it does not fork it.

## 68.0 STATUS AND NON-DESTRUCTIVE RULE

Additive only, attaching to what exists:

| Perception piece | Attaches to |
|---|---|
| Perception events | the one §19 bus, additive names (§66.8 rule) |
| Interpreted observations | context engine (§66.3, T380) → agents/tools |
| Gesture feed for spatial UI | §67.9 (USI consumes, never re-detects) |
| Screen capture inputs | existing screenshot/computer tools (T143, T262, T345) |
| Vision service status | §32 observability + existing status endpoints |
| Camera/screen hardware | Windows node (§64.6); Ubuntu optional (§64.7); ESP32 sensors (§64.9) |
| On-demand "look" style analysis | existing tool pipeline (§16) - see §68.10 |
| Sensor data (ESP32) | `DEVICE_*` events (§64.9, T160-T167) |

**No new AI core, no second agent system, no second memory system, no second
event bus, no second tool system, no second permission system, no duplicate
Dynamic/Spatial UI architecture.** The perception system **never executes
privileged actions** - that split is §68.12 and is the load-bearing wall.

**This section is documentation.** Nothing in §68 is implemented by virtue of
being written here (§54).

## 68.1 CORE CONCEPT - PERCEPTION OBSERVES, CONTEXT UNDERSTANDS

```text
                     ULTRON CORE
                          │
                   CONTEXT ENGINE (§66.3 / T380)
                          │
              ┌───────────┴───────────┐
              │                       │
         PERCEPTION                 COGNITION
              │                       │
      ┌───────┼────────┐              │
      │       │        │              │
   Vision   Voice    Sensors          │
      │       │        │              │
   Camera     STT     ESP32           │
      │                               │
      └───────────┬───────────────────┘
                  ▼  (structured events only)
              AGENTS → TOOLS → PERMISSION → NODES
```

Division of labour: the Perception Layer **observes**; the Context Engine
**interprets** in context; agents **reason**; tools **act**; nodes
**execute**; permissions **govern**; verification (§17) **confirms**. An
observation is never a command.

## 68.2 PERCEPTION SOURCES

One abstraction, many sources - each produces **normalized perception
events**, never raw streams, into the bus/context:

```text
PerceptionSource (uniform interface: start/stop/health/capabilities/events)
    ├── CameraSource        webcams on the Windows node
    ├── ScreenSource        Windows screen capture
    ├── VoiceSource         existing STT (§25/§59.17-19) - already built
    ├── ESP32SensorSource   DEVICE_* events (§64.9)
    └── Future: depth camera · extra cameras · IMU · mics · IoT · wearables
```

Voice is listed for completeness: STT is an existing perception channel
(§18 wake/STT/TTS events); this layer gives vision and sensors the same
standing. Future sources implement the same interface - **the abstraction
must support real 3D coordinates when depth hardware arrives** (§68.8).

## 68.3 CAMERA SERVICE

```text
Camera → Camera Service → Vision Processing → Perception Events → Context
```

Camera Service responsibilities: start, stop, restart, discovery, available/
unavailable, resolution and FPS configuration, health state, disconnect
detection, reconnect, processing status, privacy state. **The camera is
assumed absent until proven present**: the entire system must behave
normally with no camera attached, and a camera disappearing mid-session is a
state transition (`VISION_STATUS_CHANGED`), not a crash (§68.16).

## 68.4 PYTHON VISION SERVICE

Camera processing runs in a **local Python service** on the Windows node:

```text
Electron/Windows node → spawns or connects → Python Vision Service →
Camera → vision models → normalized perception events → bus/Electron
```

- **Local by default, cloud never mandatory** (§68.13): raw frames stay on
  the machine.
- **Technology is a gated decision, not a pre-commitment** (V2.1): inspect
  the environment (8 GB laptop, §59.25) then choose the lightest practical
  stack - candidates are OpenCV, MediaPipe, ONNX Runtime, or equivalent
  hand-tracking/vision libraries. No large dependency is installed without
  that inspection (§54).
- **Transport**: a localhost WebSocket following the existing WebSocket
  conventions (T162 lineage), plus an HTTP health endpoint. This is a local
  process link - *not* a second bus (§68.22). Existing API/SSE/device WS
  architecture is untouched.
- **Independently testable**: the service has its own lifecycle (§68.16) and
  accepts simulated frames/landmarks so unit tests need no hardware (§68.21).

## 68.5 VISION CAPABILITY MODULES

Modular, individually enabled/disabled - never all models at once:

```text
Vision
    ├── Hand Tracking          (V3)
    ├── Gesture Recognition    (V3 → GESTURE_* events → §67)
    ├── Object Detection       (V4)
    ├── Object Tracking        (V4)
    ├── Scene Understanding    (V5)
    ├── Spatial Understanding  (V5)
    ├── OCR                    (V6)
    ├── Document Understanding (V6)
    ├── Image Understanding    (V6, shares §66.13 ingestion paths)
    ├── Screen Understanding   (V7)
    └── Visual Event Detection (V4/V5, throttled §68.11)
```

Each module reports presence through capability discovery (§68.15); absence
of a module degrades features that depend on it and nothing else.

## 68.6 HAND TRACKING AND GESTURE RECOGNITION

**Tracking** (raw, local, never the UI API): hand detection, finger/palm
landmarks, orientation, finger states, pinch distance, movement, velocity
where useful, confidence, left/right identification, multiple hands where
supported.

**Recognition** converts landmarks into **semantic gestures** - the stable
abstraction between vision and ULTRON:

```json
{ "event": "GESTURE_STARTED", "type": "pinch_start", "source": "camera",
  "hand": "right", "position": { "x": 0.62, "y": 0.41 },
  "confidence": 0.94, "timestamp": 1712345678901 }
```

Vocabulary: `point`, `pinch_start`, `pinch_end`, `grab`, `release`,
`swipe_left/right/up/down`, `open_palm`, `fist`, `two_finger`, `rotate`,
`zoom_in`, `zoom_out`, `hold`. Event family (§68.11): `GESTURE_STARTED`,
`GESTURE_UPDATED` (threshold-crossings only), `GESTURE_ENDED` - the payload
carries `type`, so the family stays small and spam stays impossible.

**Raw landmark streams are explicitly not the interaction API** - §67.9
consumes these semantic events and nothing else.

## 68.7 OBJECT DETECTION AND TRACKING

Detection produces normalized results:

```json
{ "event": "PERCEPTION_OBJECT_ENTERED", "object_id": "object_42",
  "class": "laptop", "confidence": 0.93,
  "bounding_box": { "x": 0.31, "y": 0.42, "width": 0.30, "height": 0.22 },
  "timestamp": 1712345678901 }
```

- Object ids are **stable for the session** while tracked; the system never
  claims persistent real-world identity it does not have (honesty rule).
- Tracking emits *meaningful* transitions only - `ENTERED`, `MOVED`
  (threshold-crossed), `LEFT`, `SELECTED` - never per-frame spam
  (§68.11).
- Detection feeds context ("what am I looking at?"), scene understanding
  (§68.8), and spatial selection (§67.10) - it never feeds the tool
  pipeline directly (§68.12).

## 68.8 SCENE UNDERSTANDING AND SPATIAL AWARENESS

A higher-level scene representation assembled from the modules:

```json
{ "scene": { "environment": "desk", "confidence": 0.87 },
  "objects": ["laptop", "phone", "keyboard", "mouse"],
  "hands": ["right_hand"],
  "activity": "user_interacting_with_laptop" }
```

Relative spatial relationships without depth hardware: `left_of`,
`right_of`, `in_front_of`, `behind`, `near`, `above`, `below` - each an
observation with confidence:

```json
{ "relation": "left_of", "subject": "laptop", "object": "phone",
  "confidence": 0.88 }
```

The abstraction carries optional 3D coordinates so depth cameras (future)
upgrade fidelity without schema breakage. Scene understanding is *derived*
from modules, stored as session context only (§68.13 privacy), and
summarized into events (`PERCEPTION_SCENE_CHANGED`) - not streamed.

## 68.9 OCR AND DOCUMENT UNDERSTANDING

Pipeline: camera → text/document detection → OCR → extracted text → context.
Inputs: books, notes, screens, documents, labels, diagrams, signs,
handwriting where supported. **OCR activates when relevant** (on-demand mode
or a document detected with sufficient confidence) - detected words are not
auto-fed to the LLM as a stream (§68.11). "What does this say?" / "Explain
this diagram." / "Solve this." resolve through existing multimodal/document
paths (§66.13 ingestion, file tools) - **no duplicate document-processing
architecture**. Image understanding (files, uploads, screenshots) normalizes
into the same context.

## 68.10 SCREEN UNDERSTANDING

A perception source for the Windows screen:

```text
Windows screen → capture → vision → UI/object detection → screen context →
context engine → ULTRON reasoning
```

Potential results: visible applications, windows, dialogs, buttons, text,
icons, errors, selected elements. This **complements** the existing Windows
automation tools (T143/T262/T345, §59.15) and the §66.10 verification loop -
it does not replace them:

```text
Vision perceives:      "VS Code, Chrome, a Terminal, an error dialog"
User (voice):          "Open the terminal."
Intent → tool request → permission (§15) → Windows node (§64.6) → execute
```

**Never vision → action.** On-demand "what's on my screen?" analysis is a
*context request* handled by the same perception path in ON_DEMAND mode -
the screenshot *tools* remain the action-side primitives (§66.10 amendment).

## 68.11 EVENT MODEL, THROTTLING AND CONFIDENCE

Event families, additive names on §19 per §66.8 (one event, one spelling):

```text
VISION_STATUS_CHANGED      (state: OFFLINE|STARTING|READY|ERROR, ...)
VISION_CAPABILITIES_UPDATED
GESTURE_STARTED / GESTURE_UPDATED / GESTURE_ENDED
PERCEPTION_OBJECT_ENTERED / OBJECT_MOVED / OBJECT_LEFT / OBJECT_SELECTED
PERCEPTION_SCENE_CHANGED
PERCEPTION_DOCUMENT_DETECTED / PERCEPTION_TEXT_DETECTED
PERCEPTION_PERSON_DETECTED
```

Rules:

- **Meaningful events only.** 30 FPS in, event-rate out. Never raw frames
  to the LLM, never `object.moved` per frame: each class has a threshold +
  minimum interval + debounce; movement events fire on meaningful crossings;
  gestures emit start/update/end, with updates suppressed unless the gesture
  itself changed.
- **Confidence everywhere**: 0-1 on every observation, with bands -
  high ≥ 0.85, medium 0.5-0.85, low < 0.5. Low-confidence observations are
  tagged and **never used to resolve references or trigger anything
  sensitive** without confirmation; reference resolution prefers the highest
  confidence candidate and says so.
- Events fan out over the existing SSE stream (T023/T320) exactly as §19
  events do; the spatial client consumes them (§67.9), context ingests them
  (§68.14), logs and metrics observe them (§32).

## 68.12 SECURITY - PERCEPTION NEVER ACTS

Binding pipeline; the same for every source (camera, screen, sensor):

```text
camera/screen/sensors → perception → structured events → context engine →
ULTRON reasoning → tool request (§16) → permission (§15/§64.12) →
node (§64) → execution → verification (§17)
```

Incorrect architecture, forbidden: camera → vision → shell command. A
detected object, a gesture, or screen content is **never** a privileged
command; it is context. Confirmation rules (LEVEL 4-5, irreversible) apply
unchanged to anything a perception ultimately inspires (§67.16 is the
spatial instance of this rule). Perception modules have **no tool access at
all** - they are sensors, not agents.

## 68.13 PRIVACY AND VISION MODES

Default: **camera → local processing → structured events.** Raw frames are
not uploaded to cloud AI by default, not stored continuously, and not
retained beyond the working frame buffer. Explicit modes:

| Mode | Behaviour |
|---|---|
| `VISION_OFF` | no camera processing at all (default) |
| `GESTURE_ONLY` | hand/gesture recognition only |
| `OBJECT_MODE` | detection/tracking active |
| `ON_DEMAND_VISION` | camera analysed only when requested |
| `CONTINUOUS_VISION` | optional continuous perception (opt-in) |
| `SCREEN_VISION` | Windows screen understanding |
| `SPATIAL_MODE` | vision feeding spatial interaction (§67) |

The client shows a **visible, unmissable indicator** whenever vision is
active, and a control that turns it off immediately. Cloud vision (§68.15)
is a separate, explicit opt-in per §68.13's rule: local first, always.

## 68.14 MULTIMODAL FUSION - CONTEXT ENGINE

Perception joins the existing context assembly - no new store:

```text
VOICE + TEXT + VISION + GESTURE + SPATIAL STATE + MEMORY + TASK STATE
        → CONTEXT ENGINE (§66.3 / T380) → ULTRON REASONING
```

Worked examples: "What's that?" with vision showing `selected_object =
laptop` in scene `desk` → answer names the laptop. "Show me that." with
POINT at `object_42` → spatial focus on the laptop (§67.12). "Zoom into
that." + selected object → resolved reference. **Reference resolution is a
context problem**: gesture context + spatial selection + conversation
context, all through T380's `resolve` - not a memory system of its own.
Perception-derived session state (current scene, selected object, recent
visual events, detected document, screen state) is typed context, persisted
only when appropriate - **never raw video** (§29 of the request maps to §22's
existing rules; §65.17's transcript discipline is the precedent).

Voice commands the fusion makes natural: "What am I looking at?", "What is
this?", "Explain that.", "Read this.", "What does this error mean?", "What
changed?", "Show this in 3D."

## 68.15 VISION PROVIDER ABSTRACTION AND CAPABILITY DISCOVERY

Mirrors the AI provider adapters (§59.7-§59.9 discipline):

```text
VisionProvider
    ├── initialize() → config, model paths
    ├── capabilities() → enabled modules + model presence
    ├── process(input) → normalized perception events
    ├── health() → fps, latency, errors
    └── shutdown()
```

Implementations: `LocalVision` (default), `CloudVision` (future, optional),
future providers. **ULTRON core is never coupled to a specific vision
library.** Capability discovery publishes a status document the client and
planner can read:

```json
{ "vision": true, "hand_tracking": true, "gesture_recognition": true,
  "object_detection": false, "ocr": false, "screen_vision": false,
  "depth": false, "camera": "available", "mode": "GESTURE_ONLY" }
```

Consumers adapt: no hand tracking → gesture UI disabled; no camera → mouse;
no depth → approximate 2D relationships; no OCR → document commands answer
with a clear capability error, not a hang.

## 68.16 SERVICE LIFECYCLE, STATUS AND OBSERVABILITY

Service lifecycle: `START · STOP · RESTART · HEALTH · VERSION ·
CAPABILITIES`. Client-visible states (exposed via status endpoint +
`VISION_STATUS_CHANGED`): `VISION_OFFLINE`, `VISION_STARTING`,
`VISION_READY`, `CAMERA_AVAILABLE`, `CAMERA_UNAVAILABLE`,
`TRACKING_ACTIVE`, `TRACKING_LOST`, `PROCESSING`, `ERROR` - integrated with
existing system status/events (§32, §66.15 self-diagnostics can consume
them). Metrics added to §32: camera FPS, processing FPS, inference latency,
gesture latency, dropped frames, tracking confidence, detection counts,
service CPU/memory, model load time. Electron can always answer: is vision
available, is the camera available, which capabilities are enabled, what
mode are we in.

## 68.17 NODE ARCHITECTURE

The node owns the physical capability; core orchestrates (§64):

- **Windows node (§64.6)**: webcam, screen, Windows UI, local vision
  service, gesture input - the primary perception host.
- **Ubuntu (§64.7)**: optional camera/server vision, Linux screen, server
  monitoring - its metrics already feed scenes (§67.19).
- **ESP32 (§64.9)**: sensors, microphone, device state (existing
  `DEVICE_*`), future camera if hardware supports it - as perception
  *sources*, never as renderers.
- **Mobile (§64.8)**: UI only; optional camera input later if explicitly
  supported.

A future depth camera, IMU, or wearable joins as another
`PerceptionSource` on whichever node physically has it.

## 68.18 PERFORMANCE

The development environment is resource constrained (§59.25): frame skipping,
resolution control, model selection lightest-first, lazy model loading,
event throttling (§68.11), asynchronous processing, CPU-friendly defaults,
optional GPU acceleration, configurable FPS with conservative defaults. The
system must be fully usable with every advanced capability disabled - vision
is an enhancement layer, never a dependency (§68.20).

## 68.19 FEATURE FLAGS

All default **off** in the current lightweight environment; each gates its
module independently: `VISION_ENABLED` (master), `CAMERA_ENABLED`,
`HAND_TRACKING_ENABLED`, `GESTURE_ENABLED`, `OBJECT_DETECTION_ENABLED`,
`OBJECT_TRACKING_ENABLED`, `OCR_ENABLED`, `SCREEN_VISION_ENABLED`,
`SPATIAL_VISION_ENABLED`, `CONTINUOUS_VISION_ENABLED`, `CLOUD_VISION_ENABLED`.
Flags live in §34 settings and are reported through capability discovery
(§68.15) - no parallel configuration system.

## 68.20 FALLBACK - PERCEPTION IS NEVER A SINGLE POINT OF FAILURE

```text
camera vision → mouse/touch → keyboard → voice → text
spatial UI fails → dynamic 2D UI → normal chat
vision service dies → camera states go OFFLINE → all non-gesture input works
```

ULTRON must remain fully usable without vision, without a camera, and
without the spatial renderer - in that layered order. A camera disconnect
mid-session is an ordinary state transition (§68.3/§68.16); the service
restarting must not disturb chat, tasks, tools, or voice.

## 68.21 TESTING - SIMULATED PERCEPTION, NO CAMERA HARDWARE

Fixtures of simulated landmarks, detections, scenes, and frames live in the
test tree; unit tests never open a camera (same spirit as §65.23 "no real
calls, ever"). Coverage targets: landmark → gesture recognition (pure
function), gesture event schema + confidence thresholds, throttling
properties (no per-frame spam under synthetic load), object tracking id
stability, scene construction and relationships, OCR result normalization,
context fusion and voice+vision reference resolution, gesture → spatial
action through §67.10, permission enforcement on anything downstream,
camera disconnect / service restart / tracking loss state machines, and
fallback behaviour with vision flags all off.

## 68.22 DUPLICATE-SYSTEM CHECK

| Check | Result |
|---|---|
| Second event bus? | No - additive `VISION_*`/`GESTURE_*`/`PERCEPTION_*` names on §19 (§66.8) |
| Second memory system for perception state? | No - context engine session state + §22 scopes (§68.14) |
| Second tool system / perception-tools? | No - perception has no tools; on-demand analysis rides §16/§66.10 |
| Second AI router for vision models? | No - VisionProvider is a *sensor* adapter (§68.15), the model router stays for LLMs |
| Duplicate of §66.10 computer use? | No - §66.10 acts, §68 observes (amendment recorded there) |
| Duplicate screenshot capabilities? | No - ScreenSource reuses T143/T262/T345 captures as *inputs* |
| Own vision inside the spatial UI? | No - §67 consumes §68 events |
| Cloud vision required? | No - local default, cloud optional behind `CLOUD_VISION_ENABLED` |
| Raw frames to LLM/cloud by default? | No - §68.13 |
| Camera → OS bypass? | No - §68.12 |

## 68.23 ROADMAP - PHASES V1-V10

Specified in tasks.md as **T452-T491**, one contiguous block immediately
after §67's S tasks (T395-T451), appended after the §66 block: **V1** perception architecture
(source abstraction, event schema, VisionProvider, capability discovery) →
**V2** camera (service, lifecycle, health, privacy, configuration) →
**V3** hand vision (tracking, landmarks, gesture recognition, semantic
events) → **V4** object vision (detection, tracking, stable ids, visual
events) → **V5** scene (representation, relationships, change events) →
**V6** OCR (text detection, OCR, document understanding) → **V7** screen
vision (capture reuse, UI detection, screen context) → **V8** multimodal
(voice+vision, gesture+voice, spatial references, context fusion) →
**V9** spatial wiring (vision → §67 interaction, dashboards, mode switching)
→ **V10** advanced (depth, multi-hand/multi-object, cloud vision adapter,
multiple cameras, future sensors). Every task carries objective,
dependencies, implementation requirements, testing, and acceptance criteria
in tasks.md. USI phases S1-S9 and V1-V10 interleave: S4 consumes V2-V4;
S5/S6/V8/V9 are one interaction stack seen from two sides - the task block
records the merge so nothing is built twice.

## 68.24 DOCUMENTATION-ONLY DISCLAIMER

**This section is documentation.** Nothing in §68 is implemented by virtue of
being written here. No camera service, vision module, gesture, or perception
event exists until its tasks.md entry is `[x]` with a passing gate (§54):
"ULTRON can SEE" is a roadmap line, not a status line.
