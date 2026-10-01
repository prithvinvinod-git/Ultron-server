"""Unit tests for the error hierarchy (T012).

These assert the guarantees the rest of the codebase relies on: a stable code on
every error, a correct retry verdict, an HTTP status, and no secret or driver
detail leaking into a payload.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from app.core.errors import (
    RETRYABLE_CODES,
    AgentError,
    AuthError,
    CapabilityNotImplementedError,
    CommandNotAllowedError,
    ConfigError,
    DatabaseError,
    DependencyUnavailableError,
    ErrorCode,
    InvalidCredentialsError,
    InvalidInputError,
    LocalModelUnavailableError,
    MigrationRequiredError,
    ModelError,
    ModelTimeoutError,
    NotFoundError,
    OperationCancelledError,
    OperationTimeoutError,
    PermanentError,
    PermissionDeniedError,
    ProviderNotConfiguredError,
    RateLimitedError,
    RetryableError,
    ShuttingDownError,
    SsrfBlockedError,
    TaskNotFoundError,
    ToolVerificationError,
    UltronError,
    WorkspaceError,
    degrade,
    exit_code_for,
    is_retryable,
)

pytestmark = pytest.mark.unit

# One representative of every family, so a change to a base class is caught.
SAMPLE_ERRORS: tuple[type[UltronError], ...] = (
    InvalidInputError,
    NotFoundError,
    OperationTimeoutError,
    OperationCancelledError,
    RateLimitedError,
    DependencyUnavailableError,
    CapabilityNotImplementedError,
    ShuttingDownError,
    ConfigError,
    AuthError,
    PermissionDeniedError,
    SsrfBlockedError,
    DatabaseError,
    MigrationRequiredError,
    LocalModelUnavailableError,
    ProviderNotConfiguredError,
    ModelTimeoutError,
    ToolVerificationError,
    CommandNotAllowedError,
    TaskNotFoundError,
    AgentError,
    WorkspaceError,
)

#: A representative instance of each type above.
#:
#: Most errors derive their message from a domain value — a resource name, a
#: provider, a tool — because that is what makes the message actionable. They
#: therefore cannot all be built from a bare message, so the contract is
#: expressed with one factory per type rather than a uniform constructor call.
SAMPLE_INSTANCES: tuple[UltronError, ...] = (
    InvalidInputError("bad input"),
    NotFoundError("task", "t-1"),
    OperationTimeoutError("model.generate", timeout=30.0),
    OperationCancelledError("chat.stream"),
    RateLimitedError(retry_after=5.0, source="openai"),
    DependencyUnavailableError("redis"),
    CapabilityNotImplementedError("voice"),
    ShuttingDownError(),
    ConfigError("bad config"),
    AuthError(),
    PermissionDeniedError("workspace.delete"),
    SsrfBlockedError("169.254.169.254"),
    DatabaseError("select tasks"),
    MigrationRequiredError("head"),
    LocalModelUnavailableError("ollama"),
    ProviderNotConfiguredError("openai"),
    ModelTimeoutError("openai", timeout=60.0),
    ToolVerificationError("web.fetch"),
    CommandNotAllowedError("rm -rf"),
    TaskNotFoundError("t-9"),
    AgentError("planner"),
    WorkspaceError("projects"),
)


#: Intermediate bases, excluded from contract checks. They exist to be
#: subclassed and share the generic ``INTERNAL_ERROR`` code, so a per-code
#: invariant says nothing about them.
_ABSTRACT_BASES = frozenset({PermanentError, RetryableError})


def _concrete_error_types() -> tuple[type[UltronError], ...]:
    """Return every concrete error class, not just the sampled representatives.

    ``SAMPLE_ERRORS`` exists to keep the suite fast and readable; it must not be
    what proves a module-wide invariant such as the retryable-code agreement.
    """
    found: list[type[UltronError]] = []
    pending: list[type[UltronError]] = [UltronError]
    while pending:
        current = pending.pop()
        pending.extend(current.__subclasses__())
        if current is not UltronError:
            found.append(current)
    return tuple(error_type for error_type in found if error_type not in _ABSTRACT_BASES)


def test_every_code_is_backed_by_an_error() -> None:
    """A code with no error behind it is dead contract surface.

    Clients are told these values exist, so an unused one is a promise ULTRON
    cannot keep. ``INTERNAL_ERROR`` is backed by the bases themselves, which is
    exactly what a bare ``UltronError`` carries.
    """
    defined = {error_type.code for error_type in _concrete_error_types()}

    assert set(ErrorCode) - defined <= {ErrorCode.INTERNAL}


class TestErrorContract:
    @pytest.mark.parametrize("error_type", SAMPLE_ERRORS)
    def test_every_error_is_an_ultron_error(self, error_type: type[UltronError]) -> None:
        assert issubclass(error_type, UltronError)

    @pytest.mark.parametrize("error_type", SAMPLE_ERRORS)
    def test_every_error_has_a_code(self, error_type: type[UltronError]) -> None:
        assert isinstance(error_type.code, ErrorCode)

    @pytest.mark.parametrize("error_type", SAMPLE_ERRORS)
    def test_every_error_has_a_valid_status(self, error_type: type[UltronError]) -> None:
        assert 400 <= error_type.http_status <= 599

    def test_the_samples_cover_every_represented_type(self) -> None:
        """Keeps the instance list from drifting away from the type list."""
        assert {type(error) for error in SAMPLE_INSTANCES} == set(SAMPLE_ERRORS)

    @pytest.mark.parametrize("error", SAMPLE_INSTANCES)
    def test_every_error_builds_with_only_its_documented_arguments(
        self, error: UltronError
    ) -> None:
        """A caller must never need internal knowledge to raise an error.

        Every error is constructible from values the raising site already has
        (a resource name, a provider, a tool). None may demand something only
        the error class itself could know.
        """
        assert isinstance(error, UltronError)
        assert error.message
        assert isinstance(error.to_dict(), dict)

    @pytest.mark.parametrize("error", SAMPLE_INSTANCES)
    def test_every_error_names_its_code(self, error: UltronError) -> None:
        assert str(error.code) in str(error)

    def test_the_code_string_is_stable(self) -> None:
        """Clients match on this value, so it is part of the public contract."""
        assert str(LocalModelUnavailableError("ollama").code) == "LOCAL_MODEL_UNAVAILABLE"

    def test_the_code_appears_in_the_string_form(self) -> None:
        assert "INVALID_INPUT" in str(InvalidInputError("bad"))

    def test_details_appear_in_the_string_form(self) -> None:
        error = InvalidInputError("bad", details={"field": "port"})

        assert "port" in str(error)


class TestStructuredPayload:
    def test_the_payload_has_the_required_keys(self) -> None:
        payload = InvalidInputError("bad").to_dict()

        assert payload["code"] == "INVALID_INPUT"
        assert payload["message"] == "bad"
        assert payload["retryable"] is False

    def test_details_are_included_when_present(self) -> None:
        payload = NotFoundError("task", "t-1").to_dict()

        assert payload["details"] == {"resource": "task", "id": "t-1"}

    def test_empty_details_are_omitted(self) -> None:
        assert "details" not in InvalidInputError("bad").to_dict()

    def test_the_cause_type_is_included(self) -> None:
        error = DatabaseError("connect", cause=OSError("refused"))

        assert error.to_dict()["cause"] == "OSError"

    def test_the_cause_message_is_not_leaked(self) -> None:
        """A driver message can embed a connection string with a password."""
        error = DatabaseError("connect", cause=OSError("postgres://ultron:hunter2@db/ultron"))

        rendered = str(error.to_dict())

        assert "hunter2" not in rendered

    def test_the_payload_is_json_friendly(self) -> None:
        """Events serialise the payload, so nothing in it may be a raw object."""
        import json

        payload = ModelError("ollama", "failed", model="qwen").to_dict()
        round_tripped = json.loads(json.dumps(payload))

        assert round_tripped["details"] == {"provider": "ollama", "model": "qwen"}


class TestRetryVerdict:
    @pytest.mark.parametrize(
        "error",
        [
            OperationTimeoutError("op", timeout=5.0),
            RateLimitedError(),
            DependencyUnavailableError("redis"),
            LocalModelUnavailableError("ollama"),
            ModelTimeoutError("ollama"),
            DatabaseError("query"),
        ],
    )
    def test_transient_failures_are_retryable(self, error: UltronError) -> None:
        assert is_retryable(error) is True

    @pytest.mark.parametrize(
        "error",
        [
            InvalidInputError("bad"),
            PermissionDeniedError("delete"),
            NotFoundError("task", "t-1"),
            ProviderNotConfiguredError("openai"),
            CommandNotAllowedError("rm"),
        ],
    )
    def test_permanent_failures_are_not_retryable(self, error: UltronError) -> None:
        assert is_retryable(error) is False

    def test_a_missing_key_is_not_retryable_in_place(self) -> None:
        """Retrying without a credential cannot succeed; fall back instead."""
        assert ProviderNotConfiguredError("openai").retryable is False

    def test_cancellation_is_never_retryable(self) -> None:
        assert is_retryable(asyncio.CancelledError()) is False

    def test_cancellation_is_a_base_exception(self) -> None:
        """It must not be caught by a bare ``except Exception``."""
        assert not isinstance(asyncio.CancelledError(), Exception)

    def test_a_driver_timeout_is_treated_as_retryable(self) -> None:
        assert is_retryable(TimeoutError()) is True

    def test_an_untyped_connection_error_is_retryable(self) -> None:
        assert is_retryable(ConnectionResetError()) is True

    def test_a_programming_error_is_not_retryable(self) -> None:
        assert is_retryable(TypeError("bad argument")) is False

    def test_every_retryable_code_is_claimed_by_a_retryable_error(self) -> None:
        """A code listed as retryable must map to an error that says so.

        Without this, adding a code to ``RETRYABLE_CODES`` while forgetting to
        set ``retryable`` on the class would retry a permanent failure forever.
        """
        by_code = {error_type.code: error_type for error_type in _concrete_error_types()}

        for code in RETRYABLE_CODES:
            error_type = by_code.get(code)
            assert error_type is not None, f"{code} is retryable but no error defines it"
            assert error_type.retryable is True, f"{error_type.__name__} should be retryable"

    def test_every_retryable_error_has_its_code_in_the_retryable_set(self) -> None:
        """The two sources of retryability must agree in both directions.

        ``is_retryable`` reads the class flag while the model router reads
        ``RETRYABLE_CODES``. An error that is retryable by one and absent from
        the other would be retried in one path and treated as permanent in the
        other. The abstract bases are excluded: they exist to be subclassed and
        deliberately share the generic ``INTERNAL_ERROR`` code.
        """
        for error_type in _concrete_error_types():
            if error_type.retryable:
                assert error_type.code in RETRYABLE_CODES, (
                    f"{error_type.__name__} is retryable but {error_type.code} "
                    f"is missing from RETRYABLE_CODES"
                )
            else:
                assert error_type.code not in RETRYABLE_CODES, (
                    f"{error_type.__name__} is permanent but {error_type.code} "
                    f"is listed in RETRYABLE_CODES"
                )


class TestHttpStatus:
    @pytest.mark.parametrize(
        ("error", "status"),
        [
            (InvalidInputError("x"), 422),
            (NotFoundError("task", "t"), 404),
            (AuthError(), 401),
            (InvalidCredentialsError(), 401),
            (PermissionDeniedError("x"), 403),
            (SsrfBlockedError("10.0.0.1"), 403),
            (OperationTimeoutError("op"), 504),
            (OperationCancelledError("op"), 499),
            (RateLimitedError(), 429),
            (DependencyUnavailableError("redis"), 503),
            (ShuttingDownError(), 503),
            (CapabilityNotImplementedError("voice"), 501),
        ],
    )
    def test_statuses_match_the_failure(self, error: UltronError, status: int) -> None:
        assert error.http_status == status

    def test_oversized_known_reasons_are_not_retried(self) -> None:
        """A migration must be run, not waited out."""
        assert MigrationRequiredError("upgrade").retryable is False


class TestErrorMessages:
    def test_a_not_found_error_names_the_resource(self) -> None:
        error = TaskNotFoundError("task-9")

        assert error.identifier == "task-9"
        assert "task-9" in error.message

    def test_invalid_credentials_do_not_reveal_which_part_failed(self) -> None:
        """The message must not become a probe for valid usernames."""
        message = InvalidCredentialsError().message

        assert "username" not in message.lower().replace("username or password", "")
        assert "password" not in message.lower().replace("username or password", "")

    def test_a_timeout_records_its_budget(self) -> None:
        error = OperationTimeoutError("model.generate", timeout=180.0)

        assert error.to_dict()["details"]["timeout"] == 180.0

    def test_a_rate_limit_records_retry_after(self) -> None:
        error = RateLimitedError(retry_after=12.0)

        assert error.retry_after == 12.0

    def test_a_dependency_error_names_the_dependency(self) -> None:
        error = DependencyUnavailableError("ollama", reason="connection refused")

        assert "ollama" in error.message
        assert error.dependency == "ollama"

    def test_an_ssrf_block_records_the_host(self) -> None:
        assert SsrfBlockedError("169.254.169.254").host == "169.254.169.254"


class TestExitCodes:
    def test_a_config_error_uses_ex_config(self) -> None:
        assert exit_code_for(ConfigError("bad")) == 78

    def test_a_missing_capability_uses_ex_unavailable(self) -> None:
        assert exit_code_for(CapabilityNotImplementedError("voice")) == 69

    def test_shutdown_uses_ex_tempfail(self) -> None:
        assert exit_code_for(ShuttingDownError()) == 75

    def test_anything_else_is_a_plain_failure(self) -> None:
        assert exit_code_for(InvalidInputError("x")) == 1


class TestDegrade:
    async def test_the_value_is_returned_on_success(self) -> None:
        async def works() -> str:
            return "ok"

        assert await degrade("op", works(), "fallback") == "ok"

    async def test_the_fallback_is_returned_on_a_dependency_failure(self) -> None:
        async def broken() -> str:
            raise DependencyUnavailableError("redis")

        assert await degrade("op", broken(), "fallback") == "fallback"

    async def test_a_programming_error_is_not_disguised_as_degradation(self) -> None:
        """A TypeError must propagate rather than look like a missing service."""

        async def broken() -> str:
            raise TypeError("bad argument")

        with pytest.raises(TypeError):
            await degrade("op", broken(), "fallback")

    async def test_the_loss_is_logged_rather_than_discarded(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Spec section 33 forbids silently swallowing an exception."""

        async def broken() -> str:
            raise DependencyUnavailableError("redis")

        with caplog.at_level(logging.WARNING, logger="ultron"):
            await degrade("fetch", broken(), None, logger=logging.getLogger("ultron"))

        assert "degraded" in caplog.text
        assert "fetch" in caplog.text

    async def test_a_narrow_error_type_can_be_requested(self) -> None:
        async def broken() -> str:
            raise RateLimitedError()

        assert await degrade("op", broken(), "fb", on_error=RateLimitedError) == "fb"

    async def test_a_broken_awaitable_type_propagates(self) -> None:
        async def broken() -> str:
            raise ValueError("wrong kind of failure")

        with pytest.raises(ValueError):
            await degrade("op", broken(), "fallback")


class TestRetryableBase:
    def test_the_base_class_marks_itself(self) -> None:
        assert RetryableError("x").retryable is True

    def test_a_subclass_inherits_the_verdict(self) -> None:
        class CustomRetryableError(RetryableError):
            code = ErrorCode.DEPENDENCY_UNAVAILABLE

        assert is_retryable(CustomRetryableError("x")) is True
