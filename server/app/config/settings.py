"""ULTRON configuration.

Every setting comes from the environment or an ``.env`` file. Nothing is
hard-coded: no API keys, no model names, no Windows paths. The same settings
object drives the Windows development machine and the Ubuntu deployment host.

Configuration is split into focused, nested sections so that a subsystem can
depend on the slice it needs rather than on the whole application. The full
documented reference lives in ``docs/configuration.md``; the exhaustive list of
supported variables with placeholder values is in ``.env.example``.

Env file resolution
-------------------
``.env`` is looked up, in order, at:

1. ``$ULTRON_ENV_FILE`` when set (exact path, wins outright);
2. the current working directory;
3. the repository root, i.e. the parent of the ``server`` package directory.

That last entry is what lets ``scripts/dev.sh`` work from the repository root
while the package lives in ``server/``.
"""

from __future__ import annotations

import functools
import ipaddress
import os
import socket
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

Environment = Literal["development", "staging", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


def _repository_root() -> Path:
    """Return the repository root, i.e. the parent of ``server/``."""
    return Path(__file__).resolve().parents[3]


def _is_placeholder(secret: SecretStr) -> bool:
    """Return True when a secret is empty or still holds its example value."""
    value = secret.get_secret_value().strip()
    return not value or value.upper().startswith("CHANGE_ME")


class AppSettings(BaseModel):
    """Core application identity and HTTP server settings."""

    name: str = "ULTRON"
    environment: Environment = "development"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = Field(default=8000, ge=1, le=65535)
    workers: int = Field(default=1, ge=1, le=64)
    reload: bool = False
    root_path: str = ""
    shutdown_timeout: int = Field(default=30, ge=0, le=600)
    cors_origins: list[str] = Field(default_factory=list)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: Any) -> Any:
        """Accept a comma-separated string as well as a real list."""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


