
# ULTRON Error Catalogue

Every error ULTRON raises deliberately is a subclass of `UltronError`
(`server/app/core/errors.py`). This document is the human-readable catalogue;
the code, the `http_status`, and the `retryable` verdict are the machine-readable
contract.

## What is guaranteed

Four properties hold for every error in the hierarchy.

**A stable code.** Every error carries an `ErrorCode`, a `StrEnum` whose values
are the strings clients actually see. Renaming a value is a breaking change; the
wording of a message is not. Clients match on the code, so it must not drift.

**A retry verdict.** `retryable` says whether another attempt could plausibly
succeed without intervention. `is_retryable(error)` resolves it, treating a bare
`TimeoutError` or `ConnectionError` from a driver as the untyped equivalent, and
treating `asyncio.CancelledError` as never retryable because it is a deliberate
stop rather than a fault.

**An HTTP status.** `http_status` maps to the response code an API route should
use. A route may override it, but it should not have to.

**A secret-free payload.** `to_dict()` returns `code`, `message`, `retryable`, and
optionally `details` and `cause`. A cause contributes its *type* only, never its
text, because a driver exception can embed a connection string with a password in
it. `details` is written to logs and returned to clients, so it must never carry a
secret either.

## How retryability is decided

Retryability is expressed twice, and the two must agree:

- `UltronError.retryable`, read by `is_retryable()` at the point of failure.
- `RETRYABLE_CODES`, read by the model router when building its fallback chain.

An error that is retryable by one and absent from the other would be retried in
one path and treated as permanent in the other. A unit test enforces agreement in
both directions across every concrete error class.

The one deliberate asymmetry: `MigrationRequiredError` inherits from
`DatabaseError` but is **not** retryable. A pending migration must be applied, not
waited out, so retrying forever would convert a five-second fix into a hang.

## Class table

Derived from the implementation. `Retryable` marks codes also present in
`RETRYABLE_CODES`.

