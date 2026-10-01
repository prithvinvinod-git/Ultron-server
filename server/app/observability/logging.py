"""Structured logging (spec section 32).

Every important operation in ULTRON must be observable, so logs are structured
records rather than prose. A record carries the fields the spec names —
``timestamp``, ``request_id``, ``task_id``, ``agent_id``, ``tool``, ``event``,
``status``, ``duration``, ``error`` — and any extra fields the caller supplies.

Correlation
-----------
``request_id``, ``task_id`` and ``agent_id`` live in :mod:`contextvars` rather
than being passed down every call. A tool executor three layers deep can log its
correlation ids without knowing which request caused it, and an asyncio task
gets its own context automatically, so concurrent work never mixes up ids.

That isolation is the reason contextvars are used and not a plain dict: two
requests handled at the same time must not overwrite each other's ids.

Redaction
---------
Secrets are removed before a record is serialised, not merely omitted by
convention. Redaction is recursive, so a credential nested inside a request body
is removed just as a top-level one is. The key list comes from
``LoggingSettings.redact_keys``, which means an operator can extend it without a
code change.

A redacted value is replaced by a fixed marker. The original length is *not*
preserved, because leaking the length of a secret is itself a small disclosure.

Redaction has two layers, because they cover different mistakes. Key-based
matching catches a credential in a structured field, which is the reliable case.
:func:`scrub_text` catches a credential interpolated into the message itself,
which no key can describe; it is deliberately narrow and best-effort. Log
structured fields rather than interpolated values and the second layer is never
needed.
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
import time
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from app.config import LoggingSettings, get_settings

#: Written in place of a redacted value.
REDACTED: Final[str] = "***REDACTED***"

#: Fields the spec requires every record to carry. Anything not set is emitted
#: as null rather than omitted, so a log consumer can rely on the shape.
STANDARD_FIELDS: Final[tuple[str, ...]] = (
    "timestamp",
    "level",
    "logger",
    "message",
    "request_id",
    "task_id",
    "agent_id",
    "tool",
    "event",
    "status",
    "duration_ms",
    "error",
)

_LOGGER_NAME: Final[str] = "ultron"

# Correlation identifiers. Each is independent so a task can run without a
# request and an agent can run without a task.
_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "ultron_request_id", default=None
)
_task_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "ultron_task_id", default=None
)
_agent_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "ultron_agent_id", default=None
)


# ---------------------------------------------------------------------------
# Correlation context
# ---------------------------------------------------------------------------


def new_id() -> str:
    """Return a fresh correlation identifier."""
    return uuid.uuid4().hex


def get_request_id() -> str | None:
    """Return the current request id, or None outside a request."""
    return _request_id.get()


def get_task_id() -> str | None:
    """Return the current task id, or None outside a task."""
    return _task_id.get()


def get_agent_id() -> str | None:
    """Return the current agent id, or None outside an agent."""
    return _agent_id.get()


def _context_value(field: str) -> str | None:
    """Return the ambient value of a correlation field, if it is bound."""
    variables = {
        "request_id": _request_id,
        "task_id": _task_id,
        "agent_id": _agent_id,
    }
    return variables[field].get()


def current_context() -> dict[str, str]:
    """Return every correlation id currently bound, omitting the unset ones."""
    bound = {
        "request_id": _request_id.get(),
        "task_id": _task_id.get(),
        "agent_id": _agent_id.get(),
    }
    return {key: value for key, value in bound.items() if value is not None}


@contextmanager
def request_context(request_id: str | None = None) -> Iterator[str]:
    """Bind a request id for the duration of the block.

    The previous value is restored on exit, so a nested request keeps its
    parent's id rather than losing it.
    """
    value = request_id or new_id()
    token = _request_id.set(value)
    try:
        yield value
    finally:
        _request_id.reset(token)


@contextmanager
def task_context(task_id: str | None = None) -> Iterator[str]:
    """Bind a task id for the duration of the block."""
    value = task_id or new_id()
    token = _task_id.set(value)
    try:
        yield value
    finally:
        _task_id.reset(token)


@contextmanager
def agent_context(agent_id: str | None = None) -> Iterator[str]:
    """Bind an agent id for the duration of the block."""
    value = agent_id or new_id()
    token = _agent_id.set(value)
    try:
        yield value
    finally:
        _agent_id.reset(token)


@contextmanager
def correlation(
    request_id: str | None = None,
    task_id: str | None = None,
    agent_id: str | None = None,
) -> Iterator[dict[str, str]]:
    """Bind several ids at once, for example at the top of a tool execution."""
    tokens: list[tuple[contextvars.ContextVar[str | None], contextvars.Token[str | None]]] = []
    try:
        for variable, value in (
            (_request_id, request_id),
            (_task_id, task_id),
            (_agent_id, agent_id),
        ):
            if value is not None:
                tokens.append((variable, variable.set(value)))
        yield current_context()
    finally:
        for variable, token in reversed(tokens):
            variable.reset(token)


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


def redact(value: Any, keys: tuple[str, ...]) -> Any:
    """Return ``value`` with every sensitive field replaced.

    Mappings, sequences and scalars are all handled, and the walk is recursive
    so a secret inside a nested request body is removed too. A mapping whose
    *key* matches is redacted wholesale, whatever its value type.
    """
    if isinstance(value, Mapping):
        return {
            key: REDACTED if _is_sensitive(str(key), keys) else redact(item, keys)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        rebuilt = [redact(item, keys) for item in value]
        return type(value)(rebuilt) if isinstance(value, tuple) else rebuilt
    return value


def _is_sensitive(key: str, keys: tuple[str, ...]) -> bool:
    """Return True when a field name looks like a credential.

    Both the key and the configured names are reduced to bare lowercase
    alphanumerics before comparison, so separators and casing are irrelevant:
    ``OPENAI_API_KEY``, ``openaiApiKey`` and ``api-key`` all match ``api_key``.
    Matching is on substrings so ``x-api-key`` is caught without being listed.
    """
    normalized = _normalize_key(key)
    return any(_normalize_key(candidate) in normalized for candidate in keys)


def _normalize_key(key: str) -> str:
    """Strip separators and casing from a field name for comparison."""
    return "".join(character for character in key.lower() if character.isalnum())


#: Secret shapes that can appear in free text. Key-based redaction above cannot
#: see a credential interpolated into a message, so these patterns are a
#: best-effort second line of defence. They are deliberately narrow: an
#: over-broad pattern would mangle ordinary log output and train operators to
#: ignore the redaction markers.
#:
#: Order is significant and runs most specific first. A connection URL or a
#: bearer token must be consumed whole, because the general ``key=value`` rule
#: below would otherwise stop at the first space and leave the secret's tail
#: behind -- exactly the case it looked like it had handled.
_TEXT_PATTERNS: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    # Credentials embedded in a connection URL. A driver exception routinely
    # carries the DSN it failed to connect with, so this is a common leak.
    (re.compile(r"(?<=://)[^\s:/@]+:[^\s:/@]+(?=@)"), REDACTED),
    # A bearer token, consumed with its scheme word.
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{8,}"), REDACTED),
    # A JSON Web Token.
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"), REDACTED),
    # Common provider key prefixes.
    (re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_\-]{16,}"), REDACTED),
    # ``key=value``, ``key: value`` and ``"key": "value"`` for a sensitive key.
    # The key and its separator are kept because neither is a secret and the key
    # is the useful part; only the value is replaced.
    (
        re.compile(
            r"(?i)\b([a-z0-9_\-]*(?:password|passwd|secret|token|api[_\-]?key|authorization)"
            r"[a-z0-9_\-]*)\b(\s*[:=]\s*)[\"']?([^\s\"',;}]+)[\"']?"
        ),
        r"\1\2" + REDACTED,
    ),
)


def scrub_text(text: str) -> str:
    """Replace credential-shaped substrings in free text.

    This is a safety net for secrets interpolated into a log message or carried
    in a traceback, neither of which key-based :func:`redact` can see because
    there is no field name involved. It is best-effort by nature: no regular
    expression can recognise every secret a caller might interpolate. Log
    structured fields rather than interpolated values wherever possible, and the
    problem disappears.
    """
    for pattern, replacement in _TEXT_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


class JsonFormatter(logging.Formatter):
    """Render a record as one JSON object per line.

    The exception is captured with its type and message. The traceback is
    included only when the level is an error, which keeps the common case cheap
    while still giving a developer what they need to debug a failure.
    """

    def __init__(self, redact_keys: tuple[str, ...], include_traceback: bool = True) -> None:
        super().__init__()
        self.redact_keys = redact_keys
        self.include_traceback = include_traceback

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": scrub_text(record.getMessage()),
        }
        # A field set explicitly via ``extra`` wins over the ambient context, so
        # a caller can attribute a record precisely.
        for field in ("request_id", "task_id", "agent_id"):
            payload[field] = getattr(record, field, None) or _context_value(field)
        for field in ("tool", "event", "status", "duration_ms", "error"):
            payload[field] = getattr(record, field, None)
        payload.update(extract_extras(record))

        safe = redact(payload, self.redact_keys)
        if self.include_traceback and record.exc_info:
            # Scrubbed separately because it is added after ``redact``, which
            # only walks fields. A driver exception very often carries the
            # connection string that failed, so this is a common leak path.
            safe["traceback"] = scrub_text(self.formatException(record.exc_info))
        return json.dumps(safe, default=str, ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    """Render a record for a human reading a terminal.

    Still one line per record and still correlated, just not JSON. Used when
    ``LOG_FORMAT=console``; production uses JSON.
    """

    default_format = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"

    def __init__(self, redact_keys: tuple[str, ...]) -> None:
        super().__init__(self.default_format, datefmt="%Y-%m-%dT%H:%M:%S%z")
        self.redact_keys = redact_keys

    def format(self, record: logging.LogRecord) -> str:
        """Render one line, with the correlation ids and extras appended.

        The record is rebuilt rather than mutated: the same record may reach
        another handler, and one handler must not alter what another sees.
        """
        clone = logging.makeLogRecord(record.__dict__)
        clone.msg = self._render_message(record)
        clone.args = ()
        return super().format(clone)

    def _render_message(self, record: logging.LogRecord) -> str:
        """Return the redacted message with correlation and extras appended."""
        message = scrub_text(record.getMessage())

        # A field set explicitly wins over the ambient context, matching the
        # JSON formatter so both agree on attribution.
        correlation = " ".join(
            f"{field}={value}"
            for field, value in (
                (field, getattr(record, field, None) or _context_value(field))
                for field in ("request_id", "task_id", "agent_id")
            )
            if value
        )
        extras = " ".join(
            f"{key}={redact(value, self.redact_keys)}"
            for key, value in sorted(extract_extras(record).items())
        )
        parts = [part for part in (correlation, extras) if part]
        if parts:
            return f"{message} [{' '.join(parts)}]"
        return message


#: Fields the logging machinery owns. Anything else set via ``extra`` is a
#: caller field and belongs in the record's extras.
_STANDARD_ATTRS = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
        "message",
        "asctime",
    }
)


def extract_extras(record: logging.LogRecord) -> dict[str, Any]:
    """Return the caller-supplied fields on a record.

    Anything not part of the standard ``LogRecord`` attributes is treated as an
    extra, which is how a tool executor attaches ``tool`` or ``duration_ms``
    without a dedicated API.
    """
    return {
        key: value
        for key, value in record.__dict__.items()
        if key not in _STANDARD_ATTRS and not key.startswith("_")
    }


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def configure_logging(settings: LoggingSettings | None = None) -> logging.Logger:
    """Configure the ``ultron`` logger and return it.

    Idempotent: calling it twice replaces the handlers rather than stacking
    them, so a reload does not duplicate every line. Third-party loggers are
    left alone except for propagation, so a library's own output stays visible
    but is not double-printed by our handler.
    """
    resolved = settings or get_settings().logging
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(resolved.level)
    logger.propagate = False

    for stale in list(logger.handlers):
        logger.removeHandler(stale)
        stale.close()

    console = (
        JsonFormatter(resolved.redact_keys)
        if resolved.format == "json"
        else ConsoleFormatter(resolved.redact_keys)
    )
    logger.addHandler(_stream_handler(sys.stdout, console))

    if resolved.file:
        # Appended to, and created if absent, so a restart does not lose the
        # previous run's logs. The stream is deliberately left open: the handler
        # owns it, and closes it when a later call to this function reconfigures.
        path = Path(resolved.file).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        logger.addHandler(
            _stream_handler(path.open("a", encoding="utf-8"), JsonFormatter(resolved.redact_keys))
        )

    return logger


def _stream_handler(stream: Any, formatter: logging.Formatter) -> logging.Handler:
    handler = logging.StreamHandler(stream)
    handler.setFormatter(formatter)
    return handler


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a logger under the ``ultron`` namespace.

    ``get_logger(__name__)`` keeps module-qualified names, which is what makes
    ``LOG_LEVEL=ultron.database=DEBUG`` work.
    """
    if not name or name == _LOGGER_NAME:
        return logging.getLogger(_LOGGER_NAME)
    if name.startswith(f"{_LOGGER_NAME}."):
        return logging.getLogger(name)
    return logging.getLogger(f"{_LOGGER_NAME}.{name}")


