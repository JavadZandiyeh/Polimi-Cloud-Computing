"""
EnvironmentService class is the central unique point where we define
and store environment and configuration variable in our service.
That is important because everyone exactly knows where to find
information without creating different version of them.

The variables are divided according to their context, and they can be
read from `os.environ` or by other sources.
"""

from __future__ import annotations

from abc import ABCMeta
from enum import Enum
from pathlib import Path
from threading import Lock
from typing import Any

from pydantic import BaseModel, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

# Local-dev convenience: load orchestrator/.env (one level above app/). Resolved
# relative to this module so it works regardless of the working directory. Under
# docker-compose the file is absent and real environment variables are used instead
# (env vars take precedence over the file, so injected config always wins).
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def _settings_config() -> SettingsConfigDict:
    """Shared settings config: frozen, ignore extras, and read the local .env file."""
    return SettingsConfigDict(
        frozen=True,
        extra="ignore",
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
    )


class EnvironmentInitializationError(Exception):
    """Raised when environment variables fail validation during startup."""


class SingletonMeta(ABCMeta):
    """
    Singleton metaclass that creates a single instance of a class.
    This implementation is thread-safe.
    """

    _instances: dict["SingletonMeta", Any] = {}
    _locks: dict["SingletonMeta", Lock] = {}
    _global_lock: Lock = Lock()

    def __call__(cls, *args, **kwargs):
        with cls._global_lock:
            if cls not in cls._locks:
                cls._locks[cls] = Lock()
        with cls._locks[cls]:
            if cls not in cls._instances:
                instance = super().__call__(*args, **kwargs)
                cls._instances[cls] = instance
        return cls._instances[cls]


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class AppModeEnum(str, Enum):
    A2A = "a2a"
    WEB = "web"


class ProviderEnum(str, Enum):
    OPENAI = "openai"
    BEDROCK = "bedrock"


class ModelEnum(str, Enum):
    GPT_5_4_MINI = "gpt-5.4-mini"
    ANTHROPIC_CLAUDE_SONNET_BEDROCK = "anthropic.claude-3-5-sonnet-20240620-v1:0"


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class AppSettings(BaseSettings):
    """Application runtime settings."""

    model_config = _settings_config()

    public_url: str = "http://localhost:8000"
    app_mode: AppModeEnum = AppModeEnum.WEB


class LlmSettings(BaseSettings):
    """LLM provider, model, and provider credentials."""

    model_config = _settings_config()

    provider: ProviderEnum = ProviderEnum.OPENAI
    model_name: ModelEnum = ModelEnum.GPT_5_4_MINI
    openai_api_key: str = ""
    # Used only when provider == bedrock.
    aws_region: str = "us-east-1"


class DownstreamSettings(BaseSettings):
    """A2A connection settings for the downstream agents the orchestrator drives."""

    model_config = _settings_config()

    # Base A2A URLs of the downstream agents. Local-dev defaults; override with
    # container names (e.g. http://visiting-place-recommender:8000) under docker-compose.
    vpr_url: str = "http://visiting-place-recommender:8000"
    vpc_url: str = "http://visiting-place-clusterer:8000"
    sds_url: str = "http://single-day-plan-scheduler:8000"

    # Shared A2A task configuration. timeout_seconds is the connect timeout;
    # max_wait_seconds bounds how long a single agent task may take (the
    # day-scheduler runs several LLM + tool round-trips, so keep this generous).
    timeout_seconds: float = 15.0
    poll_interval_seconds: float = 1.0
    max_wait_seconds: float = 300.0


class ObservabilitySettings(BaseSettings):
    """Logging and tracing settings."""

    model_config = _settings_config()

    logfire_token: str = ""


class EnvironmentVariables(BaseModel):
    """All environment-backed settings grouped by domain."""

    app: AppSettings
    llm: LlmSettings
    downstream: DownstreamSettings
    observability: ObservabilitySettings


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class EnvironmentService(metaclass=SingletonMeta):
    """
    This service is responsible for creating the environment variables.

    EnvironmentService is a singleton that when initialized saves all
    the variables in the class attribute `_env`.
    """

    _env: EnvironmentVariables

    def __init__(self) -> None:
        """Load and validate environment-backed settings."""

        try:
            self._env = EnvironmentVariables(
                app=AppSettings(),
                llm=LlmSettings(),
                downstream=DownstreamSettings(),
                observability=ObservabilitySettings(),
            )
        except ValidationError as e:
            raise EnvironmentInitializationError from e

        self._setup_logfire()

    def _setup_logfire(self) -> None:
        """Configure Logfire when a token is present."""
        if not self.logfire_token:
            return

        import logfire

        logfire.configure(token=self.logfire_token)

    # --- Application ---

    @property
    def public_url(self) -> str:
        """Return the public URL exposed by this service."""
        return self._env.app.public_url

    @property
    def app_mode(self) -> AppModeEnum:
        """Return the application serving mode (A2A or web UI)."""
        return self._env.app.app_mode

    # --- LLM ---

    @property
    def llm_provider(self) -> ProviderEnum:
        """Return the configured LLM provider."""
        return self._env.llm.provider

    @property
    def llm_model(self) -> ModelEnum:
        """Return the configured LLM model."""
        return self._env.llm.model_name

    @property
    def openai_api_key(self) -> str:
        """Return the OpenAI API key."""
        return self._env.llm.openai_api_key

    @property
    def aws_region(self) -> str:
        """Return the AWS region used for the optional Bedrock provider."""
        return self._env.llm.aws_region

    # --- Downstream agents (A2A) ---

    @property
    def vpr_url(self) -> str:
        """Return the base A2A URL of the visiting-place-recommender agent."""
        return self._env.downstream.vpr_url

    @property
    def vpc_url(self) -> str:
        """Return the base A2A URL of the visiting-place-clusterer agent."""
        return self._env.downstream.vpc_url

    @property
    def sds_url(self) -> str:
        """Return the base A2A URL of the single-day-plan-scheduler agent."""
        return self._env.downstream.sds_url

    @property
    def a2a_timeout_seconds(self) -> float:
        """Return the per-request timeout for downstream A2A calls."""
        return self._env.downstream.timeout_seconds

    @property
    def a2a_poll_interval_seconds(self) -> float:
        """Return the A2A task poll interval for downstream calls."""
        return self._env.downstream.poll_interval_seconds

    @property
    def a2a_max_wait_seconds(self) -> float:
        """Return the maximum wait for a downstream A2A task to complete."""
        return self._env.downstream.max_wait_seconds

    # --- Observability ---

    @property
    def logfire_token(self) -> str:
        """Return the Logfire token, if configured."""
        return self._env.observability.logfire_token


env = EnvironmentService()
