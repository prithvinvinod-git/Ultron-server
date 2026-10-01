"""Unit tests for the configuration system (T010).

These tests use no database, Redis, model provider, or network service, so they
must pass on a machine with nothing else running.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings, get_settings, is_disallowed_host, reload_settings

pytestmark = pytest.mark.unit


class TestDefaults:
    def test_boots_with_no_configuration_at_all(self) -> None:
        settings = Settings()

        assert settings.environment == "development"
        assert settings.app_name == "ULTRON"
        assert settings.app.port == 8000
        assert settings.app.is_production is False

    def test_every_section_is_available(self) -> None:
        settings = Settings()

        for section in (
            settings.app,
            settings.logging,
            settings.security,
            settings.database,
            settings.redis,
            settings.ollama,
            settings.providers,
            settings.model_router,
            settings.embeddings,
            settings.opencode,
            settings.workspaces,
            settings.agents,
            settings.tasks,
            settings.terminal,
            settings.python_tool,
            settings.web,
            settings.browser,
            settings.computer_nodes,
            settings.devices,
            settings.voice,
            settings.scheduler,
            settings.notifications,
            settings.memory,
            settings.observability,
        ):
            assert section is not None

    def test_no_provider_credentials_are_invented(self) -> None:
        """A provider with no key must report itself unconfigured, not ready."""
        providers = Settings().providers

        assert providers.openai_configured is False
        assert providers.gemini_configured is False
        assert providers.anthropic_configured is False

    def test_browser_and_voice_stay_off_by_default(self) -> None:
        """Heavy optional stacks are opt-in, never pulled in on boot."""
        settings = Settings()

        assert settings.browser.enabled is False
        assert settings.voice.enabled is False
        assert settings.voice.stt_engine == "none"
        assert settings.voice.tts_engine == "none"


class TestEnvironmentParsing:
    def test_reads_flat_environment_variables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("API_PORT", "9123")
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("LOG_LEVEL", "debug")

        settings = Settings()

        assert settings.app.port == 9123
        assert settings.environment == "production"
        assert settings.app.is_production is True
        # Lower-case input is normalised, so LOG_LEVEL=debug still validates.
        assert settings.logging.level == "DEBUG"

    def test_rejects_an_out_of_range_port(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("API_PORT", "70000")

        with pytest.raises(ValidationError):
            Settings()

    def test_rejects_a_non_numeric_port(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("API_PORT", "not-a-port")

        with pytest.raises(ValidationError):
            Settings()

    def test_rejects_an_unknown_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ENVIRONMENT", "chaos")

        with pytest.raises(ValidationError):
            Settings()

    def test_ignores_unknown_variables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ULTRON_SOMETHING_ELSE", "value")

        assert Settings().app_name == "ULTRON"

    def test_comma_separated_values_become_lists(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000, http://127.0.0.1:5173")
        monkeypatch.setenv("WAKE_WORDS", "ultron,hey ultron")
        monkeypatch.setenv("COMPUTER_CAPABILITY_PRIORITY", "dom,vision")
        monkeypatch.setenv("TERMINAL_ALLOWED_COMMANDS", "git, python")

        settings = Settings()

        assert settings.app.cors_origins == ["http://localhost:3000", "http://127.0.0.1:5173"]
        assert settings.voice.wake_words == ["ultron", "hey ultron"]
        assert settings.computer_nodes.capability_priority == ["dom", "vision"]
        assert settings.terminal.allowed_commands == ["git", "python"]

    def test_empty_comma_value_becomes_an_empty_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WAKE_WORDS", "")

        assert Settings().voice.wake_words == []

    def test_terminal_denies_everything_until_an_allowlist_is_set(self) -> None:
        """Fail closed: no allow-list means no shell execution at all."""
        assert Settings().terminal.allowed_commands == []


class TestSecrets:
    def test_a_configured_key_is_reported_as_available(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-value")

        assert Settings().providers.openai_configured is True

    def test_secret_values_are_not_leaked_by_repr(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-do-not-print-me")
        monkeypatch.setenv("JWT_SECRET", "a-real-looking-secret")

        rendered = repr(Settings())

        assert "sk-do-not-print-me" not in rendered
        assert "a-real-looking-secret" not in rendered

    def test_placeholders_are_detected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert Settings().security.jwt_placeholder is True
        assert Settings().security.admin_password_placeholder is True

        monkeypatch.setenv("JWT_SECRET", "generated-value")
        monkeypatch.setenv("ADMIN_PASSWORD", "generated-value")

        security = Settings().security
        assert security.jwt_placeholder is False
        assert security.admin_password_placeholder is False

    def test_an_empty_secret_counts_as_a_placeholder(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("JWT_SECRET", "")

        assert Settings().security.jwt_placeholder is True


class TestModelRouter:
    def test_capability_specific_model_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MODEL_DEFAULT", "ollama:base")
        monkeypatch.setenv("MODEL_CODING", "ollama:coder")

        router = Settings().model_router

        assert router.for_capability("coding") == "ollama:coder"
        assert router.for_capability("research") == "ollama:base"

    def test_unknown_capability_falls_back_to_the_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MODEL_DEFAULT", "ollama:base")

        assert Settings().model_router.for_capability("not_a_capability") == "ollama:base"

    def test_fallback_chain_is_ordered(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MODEL_FALLBACK_CHAIN", "ollama:a,openai:b")

        assert Settings().model_router.fallback_chain == ["ollama:a", "openai:b"]


class TestEnvFileResolution:
    def test_reads_the_file_named_by_ultron_env_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        env_file = tmp_path / "custom.env"
        env_file.write_text("API_PORT=7777\nLOG_FORMAT=console\n", encoding="utf-8")
        monkeypatch.setenv("ULTRON_ENV_FILE", str(env_file))

        settings = Settings()

        assert settings.app.port == 7777
        assert settings.logging.format == "console"

    def test_a_missing_named_file_is_an_error_not_a_silent_fallback(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """An operator who names a file expects it to be read."""
        monkeypatch.setenv("ULTRON_ENV_FILE", str(tmp_path / "absent.env"))

        with pytest.raises(FileNotFoundError):
            Settings()

    def test_real_environment_beats_the_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        env_file = tmp_path / "custom.env"
        env_file.write_text("API_PORT=7777\n", encoding="utf-8")
        monkeypatch.setenv("ULTRON_ENV_FILE", str(env_file))
        monkeypatch.setenv("API_PORT", "8888")

        assert Settings().app.port == 8888


class TestStartupWarnings:
    def test_clean_development_configuration_is_quiet(self) -> None:
        assert Settings().startup_warnings() == []

    def test_production_placeholder_secrets_are_flagged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ENVIRONMENT", "production")

        warnings = Settings().startup_warnings()

        assert any("JWT_SECRET" in warning for warning in warnings)
        assert any("ADMIN_PASSWORD" in warning for warning in warnings)

    def test_placeholder_secrets_are_not_complained_about_in_development(self) -> None:
        assert not any("JWT_SECRET" in warning for warning in Settings().startup_warnings())

    def test_anonymous_access_is_flagged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ALLOW_ANONYMOUS", "true")

        assert any("ALLOW_ANONYMOUS" in warning for warning in Settings().startup_warnings())

    def test_a_non_postgres_database_is_flagged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///ultron.db")

        assert any("PostgreSQL" in warning for warning in Settings().startup_warnings())

    def test_ollama_default_without_a_key_is_not_complained_about(self) -> None:
        """Ollama needs no credential; liveness is the health check's job."""
        assert not any("API key" in warning for warning in Settings().startup_warnings())

    def test_a_cloud_default_without_a_key_is_flagged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MODEL_DEFAULT", "openai:gpt-4o-mini")

        assert any("API key" in warning for warning in Settings().startup_warnings())

    def test_a_configured_cloud_default_is_quiet(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MODEL_DEFAULT", "openai:gpt-4o-mini")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-value")

        assert Settings().startup_warnings() == []


