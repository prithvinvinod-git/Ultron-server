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