| Class | Code | HTTP | Retryable | Base |
| --- | --- | --- | --- | --- |
| `UltronError` | `INTERNAL_ERROR` | 500 | no | `Exception` |
| `RetryableError` | `INTERNAL_ERROR` | 500 | yes | `UltronError` |
| `PermanentError` | `INTERNAL_ERROR` | 500 | no | `UltronError` |
| `InvalidInputError` | `INVALID_INPUT` | 422 | no | `PermanentError` |
| `NotFoundError` | `NOT_FOUND` | 404 | no | `PermanentError` |
| `TaskNotFoundError` | `TASK_NOT_FOUND` | 404 | no | `NotFoundError` |
| `AgentNotFoundError` | `AGENT_NOT_FOUND` | 404 | no | `NotFoundError` |
| `ConflictError` | `CONFLICT` | 409 | no | `PermanentError` |
| `OperationTimeoutError` | `TIMEOUT` | 504 | yes | `RetryableError` |
| `OperationCancelledError` | `CANCELLED` | 499 | no | `UltronError` |
| `RateLimitedError` | `RATE_LIMITED` | 429 | yes | `RetryableError` |
| `DependencyUnavailableError` | `DEPENDENCY_UNAVAILABLE` | 503 | yes | `RetryableError` |
| `CapabilityNotImplementedError` | `NOT_IMPLEMENTED` | 501 | no | `UltronError` |
| `ShuttingDownError` | `SHUTTING_DOWN` | 503 | no | `UltronError` |
| `SerializationError` | `SERIALIZATION_ERROR` | 500 | no | `PermanentError` |
| `ConfigError` | `CONFIG_INVALID` | 500 | no | `PermanentError` |
| `ConfigMissingError` | `CONFIG_MISSING` | 500 | no | `ConfigError` |
| `AuthError` | `UNAUTHENTICATED` | 401 | no | `PermanentError` |
| `InvalidCredentialsError` | `INVALID_CREDENTIALS` | 401 | no | `AuthError` |
| `PermissionDeniedError` | `PERMISSION_DENIED` | 403 | no | `PermanentError` |
| `ConfirmationRequiredError` | `CONFIRMATION_REQUIRED` | 409 | no | `PermissionDeniedError` |
| `SsrfBlockedError` | `SSRF_BLOCKED` | 403 | no | `PermanentError` |
| `DatabaseError` | `DATABASE_ERROR` | 503 | yes | `RetryableError` |
| `MigrationRequiredError` | `MIGRATION_REQUIRED` | 503 | no | `DatabaseError` |
| `RedisError` | `REDIS_ERROR` | 503 | yes | `RetryableError` |
| `LockUnavailableError` | `CONFLICT` | 409 | no | `PermanentError` |
| `ModelError` | `PROVIDER_ERROR` | 502 | yes | `RetryableError` |
| `LocalModelUnavailableError` | `LOCAL_MODEL_UNAVAILABLE` | 503 | yes | `ModelError` |
| `ProviderNotConfiguredError` | `PROVIDER_NOT_CONFIGURED` | 503 | no | `ModelError` |
| `ModelTimeoutError` | `MODEL_TIMEOUT` | 504 | yes | `ModelError` |
| `ModelRateLimitedError` | `MODEL_RATE_LIMITED` | 429 | yes | `ModelError` |
| `ModelResponseInvalidError` | `MODEL_RESPONSE_INVALID` | 502 | no | `ModelError` |
| `ModelNotSupportedError` | `MODEL_UNSUPPORTED` | 400 | no | `ModelError` |
| `ToolError` | `TOOL_EXECUTION_FAILED` | 500 | no | `UltronError` |
| `ToolNotFoundError` | `TOOL_NOT_FOUND` | 404 | no | `ToolError` |
| `ToolSchemaInvalidError` | `TOOL_SCHEMA_INVALID` | 422 | no | `ToolError` |
| `ToolVerificationError` | `TOOL_VERIFICATION_FAILED` | 500 | no | `ToolError` |
| `ToolPermissionDeniedError` | `PERMISSION_DENIED` | 403 | no | `ToolError` |
| `CommandNotAllowedError` | `COMMAND_NOT_ALLOWED` | 403 | no | `ToolError` |
| `TaskError` | `TASK_FAILED` | 500 | no | `UltronError` |
| `AgentError` | `AGENT_FAILED` | 500 | no | `UltronError` |
| `WorkspaceError` | `WORKSPACE_ERROR` | 500 | no | `UltronError` |
| `EventBusError` | `EVENT_BUS_ERROR` | 503 | yes | `RetryableError` |
| `HealthCheckFailedError` | `HEALTH_CHECK_FAILED` | 503 | yes | `RetryableError` |
| `VoiceError` | `VOICE_ERROR` | 503 | no | `UltronError` |
| `DeviceError` | `DEVICE_ERROR` | 503 | no | `UltronError` |
| `BrowserError` | `BROWSER_ERROR` | 503 | no | `UltronError` |

## Codes by area

- **Generic** — `INTERNAL_ERROR`, `INVALID_INPUT`, `NOT_FOUND`, `CONFLICT`,
  `TIMEOUT`, `CANCELLED`, `RATE_LIMITED`, `DEPENDENCY_UNAVAILABLE`,
  `SHUTTING_DOWN`, `NOT_IMPLEMENTED`.
- **Configuration** — `CONFIG_INVALID`, `CONFIG_MISSING`.
- **Security** — `UNAUTHENTICATED`, `PERMISSION_DENIED`, `CONFIRMATION_REQUIRED`,
  `INVALID_CREDENTIALS`, `SSRF_BLOCKED`.
