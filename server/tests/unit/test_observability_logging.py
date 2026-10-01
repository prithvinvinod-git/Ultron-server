"""Unit tests for structured logging (T011).

No file or network access is required beyond a temporary log file.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

import pytest

from app.config import LoggingSettings
from app.observability.logging import (
    REDACTED,
    ConsoleFormatter,
    JsonFormatter,
    Timer,
    agent_context,
    configure_logging,
    correlation,
    current_context,
    get_agent_id,
    get_logger,
    get_request_id,
    get_task_id,
    log_operation,
    new_id,
    redact,
    request_context,
    scrub_text,
    task_context,
)

pytestmark = pytest.mark.unit

SECRET_KEYS = ("password", "token", "secret", "api_key", "authorization")


def _record(**fields: object) -> logging.LogRecord:
    """Build a bare record carrying the given ``extra`` fields."""
    record = logging.LogRecord(
        name="ultron.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )
    for key, value in fields.items():
        setattr(record, key, value)
    return record


def _extra(record: logging.LogRecord, name: str) -> Any:
    """Read a caller-supplied field off a record.

    ``LogRecord`` stores ``extra`` entries as plain attributes, which the type
    checker cannot see. This keeps that one cast in a single place.
    """
    return record.__dict__[name]


def _json_for(record: logging.LogRecord, **kwargs: object) -> dict[str, Any]:
    for key, value in kwargs.items():
        setattr(record, key, value)
    decoded: dict[str, Any] = json.loads(JsonFormatter(SECRET_KEYS).format(record))
    return decoded


class TestRedaction:
    def test_a_top_level_secret_is_replaced(self) -> None:
        assert redact({"api_key": "sk-live-1"}, SECRET_KEYS) == {"api_key": REDACTED}

    def test_a_nested_secret_is_replaced(self) -> None:
        payload = {"request": {"headers": {"authorization": "Bearer abc"}}}

        result = redact(payload, SECRET_KEYS)

        assert result["request"]["headers"]["authorization"] == REDACTED

    def test_matching_ignores_case_and_separators(self) -> None:
        payload = {"OpenAI_API_KEY": "x", "Password": "y", "apiKey": "z"}

        result = redact(payload, SECRET_KEYS)

        assert set(result.values()) == {REDACTED}

    def test_a_secret_inside_a_list_is_replaced(self) -> None:
        assert redact([{"token": "t"}], SECRET_KEYS) == [{"token": REDACTED}]

    def test_non_secret_fields_survive(self) -> None:
        result = redact({"user": "alice", "password": "p"}, SECRET_KEYS)

        assert result == {"user": "alice", "password": REDACTED}

    def test_a_scalars_length_is_not_leaked(self) -> None:
        """The marker is fixed, so a secret's length is not disclosed."""
        result = redact({"token": "a" * 40}, SECRET_KEYS)

        assert result["token"] == REDACTED

    def test_the_original_payload_is_not_mutated(self) -> None:
        payload = {"password": "p"}

        redact(payload, SECRET_KEYS)

        assert payload == {"password": "p"}


class TestScrubText:
    """Free-text redaction, where there is no field name to match."""

    def test_a_key_value_pair_is_scrubbed(self) -> None:
        assert scrub_text("api_key=abcdef123456") == f"api_key={REDACTED}"

    def test_a_bearer_token_is_scrubbed(self) -> None:
        assert scrub_text("Authorization: Bearer abcdefghijklmnop") == f"Authorization: {REDACTED}"

    def test_credentials_in_a_connection_url_are_scrubbed(self) -> None:
        """A driver exception routinely carries the DSN it failed on."""
        scrubbed = scrub_text("could not connect to postgres://ultron:hunter2@db:5432/ultron")

        assert "hunter2" not in scrubbed
        assert "ultron" in scrubbed
        assert "db:5432/ultron" in scrubbed

    def test_a_provider_key_is_scrubbed(self) -> None:
        token = "sk-abcdef0123456789abcdef0123456789"

        assert token not in scrub_text(f"key is {token} now")

    def test_a_jwt_is_scrubbed(self) -> None:
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.dBjftJeZ4CVPmB92K"

        assert jwt not in scrub_text(f"session {jwt} expired")

    def test_ordinary_text_is_untouched(self) -> None:
        text = "reindexed 3 workspaces, 12 documents, mode=full"

        assert scrub_text(text) == text

    def test_a_short_hyphenated_word_is_not_scrubbed(self) -> None:
        """Narrow on purpose: over-broad scrubbing trains operators to ignore it."""
        assert scrub_text("task=cleanup sk-short") == "task=cleanup sk-short"