class LoggingSettings(BaseModel):
    """Structured logging configuration (spec section 32)."""

    level: LogLevel = "INFO"
    format: Literal["json", "console"] = "json"
    file: str = ""
    include_metrics_in_log: bool = False
    redact_keys: tuple[str, ...] = (
        "password",
        "token",
        "secret",
        "api_key",
        "authorization",
        "jwt",
        "private_key",
    )

    @field_validator("level", mode="before")
    @classmethod
    def _upper_level(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.upper()
        return value


class SecuritySettings(BaseModel):
    """Authentication and hardening settings (spec sections 30, 31)."""

    jwt_secret: SecretStr = SecretStr("CHANGE_ME_generate_a_random_secret")
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = Field(default=60, ge=1)
    refresh_token_expire_days: int = Field(default=30, ge=1)
    admin_username: str = "admin"
    admin_password: SecretStr = SecretStr("CHANGE_ME")
    argon2_time_cost: int = Field(default=3, ge=1, le=10)
    argon2_memory_cost: int = Field(default=65536, ge=8, le=1048576)
    argon2_parallelism: int = Field(default=4, ge=1, le=16)
    allow_anonymous: bool = False
    rate_limit_enabled: bool = True
    rate_limit_requests: int = Field(default=120, ge=1)

    @property
    def jwt_placeholder(self) -> bool:
        """True while ``JWT_SECRET`` still holds its example value."""
        return _is_placeholder(self.jwt_secret)

    @property
    def admin_password_placeholder(self) -> bool:
        """True while ``ADMIN_PASSWORD`` still holds its example value."""
        return _is_placeholder(self.admin_password)


class DatabaseSettings(BaseModel):
    """PostgreSQL settings. PostgreSQL is the authoritative store."""

    url: str = "postgresql+asyncpg://ultron:CHANGE_ME@localhost:5432/ultron"
    pool_size: int = Field(default=10, ge=1, le=200)
    max_overflow: int = Field(default=20, ge=0, le=200)
    pool_timeout: int = Field(default=30, ge=1, le=300)
    pool_recycle: int = Field(default=1800, ge=0)
    echo: bool = False
    auto_migrate: bool = False
    enable_pgvector: bool = True

    @property
    def is_async(self) -> bool:
        return "+asyncpg" in self.url or "+aiosqlite" in self.url


class RedisSettings(BaseModel):
    """Redis settings: cache, queues, pub/sub, locks, ephemeral state only."""

    url: str = "redis://localhost:6379/0"
    max_connections: int = Field(default=50, ge=1, le=1000)
    socket_timeout: int = Field(default=5, ge=1, le=300)
    default_ttl: int = Field(default=3600, ge=1)
    event_bridge: bool = False


class OllamaSettings(BaseModel):
    """Ollama settings (spec section 21).

    The host is configurable because Ollama is not assumed to run on localhost
    in production, and the same setting reaches a hosted Ollama as readily as a
    local one: both speak the Ollama HTTP API, so the only difference is that a
    hosted instance authenticates. That is what ``api_key`` is for.
    """

    url: str = "http://localhost:11434"
    api_key: SecretStr = SecretStr("")
    timeout: int = Field(default=120, ge=1)
    connect_timeout: int = Field(default=10, ge=1)
    keepalive: str = "5m"
    num_ctx: int = Field(default=4096, ge=256)
    models: list[str] = Field(default_factory=list)
    embedding_model: str = ""
    healthcheck: bool = True

    @field_validator("models", mode="before")
    @classmethod
    def _split_models(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("url")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @property
    def is_configured(self) -> bool:
        return bool(self.url)

    @property
    def api_key_configured(self) -> bool:
        """True when a token is present.

        A hosted Ollama rejects an unauthenticated request, so this is the
        difference between a reachable service and a 401.
        """
        return bool(self.api_key.get_secret_value().strip())

    def auth_headers(self) -> dict[str, str]:
        """Headers for an Ollama API call, including the bearer token if set.

        A local Ollama needs no credential, so the header is omitted rather than
        sent empty; a hosted one needs it, and the two differ by configuration
        alone rather than by a code path.
        """
        headers = {"Accept": "application/json"}
        if self.api_key_configured:
            headers["Authorization"] = f"Bearer {self.api_key.get_secret_value()}"
        return headers


class ProviderSettings(BaseModel):
    """Optional cloud provider credentials (spec section 3).

    A provider with no API key is *unavailable*, not broken. The server boots
    with every one of these empty.
    """

    openai_api_key: SecretStr = SecretStr("")
    openai_base_url: str = "https://api.openai.com/v1"
    openai_timeout: int = Field(default=60, ge=1)

    gemini_api_key: SecretStr = SecretStr("")
    gemini_base_url: str = "https://generativelanguage.googleapis.com"
    gemini_timeout: int = Field(default=60, ge=1)

    anthropic_api_key: SecretStr = SecretStr("")
    anthropic_base_url: str = "https://api.anthropic.com"
    anthropic_timeout: int = Field(default=60, ge=1)

    @property
    def openai_configured(self) -> bool:
        return bool(self.openai_api_key.get_secret_value().strip())

    @property
    def gemini_configured(self) -> bool:
        return bool(self.gemini_api_key.get_secret_value().strip())

    @property
    def anthropic_configured(self) -> bool:
        return bool(self.anthropic_api_key.get_secret_value().strip())


class ModelRouterSettings(BaseModel):
    """Model selection configuration (spec sections 20, 48, 49).

    Model identifiers are ``<provider>:<model>`` and are never hard-coded in the
    application; all selection happens from these settings.
    """

    default: str = "ollama:qwen2.5:7b-instruct"
    coding: str = ""
    research: str = ""
    vision: str = ""
    fast: str = ""
    reasoning: str = ""
    fallback_chain: list[str] = Field(default_factory=list)
    max_output_tokens: int = Field(default=4096, ge=1)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    request_timeout: int = Field(default=180, ge=1)
    max_retries: int = Field(default=2, ge=0, le=10)
    retry_backoff: float = Field(default=2.0, ge=1.0, le=30.0)
    usage_tracking: bool = True
    health_refresh: int = Field(default=60, ge=5)

    @field_validator("fallback_chain", mode="before")
    @classmethod
    def _split_chain(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    def for_capability(self, capability: str) -> str:
        """Return the configured model for a capability, or the default.

        Unknown capabilities fall back to the default rather than raising, so a
        new agent can ask for a capability the operator has not configured yet.
        """
        selected = getattr(self, capability, "") or self.default
        return selected


class EmbeddingSettings(BaseModel):
    """Embedding provider configuration for semantic memory."""

    provider: Literal["ollama", "openai", "none"] = "ollama"
    model: str = ""
    dimensions: int = Field(default=768, ge=8, le=8192)
    timeout: int = Field(default=30, ge=1)
    allow_hash_fallback: bool = False


class OpenCodeSettings(BaseModel):
    """OpenCode coding engine settings (spec sections 8, 9)."""

    path: str = "opencode"
    model: str = ""
    provider: str = ""
    timeout: int = Field(default=1800, ge=1)
    max_output_lines: int = Field(default=5000, ge=1)
    extra_args: str = ""
    workspace_template: str = ""
    git_isolation: bool = True
    git_branch_prefix: str = "ultron"


class WorkspaceSettings(BaseModel):
    """Workspace root and sandbox configuration (spec section 46)."""

    root: str = "./workspaces"
    allowed_roots: list[str] = Field(default_factory=list)
    cleanup_on_task_end: bool = False
    max_size_mb: int = Field(default=5120, ge=1)

    @field_validator("allowed_roots", mode="before")
    @classmethod
    def _split_roots(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    def resolved_root(self) -> Path:
        """Resolve the workspace root against the repository, never the CWD."""
        root = Path(self.root).expanduser()
        if not root.is_absolute():
            root = _repository_root() / root
        return root.resolve()

    def allowed_paths(self) -> list[Path]:
        """Every directory filesystem and git operations may touch.

        When no explicit allow-list is configured, the workspace root is the
        only permitted location. An agent therefore never inherits the whole
        server filesystem.
        """
        roots = [self.resolved_root()]
        roots.extend(Path(item).expanduser().resolve() for item in self.allowed_roots)
        return roots


class AgentSettings(BaseModel):
    """Agent runtime limits (spec sections 6, 7, 41, 48)."""

    default_timeout: int = Field(default=900, ge=1)
    max_concurrency: int = Field(default=4, ge=1, le=256)
    max_per_type: int = Field(default=8, ge=1, le=256)
    heartbeat_interval: int = Field(default=15, ge=1)
    default_permission_level: int = Field(default=1, ge=0, le=5)
    confirmation_level: int = Field(default=4, ge=0, le=5)
    max_log_lines: int = Field(default=2000, ge=1)
    memory_namespace: str = "agent"


class TaskSettings(BaseModel):
    """Task engine limits (spec section 18)."""

    default_timeout: int = Field(default=1800, ge=1)
    max_parallel: int = Field(default=8, ge=1, le=256)
    max_retries: int = Field(default=1, ge=0, le=10)
    persist: bool = True
    recover_on_startup: bool = True
    max_steps: int = Field(default=100, ge=1)
    cycle_detection: bool = True


class TerminalSettings(BaseModel):
    """Terminal tool configuration (spec sections 14, 31).

    An empty allow-list means every shell invocation is denied.
    """

    allowed_commands: list[str] = Field(default_factory=list)
    timeout: int = Field(default=60, ge=1)
    max_output_bytes: int = Field(default=1048576, ge=1024)
    denied_patterns: list[str] = Field(default_factory=lambda: ["rm -rf /", "mkfs", "shutdown"])

    @field_validator("allowed_commands", "denied_patterns", mode="before")
    @classmethod
    def _split_list(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


class PythonToolSettings(BaseModel):
    """Sandboxed Python execution settings."""

    timeout: int = Field(default=60, ge=1)
    memory_limit_mb: int = Field(default=512, ge=64)


class WebSettings(BaseModel):
    """Web tool settings, including the SSRF guard (spec section 31)."""

    timeout: int = Field(default=30, ge=1)
    max_response_bytes: int = Field(default=5242880, ge=1024)
    user_agent: str = "ULTRON/0.1 (+https://localhost)"
    search_provider: Literal["duckduckgo", "searxng", "none"] = "duckduckgo"
    search_url: str = ""
    allow_private_networks: bool = False


class BrowserSettings(BaseModel):
    """Playwright browser settings (spec section 11)."""

    enabled: bool = False
    headless: bool = True
    executable_path: str = ""
    timeout: int = Field(default=30, ge=1)
    max_sessions: int = Field(default=4, ge=1, le=64)
    screenshot_dir: str = "./data/screenshots"
    download_dir: str = "./data/downloads"
    persist_profile: bool = False
    block_media: bool = True


class ComputerNodeSettings(BaseModel):
    """Computer node gateway settings (spec sections 13, 42)."""

    enabled: bool = True
    token_ttl: int = Field(default=3600, ge=60)
    heartbeat: int = Field(default=15, ge=1)
    offline_after: int = Field(default=45, ge=1)
    capability_priority: list[str] = Field(
        default_factory=lambda: [
            "application_api",
            "dom",
            "ui_automation",
            "keyboard_shortcut",
            "mouse",
            "vision",
        ]
    )
    default_permission_level: int = Field(default=4, ge=0, le=5)

    @field_validator("capability_priority", mode="before")
    @classmethod
    def _split_priority(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


class DeviceSettings(BaseModel):
    """Device gateway settings, including ESP32 (spec section 26)."""

    enabled: bool = True
    heartbeat_timeout: int = Field(default=60, ge=1)
    max_connections: int = Field(default=32, ge=1, le=512)
    esp32_protocol_version: int = Field(default=1, ge=1)
    require_auth: bool = True
    mqtt_enabled: bool = False
    mqtt_broker_host: str = "localhost"
    mqtt_broker_port: int = Field(default=1883, ge=1, le=65535)
    mqtt_username: str = ""
    mqtt_password: SecretStr = SecretStr("")
    mqtt_client_id: str = "ultron"
    mqtt_topic_prefix: str = "ultron"


class VoiceSettings(BaseModel):
    """Voice subsystem settings (spec section 25)."""

    enabled: bool = False
    stt_engine: Literal["none", "faster-whisper"] = "none"
    stt_model: str = "base"
    stt_device: str = "cpu"
    stt_compute_type: str = "int8"
    stt_language: str = "en"
    stt_timeout: int = Field(default=60, ge=1)
    stt_partial_results: bool = True
    tts_engine: Literal["none", "piper"] = "none"
    tts_model_path: str = ""
    tts_binary_path: str = "piper"
    tts_sample_rate: int = Field(default=22050, ge=8000)
    tts_timeout: int = Field(default=60, ge=1)
    interruption_enabled: bool = True
    session_ttl: int = Field(default=300, ge=10)
    wake_words: list[str] = Field(default_factory=lambda: ["ultron"])

    @field_validator("wake_words", mode="before")
    @classmethod
    def _split_wake_words(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


class SchedulerSettings(BaseModel):
    """Scheduler settings (spec section 44)."""

    enabled: bool = True
    timezone: str = "UTC"
    max_instances: int = Field(default=3, ge=1, le=64)
    misfire_grace: int = Field(default=300, ge=0)
    job_store: Literal["memory", "database"] = "database"


class NotificationSettings(BaseModel):
    """Notification settings (spec section 45)."""

    enabled: bool = True
    default_provider: str = "log"
    webhook_url: str = ""
    webhook_timeout: int = Field(default=15, ge=1)
    log_level: LogLevel = "INFO"

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_level(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.upper()
        return value


class MemorySettings(BaseModel):
    """Memory subsystem settings (spec section 22)."""

    enabled: bool = True
    short_term_max_messages: int = Field(default=40, ge=1)
    short_term_ttl: int = Field(default=86400, ge=60)
    retrieval_top_k: int = Field(default=8, ge=1, le=200)
    min_similarity: float = Field(default=0.25, ge=0.0, le=1.0)
    episodic_retention_days: int = Field(default=180, ge=1)
    long_term_enabled: bool = True
    semantic_enabled: bool = True
    default_namespace: str = "user"


class ObservabilitySettings(BaseModel):
    """Metrics, monitoring and health settings (spec section 32)."""

    metrics_enabled: bool = True
    metrics_path: str = "/metrics"
    resource_monitor_enabled: bool = True
    resource_monitor_interval: int = Field(default=30, ge=1)
    cpu_high_percent: float = Field(default=85.0, ge=0.0, le=100.0)
    ram_high_percent: float = Field(default=90.0, ge=0.0, le=100.0)
    disk_low_percent: float = Field(default=10.0, ge=0.0, le=100.0)
    gpu_high_percent: float = Field(default=90.0, ge=0.0, le=100.0)
    events_persist: bool = True
    event_retention_days: int = Field(default=30, ge=1)
    audit_log_retention_days: int = Field(default=180, ge=1)
    healthcheck_cache_ttl: int = Field(default=5, ge=0)


class Settings(BaseSettings):
    """The complete ULTRON configuration.

    Access the slices through the properties below rather than reading flat
    keys, so that a subsystem can depend on exactly the section it needs.
    """

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env", "../../.env"),
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
        validate_assignment=True,
    )

    # Flat environment variables, grouped into nested sections. Pydantic
    # Settings maps names such as API_PORT onto these fields.
    app_name: Annotated[str, Field(alias="APP_NAME")] = "ULTRON"
    environment: Annotated[Environment, Field(alias="ENVIRONMENT")] = "development"
    log_level: Annotated[LogLevel, Field(alias="LOG_LEVEL")] = "INFO"
    log_format: Annotated[Literal["json", "console"], Field(alias="LOG_FORMAT")] = "json"
    log_file: Annotated[str, Field(alias="LOG_FILE")] = ""
    metrics_include_in_log: Annotated[bool, Field(alias="METRICS_INCLUDE_IN_LOG")] = False

    # Binds every interface by default so a LAN client can reach the server.
    api_host: Annotated[str, Field(alias="API_HOST")] = "0.0.0.0"
    api_port: Annotated[int, Field(alias="API_PORT", ge=1, le=65535)] = 8000
    api_workers: Annotated[int, Field(alias="API_WORKERS", ge=1, le=64)] = 1
    api_reload: Annotated[bool, Field(alias="API_RELOAD")] = False
    api_root_path: Annotated[str, Field(alias="API_ROOT_PATH")] = ""
    debug: Annotated[bool, Field(alias="DEBUG")] = False
    shutdown_timeout: Annotated[int, Field(alias="SHUTDOWN_TIMEOUT")] = 30
    cors_origins: Annotated[str, Field(alias="CORS_ORIGINS")] = ""

    jwt_secret: Annotated[SecretStr, Field(alias="JWT_SECRET")] = SecretStr(
        "CHANGE_ME_generate_a_random_secret"
    )
    jwt_algorithm: Annotated[str, Field(alias="JWT_ALGORITHM")] = "HS256"
    access_token_expire_minutes: Annotated[int, Field(alias="ACCESS_TOKEN_EXPIRE_MINUTES")] = 60
    refresh_token_expire_days: Annotated[int, Field(alias="REFRESH_TOKEN_EXPIRE_DAYS")] = 30
    admin_username: Annotated[str, Field(alias="ADMIN_USERNAME")] = "admin"
    admin_password: Annotated[SecretStr, Field(alias="ADMIN_PASSWORD")] = SecretStr("CHANGE_ME")
    argon2_time_cost: Annotated[int, Field(alias="ARGON2_TIME_COST")] = 3
    argon2_memory_cost: Annotated[int, Field(alias="ARGON2_MEMORY_COST")] = 65536
    argon2_parallelism: Annotated[int, Field(alias="ARGON2_PARALLELISM")] = 4
    allow_anonymous: Annotated[bool, Field(alias="ALLOW_ANONYMOUS")] = False
    rate_limit_enabled: Annotated[bool, Field(alias="RATE_LIMIT_ENABLED")] = True
    rate_limit_requests: Annotated[int, Field(alias="RATE_LIMIT_REQUESTS")] = 120

    database_url: Annotated[str, Field(alias="DATABASE_URL")] = (
        "postgresql+asyncpg://ultron:CHANGE_ME@localhost:5432/ultron"
    )
    database_pool_size: Annotated[int, Field(alias="DATABASE_POOL_SIZE")] = 10
    database_max_overflow: Annotated[int, Field(alias="DATABASE_MAX_OVERFLOW")] = 20
    database_pool_timeout: Annotated[int, Field(alias="DATABASE_POOL_TIMEOUT")] = 30
    database_pool_recycle: Annotated[int, Field(alias="DATABASE_POOL_REYCLE")] = 1800
    database_echo: Annotated[bool, Field(alias="DATABASE_ECHO")] = False
    database_auto_migrate: Annotated[bool, Field(alias="DATABASE_AUTO_MIGRATE")] = False
    enable_pgvector: Annotated[bool, Field(alias="ENABLE_PGVECTOR")] = True

    redis_url: Annotated[str, Field(alias="REDIS_URL")] = "redis://localhost:6379/0"
    redis_max_connections: Annotated[int, Field(alias="REDIS_MAX_CONNECTIONS")] = 50
    redis_socket_timeout: Annotated[int, Field(alias="REDIS_SOCKET_TIMEOUT")] = 5
    redis_default_ttl: Annotated[int, Field(alias="REDIS_DEFAULT_TTL")] = 3600
    redis_event_bridge: Annotated[bool, Field(alias="REDIS_EVENT_BRIDGE")] = False

    ollama_url: Annotated[str, Field(alias="OLLAMA_URL")] = "http://localhost:11434"
    ollama_api_key: Annotated[SecretStr, Field(alias="OLLAMA_API_KEY")] = SecretStr("")
    ollama_timeout: Annotated[int, Field(alias="OLLAMA_TIMEOUT")] = 120
    ollama_connect_timeout: Annotated[int, Field(alias="OLLAMA_CONNECT_TIMEOUT")] = 10
    ollama_keepalive: Annotated[str, Field(alias="OLLAMA_KEEPALIVE")] = "5m"
    ollama_num_ctx: Annotated[int, Field(alias="OLLAMA_NUM_CTX")] = 4096
    ollama_models: Annotated[str, Field(alias="OLLAMA_MODELS")] = ""
    ollama_embedding_model: Annotated[str, Field(alias="OLLAMA_EMBEDDING_MODEL")] = ""
    ollama_healthcheck: Annotated[bool, Field(alias="OLLAMA_HEALTHCHECK")] = True

    openai_api_key: Annotated[SecretStr, Field(alias="OPENAI_API_KEY")] = SecretStr("")
    openai_base_url: Annotated[str, Field(alias="OPENAI_BASE_URL")] = "https://api.openai.com/v1"
    openai_timeout: Annotated[int, Field(alias="OPENAI_TIMEOUT")] = 60
    gemini_api_key: Annotated[SecretStr, Field(alias="GEMINI_API_KEY")] = SecretStr("")
    gemini_base_url: Annotated[str, Field(alias="GEMINI_BASE_URL")] = (
        "https://generativelanguage.googleapis.com"
    )
    gemini_timeout: Annotated[int, Field(alias="GEMINI_TIMEOUT")] = 60
    anthropic_api_key: Annotated[SecretStr, Field(alias="ANTHROPIC_API_KEY")] = SecretStr("")
    anthropic_base_url: Annotated[str, Field(alias="ANTHROPIC_BASE_URL")] = (
        "https://api.anthropic.com"
    )
    anthropic_timeout: Annotated[int, Field(alias="ANTHROPIC_TIMEOUT")] = 60

    model_default: Annotated[str, Field(alias="MODEL_DEFAULT")] = "ollama:qwen2.5:7b-instruct"
    model_coding: Annotated[str, Field(alias="MODEL_CODING")] = ""
    model_research: Annotated[str, Field(alias="MODEL_RESEARCH")] = ""
    model_vision: Annotated[str, Field(alias="MODEL_VISION")] = ""
    model_fast: Annotated[str, Field(alias="MODEL_FAST")] = ""
    model_reasoning: Annotated[str, Field(alias="MODEL_REASONING")] = ""
    model_fallback_chain: Annotated[str, Field(alias="MODEL_FALLBACK_CHAIN")] = ""
    model_max_output_tokens: Annotated[int, Field(alias="MODEL_MAX_OUTPUT_TOKENS")] = 4096
    model_temperature: Annotated[float, Field(alias="MODEL_TEMPERATURE")] = 0.7
    model_request_timeout: Annotated[int, Field(alias="MODEL_REQUEST_TIMEOUT")] = 180
    model_max_retries: Annotated[int, Field(alias="MODEL_MAX_RETRIES")] = 2
    model_retry_backoff: Annotated[float, Field(alias="MODEL_RETRY_BACKOFF")] = 2.0
    model_usage_tracking: Annotated[bool, Field(alias="MODEL_USAGE_TRACKING")] = True
    model_health_refresh: Annotated[int, Field(alias="MODEL_HEALTH_REFRESH")] = 60

    embedding_provider: Annotated[
        Literal["ollama", "openai", "none"], Field(alias="EMBEDDING_PROVIDER")
    ] = "ollama"
    embedding_model: Annotated[str, Field(alias="EMBEDDING_MODEL")] = ""
    embedding_dimensions: Annotated[int, Field(alias="EMBEDDING_DIMENSIONS")] = 768
    embedding_timeout: Annotated[int, Field(alias="EMBEDDING_TIMEOUT")] = 30
    embedding_allow_hash_fallback: Annotated[bool, Field(alias="EMBEDDING_ALLOW_HASH_FALLBACK")] = (
        False
    )

    opencode_path: Annotated[str, Field(alias="OPENCODE_PATH")] = "opencode"
    opencode_model: Annotated[str, Field(alias="OPENCODE_MODEL")] = ""
    opencode_provider: Annotated[str, Field(alias="OPENCODE_PROVIDER")] = ""
    opencode_timeout: Annotated[int, Field(alias="OPENCODE_TIMEOUT")] = 1800
    opencode_max_output_lines: Annotated[int, Field(alias="OPENCODE_MAX_OUTPUT_LINES")] = 5000
    opencode_extra_args: Annotated[str, Field(alias="OPENCODE_EXTRA_ARGS")] = ""
    opencode_workspace_template: Annotated[str, Field(alias="OPENCODE_WORKSPACE_TEMPLATE")] = ""
    opencode_git_isolation: Annotated[bool, Field(alias="OPENCODE_GIT_ISOLATION")] = True
    opencode_git_branch_prefix: Annotated[str, Field(alias="OPENCODE_GIT_BRANCH_PREFIX")] = "ultron"

    workspaces_root: Annotated[str, Field(alias="WORKSPACES_ROOT")] = "./workspaces"
    workspaces_allowed_roots: Annotated[str, Field(alias="WORKSPACES_ALLOWED_ROOTS")] = ""
    workspaces_cleanup_on_task_end: Annotated[
        bool, Field(alias="WORKSPACES_CLEANUP_ON_TASK_END")
    ] = False
    workspaces_max_size_mb: Annotated[int, Field(alias="WORKSPACES_MAX_SIZE_MB")] = 5120

    agent_default_timeout: Annotated[int, Field(alias="AGENT_DEFAULT_TIMEOUT")] = 900
    agent_max_concurrency: Annotated[int, Field(alias="AGENT_MAX_CONCURRENCY")] = 4
    agent_max_per_type: Annotated[int, Field(alias="AGENT_MAX_PER_TYPE")] = 8
    agent_heartbeat_interval: Annotated[int, Field(alias="AGENT_HEARTBEAT_INTERVAL")] = 15
    agent_default_permission_level: Annotated[
        int, Field(alias="AGENT_DEFAULT_PERMISSION_LEVEL")
    ] = 1
    agent_confirmation_level: Annotated[int, Field(alias="AGENT_CONFIRMATION_LEVEL")] = 4
    agent_max_log_lines: Annotated[int, Field(alias="AGENT_MAX_LOG_LINES")] = 2000
    agent_memory_namespace: Annotated[str, Field(alias="AGENT_MEMORY_NAMESPACE")] = "agent"

    task_default_timeout: Annotated[int, Field(alias="TASK_DEFAULT_TIMEOUT")] = 1800
    task_max_parallel: Annotated[int, Field(alias="TASK_MAX_PARALLEL")] = 8
    task_max_retries: Annotated[int, Field(alias="TASK_MAX_RETRIES")] = 1
    task_persist: Annotated[bool, Field(alias="TASK_PERSIST")] = True
    task_recover_on_startup: Annotated[bool, Field(alias="TASK_RECOVER_ON_STARTUP")] = True
    task_max_steps: Annotated[int, Field(alias="TASK_MAX_STEPS")] = 100
    task_graph_cycle_detection: Annotated[bool, Field(alias="TASK_GRAPH_CYCLE_DETECTION")] = True

    terminal_allowed_commands: Annotated[str, Field(alias="TERMINAL_ALLOWED_COMMANDS")] = ""
    terminal_timeout: Annotated[int, Field(alias="TERMINAL_TIMEOUT")] = 60
    terminal_max_output_bytes: Annotated[int, Field(alias="TERMINAL_MAX_OUTPUT_BYTES")] = 1048576
    terminal_denied_patterns: Annotated[str, Field(alias="TERMINAL_DENIED_PATTERNS")] = (
        "rm -rf /,mkfs,shutdown,reboot,dd if="
    )

    python_timeout: Annotated[int, Field(alias="PYTHON_TIMEOUT")] = 60
    python_memory_limit_mb: Annotated[int, Field(alias="PYTHON_MEMORY_LIMIT_MB")] = 512

    web_timeout: Annotated[int, Field(alias="WEB_TIMEOUT")] = 30
    web_max_response_bytes: Annotated[int, Field(alias="WEB_MAX_RESPONSE_BYTES")] = 5242880
    web_user_agent: Annotated[str, Field(alias="WEB_USER_AGENT")] = (
        "ULTRON/0.1 (+https://localhost)"
    )
    web_search_provider: Annotated[
        Literal["duckduckgo", "searxng", "none"], Field(alias="WEB_SEARCH_PROVIDER")
    ] = "duckduckgo"
    web_search_url: Annotated[str, Field(alias="WEB_SEARCH_URL")] = ""
    web_allow_private_networks: Annotated[bool, Field(alias="WEB_ALLOW_PRIVATE_NETWORKS")] = False

    browser_enabled: Annotated[bool, Field(alias="BROWSER_ENABLED")] = False
    browser_headless: Annotated[bool, Field(alias="BROWSER_HEADLESS")] = True
    browser_executable_path: Annotated[str, Field(alias="BROWSER_EXECUTABLE_PATH")] = ""
    browser_timeout: Annotated[int, Field(alias="BROWSER_TIMEOUT")] = 30
    browser_max_sessions: Annotated[int, Field(alias="BROWSER_MAX_SESSIONS")] = 4
    browser_screenshot_dir: Annotated[str, Field(alias="BROWSER_SCREENSHOT_DIR")] = (
        "./data/screenshots"
    )
    browser_download_dir: Annotated[str, Field(alias="BROWSER_DOWNLOAD_DIR")] = "./data/downloads"
    browser_persist_profile: Annotated[bool, Field(alias="BROWSER_PERSIST_PROFILE")] = False
    browser_block_media: Annotated[bool, Field(alias="BROWSER_BLOCK_MEDIA")] = True

    computer_nodes_enabled: Annotated[bool, Field(alias="COMPUTER_NODES_ENABLED")] = True
    computer_node_token_ttl: Annotated[int, Field(alias="COMPUTER_NODE_TOKEN_TTL")] = 3600
    computer_node_heartbeat: Annotated[int, Field(alias="COMPUTER_NODE_HEARTBEAT")] = 15
    computer_node_offline_after: Annotated[int, Field(alias="COMPUTER_NODE_OFFLINE_AFTER")] = 45
    computer_capability_priority: Annotated[str, Field(alias="COMPUTER_CAPABILITY_PRIORITY")] = (
        "application_api,dom,ui_automation,keyboard_shortcut,mouse,vision"
    )
    computer_node_default_permission_level: Annotated[
        int, Field(alias="COMPUTER_NODE_DEFAULT_PERMISSION_LEVEL")
    ] = 4

    devices_enabled: Annotated[bool, Field(alias="DEVICES_ENABLED")] = True
    devices_heartbeat_timeout: Annotated[int, Field(alias="DEVICES_HEARTBEAT_TIMEOUT")] = 60
    devices_max_connections: Annotated[int, Field(alias="DEVICES_MAX_CONNECTIONS")] = 32
    esp32_protocol_version: Annotated[int, Field(alias="ESP32_PROTOCOL_VERSION")] = 1
    esp32_require_auth: Annotated[bool, Field(alias="ESP32_REQUIRE_AUTH")] = True
    mqtt_enabled: Annotated[bool, Field(alias="MQTT_ENABLED")] = False
    mqtt_broker_host: Annotated[str, Field(alias="MQTT_BROKER_HOST")] = "localhost"
    mqtt_broker_port: Annotated[int, Field(alias="MQTT_BROKER_PORT")] = 1883
    mqtt_username: Annotated[str, Field(alias="MQTT_USERNAME")] = ""
    mqtt_password: Annotated[SecretStr, Field(alias="MQTT_PASSWORD")] = SecretStr("")
    mqtt_client_id: Annotated[str, Field(alias="MQTT_CLIENT_ID")] = "ultron"
    mqtt_topic_prefix: Annotated[str, Field(alias="MQTT_TOPIC_PREFIX")] = "ultron"

    voice_enabled: Annotated[bool, Field(alias="VOICE_ENABLED")] = False
    stt_engine: Annotated[Literal["none", "faster-whisper"], Field(alias="STT_ENGINE")] = "none"
    stt_model: Annotated[str, Field(alias="STT_MODEL")] = "base"
    stt_device: Annotated[str, Field(alias="STT_DEVICE")] = "cpu"
    stt_compute_type: Annotated[str, Field(alias="STT_COMPUTE_TYPE")] = "int8"
    stt_language: Annotated[str, Field(alias="STT_LANGUAGE")] = "en"
    stt_timeout: Annotated[int, Field(alias="STT_TIMEOUT")] = 60
    stt_partial_results: Annotated[bool, Field(alias="STT_PARTIAL_RESULTS")] = True
    tts_engine: Annotated[Literal["none", "piper"], Field(alias="TTS_ENGINE")] = "none"
    tts_model_path: Annotated[str, Field(alias="TTS_MODEL_PATH")] = ""
    tts_binary_path: Annotated[str, Field(alias="TTS_BINARY_PATH")] = "piper"
    tts_sample_rate: Annotated[int, Field(alias="TTS_SAMPLE_RATE")] = 22050
    tts_timeout: Annotated[int, Field(alias="TTS_TIMEOUT")] = 60
    voice_interruption_enabled: Annotated[bool, Field(alias="VOICE_INTERRUPTION_ENABLED")] = True
    voice_session_ttl: Annotated[int, Field(alias="VOICE_SESSION_TTL")] = 300
    wake_words: Annotated[str, Field(alias="WAKE_WORDS")] = "ultron,hey ultron"

    scheduler_enabled: Annotated[bool, Field(alias="SCHEDULER_ENABLED")] = True
    scheduler_timezone: Annotated[str, Field(alias="SCHEDULER_TIMEZONE")] = "UTC"
    scheduler_max_instances: Annotated[int, Field(alias="SCHEDULER_MAX_INSTANCES")] = 3
    scheduler_misfire_grace: Annotated[int, Field(alias="SCHEDULER_MISFIRE_GRACE")] = 300
    scheduler_job_store: Annotated[
        Literal["memory", "database"], Field(alias="SCHEDULER_JOB_STORE")
    ] = "database"

    notification_enabled: Annotated[bool, Field(alias="NOTIFICATION_ENABLED")] = True
    notification_default_provider: Annotated[str, Field(alias="NOTIFICATION_DEFAULT_PROVIDER")] = (
        "log"
    )
    notification_webhook_url: Annotated[str, Field(alias="NOTIFICATION_WEBHOOK_URL")] = ""
    notification_webhook_timeout: Annotated[int, Field(alias="NOTIFICATION_WEBHOOK_TIMEOUT")] = 15
    notification_log_level: Annotated[LogLevel, Field(alias="NOTIFICATION_LOG_LEVEL")] = "INFO"

    memory_enabled: Annotated[bool, Field(alias="MEMORY_ENABLED")] = True
    memory_short_term_max_messages: Annotated[
        int, Field(alias="MEMORY_SHORT_TERM_MAX_MESSAGES")
    ] = 40
    memory_short_term_ttl: Annotated[int, Field(alias="MEMORY_SHORT_TERM_TTL")] = 86400
    memory_retrieval_top_k: Annotated[int, Field(alias="MEMORY_RETRIEVAL_TOP_K")] = 8
    memory_min_similarity: Annotated[float, Field(alias="MEMORY_MIN_SIMILARITY")] = 0.25
    memory_episodic_retention_days: Annotated[
        int, Field(alias="MEMORY_EPISODIC_RETENTION_DAYS")
    ] = 180
    memory_long_term_enabled: Annotated[bool, Field(alias="MEMORY_LONG_TERM_ENABLED")] = True
    memory_semantic_enabled: Annotated[bool, Field(alias="MEMORY_SEMANTIC_ENABLED")] = True
    memory_default_namespace: Annotated[str, Field(alias="MEMORY_DEFAULT_NAMESPACE")] = "user"

    metrics_enabled: Annotated[bool, Field(alias="METRICS_ENABLED")] = True
    metrics_path: Annotated[str, Field(alias="METRICS_PATH")] = "/metrics"
    resource_monitor_enabled: Annotated[bool, Field(alias="RESOURCE_MONITOR_ENABLED")] = True
    resource_monitor_interval: Annotated[int, Field(alias="RESOURCE_MONITOR_INTERVAL")] = 30
    cpu_high_percent: Annotated[float, Field(alias="CPU_HIGH_PERCENT")] = 85.0
    ram_high_percent: Annotated[float, Field(alias="RAM_HIGH_PERCENT")] = 90.0
    disk_low_percent: Annotated[float, Field(alias="DISK_LOW_PERCENT")] = 10.0
    gpu_high_percent: Annotated[float, Field(alias="GPU_HIGH_PERCENT")] = 90.0
    events_persist: Annotated[bool, Field(alias="EVENTS_PERSIST")] = True
    event_retention_days: Annotated[int, Field(alias="EVENT_RETENTION_DAYS")] = 30
    audit_log_retention_days: Annotated[int, Field(alias="AUDIT_LOG_RETENTION_DAYS")] = 180
    healthcheck_cache_ttl: Annotated[int, Field(alias="HEALTHCHECK_CACHE_TTL")] = 5

    @field_validator("log_level", "notification_log_level", mode="before")
    @classmethod
    def _normalise_log_level(cls, value: Any) -> Any:
        """Accept ``LOG_LEVEL=debug`` as readily as ``LOG_LEVEL=DEBUG``."""
        if isinstance(value, str):
            return value.strip().upper()
        return value

    @model_validator(mode="after")
    def _validate_sections(self) -> Settings:
        """Apply every section's constraints as soon as settings are built.

        The flat fields exist only to receive environment variables; the range
        and vocabulary rules live on the section models, which are the single
        source of truth. Building each section here means an out-of-range value
        is rejected at construction, with the offending field named, instead of
        being accepted and discovered much later at first use.
        """
        self.sections()
        return self

    def sections(self) -> tuple[BaseModel, ...]:
        """Every configuration section, built from the flat fields.

        A subsystem should depend on the one section it needs, but a diagnostic
        dump or a health report needs all of them.
        """
        return (
            self.app,
            self.logging,
            self.security,
            self.database,
            self.redis,
            self.ollama,
            self.providers,
            self.model_router,
            self.embeddings,
            self.opencode,
            self.workspaces,
            self.agents,
            self.tasks,
            self.terminal,
            self.python_tool,
            self.web,
            self.browser,
            self.computer_nodes,
            self.devices,
            self.voice,
            self.scheduler,
            self.notifications,
            self.memory,
            self.observability,
        )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Resolve the ``.env`` file explicitly, including ``$ULTRON_ENV_FILE``.

        ``$ULTRON_ENV_FILE`` wins outright so tests and the deployment scripts
        can point at an alternative file without mutating the working directory.
        """
        explicit = os.environ.get("ULTRON_ENV_FILE")
        sources: list[PydanticBaseSettingsSource] = [init_settings, env_settings]

        if explicit:
            sources.append(_file_source(settings_cls, Path(explicit)))
        else:
            sources.append(dotenv_settings)

        sources.append(file_secret_settings)
        return tuple(sources)

    def startup_warnings(self) -> list[str]:
        """Configuration problems worth surfacing at boot.

        These are warnings, not failures. ULTRON must boot and then report a
        degraded or unhealthy state rather than refusing to start, so that a
        misconfigured deployment is still observable through ``/health``.
        """
        warnings: list[str] = []
        security = self.security
        router = self.model_router
        providers = self.providers

        if self.environment == "production":
            if security.jwt_placeholder:
                warnings.append(
                    "JWT_SECRET still holds its placeholder value. Generate a "
                    "real secret before serving production traffic."
                )
            if security.admin_password_placeholder:
                warnings.append(
                    "ADMIN_PASSWORD still holds its placeholder value. The "
                    "bootstrap administrator cannot be created as configured."
                )
        if security.allow_anonymous:
            warnings.append("ALLOW_ANONYMOUS is enabled; the API accepts no token.")
        if not self.database.url.startswith("postgresql"):
            warnings.append(
                "DATABASE_URL is not PostgreSQL. PostgreSQL is the authoritative "
                "store for ULTRON; the server cannot operate correctly without it."
            )
        if router.fallback_chain:
            warnings.append(
                "MODEL_FALLBACK_CHAIN is set; requests will fail over to "
                f"{', '.join(router.fallback_chain)} when a provider is unavailable."
            )

        # Only warn about a provider that is referenced but has no credentials.
        # Ollama needs no credential, and whether it is actually running is
        # answered by the health check rather than by configuration.
        provider = router.default.split(":", 1)[0].strip().lower()
        credentialed = {
            "openai": providers.openai_configured,
            "gemini": providers.gemini_configured,
            "anthropic": providers.anthropic_configured,
        }
        if provider in credentialed and not credentialed[provider]:
            warnings.append(
                f"MODEL_DEFAULT targets provider '{provider}' but no API key is "
                "configured. Model requests will fail with PROVIDER_NOT_CONFIGURED."
            )
        return warnings

    # -- Section views ----------------------------------------------------

    @property
    def app(self) -> AppSettings:
        return AppSettings(
            name=self.app_name,
            environment=self.environment,
            debug=self.debug,
            host=self.api_host,
            port=self.api_port,
            workers=self.api_workers,
            reload=self.api_reload,
            root_path=self.api_root_path,
            shutdown_timeout=self.shutdown_timeout,
            cors_origins=self.cors_origins,
        )

    @property
    def logging(self) -> LoggingSettings:
        return LoggingSettings(
            level=self.log_level,
            format=self.log_format,
            file=self.log_file,
            include_metrics_in_log=self.metrics_include_in_log,
        )

    @property
    def security(self) -> SecuritySettings:
        return SecuritySettings(
            jwt_secret=self.jwt_secret,
            jwt_algorithm=self.jwt_algorithm,
            access_token_expire_minutes=self.access_token_expire_minutes,
            refresh_token_expire_days=self.refresh_token_expire_days,
            admin_username=self.admin_username,
            admin_password=self.admin_password,
            argon2_time_cost=self.argon2_time_cost,
            argon2_memory_cost=self.argon2_memory_cost,
            argon2_parallelism=self.argon2_parallelism,
            allow_anonymous=self.allow_anonymous,
            rate_limit_enabled=self.rate_limit_enabled,
            rate_limit_requests=self.rate_limit_requests,
        )

    @property
    def database(self) -> DatabaseSettings:
        return DatabaseSettings(
            url=self.database_url,
            pool_size=self.database_pool_size,
            max_overflow=self.database_max_overflow,
            pool_timeout=self.database_pool_timeout,
            pool_recycle=self.database_pool_recycle,
            echo=self.database_echo,
            auto_migrate=self.database_auto_migrate,
            enable_pgvector=self.enable_pgvector,
        )

    @property
    def redis(self) -> RedisSettings:
        return RedisSettings(
            url=self.redis_url,
            max_connections=self.redis_max_connections,
            socket_timeout=self.redis_socket_timeout,
            default_ttl=self.redis_default_ttl,
            event_bridge=self.redis_event_bridge,
        )

    @property
    def ollama(self) -> OllamaSettings:
        return OllamaSettings(
            url=self.ollama_url,
            api_key=self.ollama_api_key,
            timeout=self.ollama_timeout,
            connect_timeout=self.ollama_connect_timeout,
            keepalive=self.ollama_keepalive,
            num_ctx=self.ollama_num_ctx,
            models=self.ollama_models,
            embedding_model=self.ollama_embedding_model,
            healthcheck=self.ollama_healthcheck,
        )

    @property
    def providers(self) -> ProviderSettings:
        return ProviderSettings(
            openai_api_key=self.openai_api_key,
            openai_base_url=self.openai_base_url,
            openai_timeout=self.openai_timeout,
            gemini_api_key=self.gemini_api_key,
            gemini_base_url=self.gemini_base_url,
            gemini_timeout=self.gemini_timeout,
            anthropic_api_key=self.anthropic_api_key,
            anthropic_base_url=self.anthropic_base_url,
            anthropic_timeout=self.anthropic_timeout,
        )

    @property
    def model_router(self) -> ModelRouterSettings:
        return ModelRouterSettings(
            default=self.model_default,
            coding=self.model_coding,
            research=self.model_research,
            vision=self.model_vision,
            fast=self.model_fast,
            reasoning=self.model_reasoning,
            fallback_chain=self.model_fallback_chain,
            max_output_tokens=self.model_max_output_tokens,
            temperature=self.model_temperature,
            request_timeout=self.model_request_timeout,
            max_retries=self.model_max_retries,
            retry_backoff=self.model_retry_backoff,
            usage_tracking=self.model_usage_tracking,
            health_refresh=self.model_health_refresh,
        )

    @property
    def embeddings(self) -> EmbeddingSettings:
        return EmbeddingSettings(
            provider=self.embedding_provider,
            model=self.embedding_model,
            dimensions=self.embedding_dimensions,
            timeout=self.embedding_timeout,
            allow_hash_fallback=self.embedding_allow_hash_fallback,
        )

    @property
    def opencode(self) -> OpenCodeSettings:
        return OpenCodeSettings(
            path=self.opencode_path,
            model=self.opencode_model,
            provider=self.opencode_provider,
            timeout=self.opencode_timeout,
            max_output_lines=self.opencode_max_output_lines,
            extra_args=self.opencode_extra_args,
            workspace_template=self.opencode_workspace_template,
            git_isolation=self.opencode_git_isolation,
            git_branch_prefix=self.opencode_git_branch_prefix,
        )

    @property
    def workspaces(self) -> WorkspaceSettings:
        return WorkspaceSettings(
            root=self.workspaces_root,
            allowed_roots=self.workspaces_allowed_roots,
            cleanup_on_task_end=self.workspaces_cleanup_on_task_end,
            max_size_mb=self.workspaces_max_size_mb,
        )

    @property
    def agents(self) -> AgentSettings:
        return AgentSettings(
            default_timeout=self.agent_default_timeout,
            max_concurrency=self.agent_max_concurrency,
            max_per_type=self.agent_max_per_type,
            heartbeat_interval=self.agent_heartbeat_interval,
            default_permission_level=self.agent_default_permission_level,
            confirmation_level=self.agent_confirmation_level,
            max_log_lines=self.agent_max_log_lines,
            memory_namespace=self.agent_memory_namespace,
        )

    @property
    def tasks(self) -> TaskSettings:
        return TaskSettings(
            default_timeout=self.task_default_timeout,
            max_parallel=self.task_max_parallel,
            max_retries=self.task_max_retries,
            persist=self.task_persist,
            recover_on_startup=self.task_recover_on_startup,
            max_steps=self.task_max_steps,
            cycle_detection=self.task_graph_cycle_detection,
        )

    @property
    def terminal(self) -> TerminalSettings:
        return TerminalSettings(
            allowed_commands=self.terminal_allowed_commands,
            timeout=self.terminal_timeout,
            max_output_bytes=self.terminal_max_output_bytes,
            denied_patterns=self.terminal_denied_patterns,
        )

    @property
    def python_tool(self) -> PythonToolSettings:
        return PythonToolSettings(
            timeout=self.python_timeout,
            memory_limit_mb=self.python_memory_limit_mb,
        )

    @property
    def web(self) -> WebSettings:
        return WebSettings(
            timeout=self.web_timeout,
            max_response_bytes=self.web_max_response_bytes,
            user_agent=self.web_user_agent,
            search_provider=self.web_search_provider,
            search_url=self.web_search_url,
            allow_private_networks=self.web_allow_private_networks,
        )

    @property
    def browser(self) -> BrowserSettings:
        return BrowserSettings(
            enabled=self.browser_enabled,
            headless=self.browser_headless,
            executable_path=self.browser_executable_path,
            timeout=self.browser_timeout,
            max_sessions=self.browser_max_sessions,
            screenshot_dir=self.browser_screenshot_dir,
            download_dir=self.browser_download_dir,
            persist_profile=self.browser_persist_profile,
            block_media=self.browser_block_media,
        )

    @property
    def computer_nodes(self) -> ComputerNodeSettings:
        return ComputerNodeSettings(
            enabled=self.computer_nodes_enabled,
            token_ttl=self.computer_node_token_ttl,
            heartbeat=self.computer_node_heartbeat,
            offline_after=self.computer_node_offline_after,
            capability_priority=self.computer_capability_priority,
            default_permission_level=self.computer_node_default_permission_level,
        )

    @property
    def devices(self) -> DeviceSettings:
        return DeviceSettings(
            enabled=self.devices_enabled,
            heartbeat_timeout=self.devices_heartbeat_timeout,
            max_connections=self.devices_max_connections,
            esp32_protocol_version=self.esp32_protocol_version,
            require_auth=self.esp32_require_auth,
            mqtt_enabled=self.mqtt_enabled,
            mqtt_broker_host=self.mqtt_broker_host,
            mqtt_broker_port=self.mqtt_broker_port,
            mqtt_username=self.mqtt_username,
            mqtt_password=self.mqtt_password,
            mqtt_client_id=self.mqtt_client_id,
            mqtt_topic_prefix=self.mqtt_topic_prefix,
        )

    @property
    def voice(self) -> VoiceSettings:
        return VoiceSettings(
            enabled=self.voice_enabled,
            stt_engine=self.stt_engine,
            stt_model=self.stt_model,
            stt_device=self.stt_device,
            stt_compute_type=self.stt_compute_type,
            stt_language=self.stt_language,
            stt_timeout=self.stt_timeout,
            stt_partial_results=self.stt_partial_results,
            tts_engine=self.tts_engine,
            tts_model_path=self.tts_model_path,
            tts_binary_path=self.tts_binary_path,
            tts_sample_rate=self.tts_sample_rate,
            tts_timeout=self.tts_timeout,
            interruption_enabled=self.voice_interruption_enabled,
            session_ttl=self.voice_session_ttl,
            wake_words=self.wake_words,
        )

    @property
    def scheduler(self) -> SchedulerSettings:
        return SchedulerSettings(
            enabled=self.scheduler_enabled,
            timezone=self.scheduler_timezone,
            max_instances=self.scheduler_max_instances,
            misfire_grace=self.scheduler_misfire_grace,
            job_store=self.scheduler_job_store,
        )

    @property
    def notifications(self) -> NotificationSettings:
        return NotificationSettings(
            enabled=self.notification_enabled,
            default_provider=self.notification_default_provider,
            webhook_url=self.notification_webhook_url,
            webhook_timeout=self.notification_webhook_timeout,
            log_level=self.notification_log_level,
        )

    @property
    def memory(self) -> MemorySettings:
        return MemorySettings(
            enabled=self.memory_enabled,
            short_term_max_messages=self.memory_short_term_max_messages,
            short_term_ttl=self.memory_short_term_ttl,
            retrieval_top_k=self.memory_retrieval_top_k,
            min_similarity=self.memory_min_similarity,
            episodic_retention_days=self.memory_episodic_retention_days,
            long_term_enabled=self.memory_long_term_enabled,
            semantic_enabled=self.memory_semantic_enabled,
            default_namespace=self.memory_default_namespace,
        )

    @property
    def observability(self) -> ObservabilitySettings:
        return ObservabilitySettings(
            metrics_enabled=self.metrics_enabled,
            metrics_path=self.metrics_path,
            resource_monitor_enabled=self.resource_monitor_enabled,
            resource_monitor_interval=self.resource_monitor_interval,
            cpu_high_percent=self.cpu_high_percent,
            ram_high_percent=self.ram_high_percent,
            disk_low_percent=self.disk_low_percent,
            gpu_high_percent=self.gpu_high_percent,
            events_persist=self.events_persist,
            event_retention_days=self.event_retention_days,
            audit_log_retention_days=self.audit_log_retention_days,
            healthcheck_cache_ttl=self.healthcheck_cache_ttl,
        )


def _file_source(settings_cls: type[BaseSettings], path: Path) -> PydanticBaseSettingsSource:
    """Return a dotenv settings source for the explicit ``$ULTRON_ENV_FILE``.

    A path that was named explicitly but does not exist is an error rather than
    a silent fallback: an operator who points ULTRON at a file expects that file
    to be read, and quietly starting on defaults hides the mistake.
    """
    resolved = path.expanduser()
    if not resolved.is_file():
        message = (
            f"ULTRON_ENV_FILE points at '{resolved}', which is not a readable file. "
            "Correct the path or unset the variable to use the default .env lookup."
        )
        raise FileNotFoundError(message)
    return DotEnvSettingsSource(settings_cls, env_file=resolved, env_file_encoding="utf-8")


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton.

    Cached so that the whole application observes one configuration. Tests call
    :func:`reload_settings` after mutating the environment.
    """
    return Settings()


def reload_settings() -> Settings:
    """Discard the cached settings and rebuild them from the environment."""
    get_settings.cache_clear()
    return get_settings()


def is_disallowed_host(host: str, *, allow_private: bool = False) -> bool:
    """Return True when a web tool must not contact this host.

    This is the SSRF guard required by spec section 31: a model must not be able
    to reach internal services, the cloud metadata endpoint, or the host's own
    private interfaces through the server's web tools.

    The check fails closed. A name that cannot be resolved is reported as
    disallowed, because a caller that cannot prove a target is public should not
    be handed the connection. ``ALLOW_PRIVATE_NETWORKS=true`` relaxes the rule
    for trusted development networks only.
    """
    try:
        return any(_is_disallowed_address(candidate, allow_private) for candidate in _resolve(host))
    except socket.gaierror:
        return True


def _resolve(host: str) -> list[str]:
    """Return every IP address a hostname resolves to."""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        # typeshed types sockaddr[0] as str | int; in practice it is the address.
        return [str(info[4][0]) for info in socket.getaddrinfo(host, None)]
    return [host]


def _is_disallowed_address(candidate: str, allow_private: bool) -> bool:
    """Return True when a single resolved address must not be contacted."""
    if allow_private:
        return False
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        return True
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )
