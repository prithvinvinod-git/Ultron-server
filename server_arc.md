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

## 59.16 VOICE — LIVESKIT TRANSPORT

Extends §25. **New: LiveKit as the real-time transport.**

LiveKit is **self-hosted**. It is the transport and orchestration layer for voice
sessions, not a cloud dependency and not the STT or TTS engine.

```text
Microphone
   |
LiveKit            self-hosted transport, room/session orchestration
   |
Voice Agent
   |
STT                local, §59.17
   |
LLM / Agent        §20, §59.8
   |
TTS                local, §59.18
   |
LiveKit
   |
Speaker
```

Required: real-time microphone input, interruption/barge-in (§59.19), streaming
STT, streaming responses where the provider allows it, streaming TTS, low
latency, and support for multiple voice agents over the same deployment.

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

## 59.25 RESOURCE MANAGEMENT — 8 GB RAM

Reaffirms and extends §48. The development and server target is an **8 GB RAM
Intel i5-1235U**. This is a design constraint, not a deployment detail.

Do not assume: a dedicated GPU, large local models, several large models at
once, Kubernetes-style infrastructure, many containers, a huge database, or many
background services.

Do prefer: lightweight services, asynchronous workers, model unloading, **one
heavy local model at a time**, CPU-friendly inference, caching, queues, lazy
loading.

**Load-shedding order.** When memory is short, shed in this order, and say so:

```text
1. unload the local model                (biggest single win, §59.8)
2. drop LiveKit                          (voice goes offline-first, §59.20)
3. stop background agents and schedules  (§44)
4. reduce concurrent agent fan-out       (§7, §41)
5. report degraded capability to the user
```

Shedding must be observable (§59.24) and must never silently reduce the quality
of a security check or skip a permission evaluation. **Correctness is never
shedding material** -- §59.25 pressure must not become §15 pressure.

## 59.26 IMPLEMENTATION STRATEGY

Maps onto §51's phases rather than replacing them. Phases A and B are already
done: §51 Phase 0 (T001-T009) and most of Phase 1.

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