- **Persistence** — `DATABASE_ERROR`, `MIGRATION_REQUIRED`, `REDIS_ERROR`.
- **Models** — `LOCAL_MODEL_UNAVAILABLE`, `PROVIDER_NOT_CONFIGURED`,
  `PROVIDER_ERROR`, `MODEL_TIMEOUT`, `MODEL_RATE_LIMITED`,
  `MODEL_RESPONSE_INVALID`, `MODEL_UNSUPPORTED`.
- **Tools and agents** — `TOOL_NOT_FOUND`, `TOOL_EXECUTION_FAILED`,
  `TOOL_SCHEMA_INVALID`, `TOOL_VERIFICATION_FAILED`, `COMMAND_NOT_ALLOWED`,
  `TASK_NOT_FOUND`, `TASK_FAILED`, `AGENT_NOT_FOUND`, `AGENT_FAILED`,
  `WORKSPACE_ERROR`.
- **Subsystems** — `EVENT_BUS_ERROR`, `HEALTH_CHECK_FAILED`, `VOICE_ERROR`,
  `DEVICE_ERROR`, `BROWSER_ERROR`, `SERIALIZATION_ERROR`.

## Notable individual errors

**`LocalModelUnavailableError`** is the one code the specification names
explicitly. When Ollama is unreachable ULTRON reports this and keeps running: a
missing local model is a degraded capability, not a reason to take the process
down. It is retryable, so the router can fall through to a cloud provider.

**`ProviderNotConfiguredError`** is the opposite case and is deliberately *not*
retryable. Retrying a call with no credential cannot succeed; the caller must fall
back or ask the operator to configure a key.

**`InvalidCredentialsError`** carries a fixed message. Distinguishing "no such
user" from "wrong password" would turn the login path into a probe for valid
usernames.

**`SsrfBlockedError`** records the blocked host. A request to a link-local address
is refused before the connection is made, and the operator needs to know which host
was rejected to explain why.

**`ConfirmationRequiredError`** is the second stage of a denied action, distinct
from a hard refusal: the operation is permitted but needs explicit approval. It
uses 409 rather than 403 so a client can tell "ask again" from "no".

**`LockUnavailableError`** shares `CONFLICT` with `ConflictError` rather than
taking a new code, because to a client the two are the same answer — "someone else
got there first" — and a new code means every consumer of the error enumeration has
to learn about it. It stays a distinct type so a caller waiting on a lock can catch
exactly this, and it is deliberately **not** retryable: a retry here means another
worker still holds the lock, so a blanket "retry on failure" loop turns many waiters
into a thundering herd against the resource they are waiting for. The retryable
counterpart is `RedisError`, which is a fault in the dependency rather than
contention.

## Adding an error

1. Pick an existing `ErrorCode` if one fits. Adding a code is a public contract
   change and needs a reason that survives review.
2. Subclass the base that matches the retry verdict: `RetryableError` for a cause
   that may clear, `PermanentError` for one that needs intervention, or
   `UltronError` when the verdict is neither.
3. Set `http_status` if the default does not apply.
4. Derive the message from the domain value, so the message says what failed. Do
   not accept a free-form message where a resource name, provider, or tool is
   available; those are what make the message actionable.
5. If the class is retryable, add its code to `RETRYABLE_CODES`. The agreement test
   will fail if you forget.
6. Add a case to the tests. The retryable-agreement and code-coverage tests run
   across the whole hierarchy, so a new class is checked automatically.

## Degradation

`degrade(name, awaitable, fallback)` is the sanctioned way to absorb a failure
whose loss is acceptable, such as a cache read. It returns the fallback on a
`UltronError` and **re-raises** a programming error, so a `TypeError` can never be
disguised as a missing service. The loss is logged as a warning rather than
discarded, because section 33 of the specification forbids silently swallowing an
exception. Pass `on_error=` to narrow which error types are absorbed.

`exit_code_for(error)` maps an error to a process exit code following `sysexits.h`,
so a supervisor can distinguish a configuration failure (78) from a missing
capability (69) or a temporary shutdown (75) from a plain failure (1).