class TestCorrelationContext:
    def test_ids_default_to_unset(self) -> None:
        assert get_request_id() is None
        assert get_task_id() is None
        assert get_agent_id() is None
        assert current_context() == {}

    def test_a_request_id_is_bound_inside_the_block(self) -> None:
        with request_context() as value:
            assert get_request_id() == value
        assert get_request_id() is None

    def test_an_explicit_id_is_respected(self) -> None:
        with request_context("req-1"):
            assert get_request_id() == "req-1"

    def test_ids_are_independent(self) -> None:
        with request_context("req-1"), task_context("task-1"), agent_context("agent-1"):
            assert current_context() == {
                "request_id": "req-1",
                "task_id": "task-1",
                "agent_id": "agent-1",
            }

    def test_a_nested_request_restores_the_parent_id(self) -> None:
        with request_context("outer"):
            with request_context("inner"):
                assert get_request_id() == "inner"
            assert get_request_id() == "outer"

    def test_correlation_binds_several_at_once(self) -> None:
        with correlation(request_id="r", task_id="t"):
            assert current_context() == {"request_id": "r", "task_id": "t"}

    def test_correlation_ignores_none_values(self) -> None:
        with correlation(request_id="r"):
            assert current_context() == {"request_id": "r"}

    def test_generated_ids_are_unique(self) -> None:
        assert new_id() != new_id()


class TestJsonFormatter:
    def test_a_record_carries_the_spec_fields(self) -> None:
        """Spec section 32 names these fields explicitly."""
        payload = _json_for(_record())

        for field in (
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
        ):
            assert field in payload

    def test_unset_fields_are_null_rather_than_absent(self) -> None:
        payload = _json_for(_record())

        assert payload["request_id"] is None
        assert payload["tool"] is None

    def test_ambient_ids_are_picked_up(self) -> None:
        with request_context("req-9"), task_context("task-9"):
            payload = _json_for(_record())

        assert payload["request_id"] == "req-9"
        assert payload["task_id"] == "task-9"

    def test_an_explicit_field_beats_the_ambient_context(self) -> None:
        with request_context("ambient"):
            payload = _json_for(_record(), request_id="explicit")

        assert payload["request_id"] == "explicit"

    def test_extra_fields_are_included(self) -> None:
        payload = _json_for(_record(), tool="web.fetch", status="ok", duration_ms=12.5)

        assert payload["tool"] == "web.fetch"
        assert payload["status"] == "ok"
        assert payload["duration_ms"] == 12.5

    def test_a_secret_in_an_extra_is_redacted(self) -> None:
        record = _record(api_key="sk-leak-me")

        payload = json.loads(JsonFormatter(SECRET_KEYS).format(record))

        assert "sk-leak-me" not in json.dumps(payload)
        assert payload["api_key"] == REDACTED

    def test_a_secret_interpolated_into_the_message_is_redacted(self) -> None:
        """A credential passed as a message argument must not reach the log.

        There is no field name to match here, so this exercises the text
        patterns. The token is full length on purpose: a 7-character value is
        not a credential, and matching those would mangle ordinary log output.
        """
        token = "sk-abcdef0123456789abcdef0123456789"
        record = logging.LogRecord(
            "ultron.test", logging.INFO, __file__, 1, "using %s", (token,), None
        )

        payload = json.loads(JsonFormatter(SECRET_KEYS).format(record))

        assert token not in payload["message"]
        assert REDACTED in payload["message"]

    def test_ordinary_hyphenated_text_is_not_mangled(self) -> None:
        """The safety net must stay narrow or operators stop trusting it."""
        record = _record()
        record.msg = "task=cleanup run-mode=fast sk-short"
        record.args = ()

        payload = json.loads(JsonFormatter(SECRET_KEYS).format(record))

        assert payload["message"] == "task=cleanup run-mode=fast sk-short"

    def test_a_traceback_is_captured_for_a_failure(self) -> None:
        try:
            raise ValueError("boom")
        except ValueError:
            record = logging.LogRecord(
                "ultron.test",
                logging.ERROR,
                __file__,
                1,
                "failed",
                (),
                sys.exc_info(),
            )

        payload = json.loads(JsonFormatter(SECRET_KEYS).format(record))

        assert "ValueError" in payload["traceback"]

    def test_a_secret_in_the_traceback_is_redacted(self) -> None:
        """A driver exception often carries the connection string that failed."""
        try:
            raise ConnectionError("could not reach postgres://ultron:hunter2@db:5432/ultron")
        except ConnectionError:
            record = logging.LogRecord(
                "ultron.test", logging.ERROR, __file__, 1, "query failed", (), sys.exc_info()
            )

        payload = json.loads(JsonFormatter(SECRET_KEYS).format(record))

        assert "hunter2" not in payload["traceback"]
        assert "ConnectionError" in payload["traceback"]

    def test_the_output_is_one_line(self) -> None:
        assert "\n" not in JsonFormatter(SECRET_KEYS).format(_record())