class TestSsrfGuard:
    @pytest.mark.parametrize(
        "host",
        [
            "127.0.0.1",
            "localhost",
            "10.0.0.5",
            "192.168.1.10",
            "172.16.0.1",
            "169.254.169.254",  # cloud metadata endpoint
            "0.0.0.0",
        ],
    )
    def test_internal_targets_are_blocked(self, host: str) -> None:
        assert is_disallowed_host(host) is True

    def test_a_public_address_is_allowed(self) -> None:
        assert is_disallowed_host("93.184.216.34") is False

    def test_private_networks_can_be_allowed_for_trusted_development(
        self,
    ) -> None:
        assert is_disallowed_host("192.168.1.10", allow_private=True) is False

    def test_an_unresolvable_host_fails_closed(self) -> None:
        """A target that cannot be proven public must not be contacted."""
        assert is_disallowed_host("nonexistent.invalid") is True


class TestWorkspaceRoot:
    def test_a_relative_root_resolves_against_the_repository(self, repo_root: Path) -> None:
        resolved = Settings().workspaces.resolved_root()

        assert resolved == (repo_root / "workspaces").resolve()
        assert resolved.is_absolute()

    def test_an_absolute_root_is_kept(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("WORKSPACES_ROOT", str(tmp_path))

        assert Settings().workspaces.resolved_root() == tmp_path.resolve()

    def test_the_workspace_root_is_the_only_default_allowance(self) -> None:
        allowed = Settings().workspaces.allowed_paths()

        assert len(allowed) == 1
        assert allowed[0] == Settings().workspaces.resolved_root()


class TestSettingsSingleton:
    def test_repeated_calls_return_one_instance(self) -> None:
        assert get_settings() is get_settings()

    def test_reload_builds_a_new_instance(self) -> None:
        first = get_settings()
        second = reload_settings()

        assert first is not second
        assert get_settings() is second

    def test_reload_observes_a_changed_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert get_settings().app.port == 8000
        monkeypatch.setenv("API_PORT", "8181")

        assert reload_settings().app.port == 8181