# ---------------------------------------------------------------------------
# Operation logging
# ---------------------------------------------------------------------------


class Timer:
    """Measure an operation and report its duration.

    Use as a context manager so the duration lands on the log record even when
    the operation raises, which is exactly when the number is interesting.
    """

    def __init__(self) -> None:
        self.started: float = 0.0
        self.elapsed_ms: float = 0.0

    def __enter__(self) -> Timer:
        self.started = time.perf_counter()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.elapsed_ms = (time.perf_counter() - self.started) * 1000


@contextmanager
def log_operation(
    logger: logging.Logger,
    operation: str,
    *,
    level: int = logging.INFO,
    tool: str | None = None,
    **fields: Any,
) -> Iterator[dict[str, Any]]:
    """Log the start, outcome and duration of an operation.

    On success a record is emitted with ``status="ok"``; on failure one is
    emitted with ``status="error"`` and the error detail, and the exception is
    re-raised. Exceptions are never swallowed (spec section 33), so a caller
    that needs graceful degradation must catch it explicitly.
    """
    timer = Timer()
    with timer:
        try:
            yield fields
        except Exception as error:
            logger.log(
                logging.ERROR,
                operation,
                extra={
                    "event": operation,
                    "status": "error",
                    "tool": tool,
                    "error": f"{type(error).__name__}: {error}",
                    "duration_ms": round(timer.elapsed_ms, 3),
                    **fields,
                },
                exc_info=True,
            )
            raise
    logger.log(
        level,
        operation,
        extra={
            "event": operation,
            "status": "ok",
            "tool": tool,
            "duration_ms": round(timer.elapsed_ms, 3),
            **fields,
        },
    )