class TestConsoleFormatter:
    def test_the_message_is_present(self) -> None:
        rendered = ConsoleFormatter(SECRET_KEYS).format(_record())

        assert "hello" in rendered

    def test_a_secret_in_the_message_is_redacted(self) -> None:
        """A credential interpolated into a message must not reach the console."""
        token = "sk-abcdef0123456789abcdef0123456789"
        record = logging.LogRecord(
            "ultron.test", logging.INFO, __file__, 1, "key=%s", (token,), None
        )

        assert token not in ConsoleFormatter(SECRET_KEYS).format(record)

    def test_correlation_appears_in_the_line(self) -> None:
        with request_context("req-7"):
            rendered = ConsoleFormatter(SECRET_KEYS).format(_record())

        assert "request_id=req-7" in rendered

    def test_extras_appear_in_the_line(self) -> None:
        rendered = ConsoleFormatter(SECRET_KEYS).format(_record(tool="git.status"))

        assert "tool=git.status" in rendered

    def test_the_original_record_is_not_mutated(self) -> None:
        """One handler must not alter what another handler sees."""
        record = _record()
        record.msg = "token=sk-shared"
        record.args = ()

        ConsoleFormatter(SECRET_KEYS).format(record)

        assert record.msg == "token=sk-shared"


class TestConfigureLogging:
    def test_a_logger_is_returned(self) -> None:
        assert configure_logging(LoggingSettings()) is logging.getLogger("ultron")

    def test_calling_twice_does_not_duplicate_handlers(self) -> None:
        configure_logging(LoggingSettings())
        first = len(logging.getLogger("ultron").handlers)

        configure_logging(LoggingSettings())

        assert len(logging.getLogger("ultron").handlers) == first

    def test_the_level_is_applied(self) -> None:
        logger = configure_logging(LoggingSettings(level="DEBUG"))

        assert logger.level == logging.DEBUG

    def test_a_log_file_is_written(self, tmp_path: Path) -> None:
        target = tmp_path / "logs" / "ultron.jsonl"
        logger = configure_logging(LoggingSettings(file=str(target)))

        logger.info("persisted", extra={"event": "test"})

        assert target.exists()
        assert "persisted" in target.read_text(encoding="utf-8")

    def test_the_log_file_is_appended_to(self, tmp_path: Path) -> None:
        target = tmp_path / "ultron.jsonl"
        target.write_text('{"existing": true}\n', encoding="utf-8")

        configure_logging(LoggingSettings(file=str(target))).info("second")

        content = target.read_text(encoding="utf-8")
        assert "existing" in content
        assert "second" in content

    def test_json_is_the_production_default(self) -> None:
        settings = LoggingSettings()

        assert settings.format == "json"

    def test_get_logger_keeps_the_namespace(self) -> None:
        assert get_logger("database").name == "ultron.database"
        assert get_logger("ultron.database").name == "ultron.database"
        assert get_logger().name == "ultron"


class TestTimer:
    def test_a_duration_is_measured(self) -> None:
        with Timer() as timer:
            pass

        assert timer.elapsed_ms >= 0.0

    def test_the_duration_is_measured_even_on_failure(self) -> None:
        timer = Timer()
        with pytest.raises(ValueError), timer:
            raise ValueError("boom")

        assert timer.elapsed_ms > 0.0


class TestLogOperation:
    def test_success_is_logged_with_ok_status(self, caplog: pytest.LogCaptureFixture) -> None:
        logger = logging.getLogger("ultron.test.op")

        with (
            caplog.at_level(logging.INFO, logger="ultron.test.op"),
            log_operation(logger, "did_work", tool="web"),
        ):
            pass

        record = caplog.records[-1]
        assert _extra(record, "status") == "ok"
        assert _extra(record, "tool") == "web"
        assert _extra(record, "duration_ms") >= 0.0

    def test_failure_is_logged_and_re_raised(self, caplog: pytest.LogCaptureFixture) -> None:
        logger = logging.getLogger("ultron.test.op")

        with (
            caplog.at_level(logging.INFO, logger="ultron.test.op"),
            pytest.raises(ValueError, match="boom"),
            log_operation(logger, "did_work"),
        ):
            raise ValueError("boom")

        record = caplog.records[-1]
        assert _extra(record, "status") == "error"
        assert "ValueError" in _extra(record, "error")

    def test_exceptions_are_never_swallowed(self) -> None:
        """Spec section 33: nothing is silently discarded."""
        with pytest.raises(ValueError), log_operation(logging.getLogger("ultron"), "op"):
            raise ValueError("boom")

    def test_extra_fields_are_carried(self, caplog: pytest.LogCaptureFixture) -> None:
        logger = logging.getLogger("ultron.test.op")

        with (
            caplog.at_level(logging.INFO, logger="ultron.test.op"),
            log_operation(logger, "op", item_id=42),
        ):
            pass

        assert _extra(caplog.records[-1], "item_id") == 42
