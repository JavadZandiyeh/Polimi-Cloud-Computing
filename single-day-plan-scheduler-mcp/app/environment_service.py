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
from threading import Lock
from typing import Any

from pydantic import BaseModel, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


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
# Settings
# ---------------------------------------------------------------------------


class GoogleSettings(BaseSettings):
    """Google Maps Platform settings used by the routing tools."""

    model_config = SettingsConfigDict(frozen=True, extra="ignore")

    google_maps_api_key: str = ""
    google_routes_timeout_seconds: float = 5.0


class ObservabilitySettings(BaseSettings):
    """Logging and tracing settings."""

    model_config = SettingsConfigDict(frozen=True, extra="ignore")

    logfire_token: str = ""
    # Deployment label shown in the Logfire UI to separate runs (e.g. local, staging,
    # production). Set LOGFIRE_ENVIRONMENT per deployment; defaults to local-dev.
    logfire_environment: str = "local"


class EnvironmentVariables(BaseModel):
    """All environment-backed settings grouped by domain."""

    google: GoogleSettings
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
                google=GoogleSettings(),
                observability=ObservabilitySettings(),
            )
        except ValidationError as e:
            raise EnvironmentInitializationError from e

        self._setup_logfire()

    def _setup_logfire(self) -> None:
        """Configure Logfire and instrument this server's frameworks when a token is present.

        Names this MCP server in the Logfire UI and captures the incoming MCP tool calls
        (route_estimate / place_details) and the outbound Google Routes/Maps HTTP requests,
        so a tool call from the scheduler reads as one labelled, drill-down-able trace.
        """
        import logfire

        # Always configure so manual spans are valid; only export when a token is set.
        logfire.configure(
            token=self.logfire_token or None,
            send_to_logfire="if-token-present",
            service_name="single-day-plan-scheduler-mcp",
            service_version="1.0.0",
            environment=self.logfire_environment,
            console=False,
        )
        logfire.instrument_mcp()
        logfire.instrument_httpx()

    # --- Google ---

    @property
    def google_maps_api_key(self) -> str:
        """Return the Google Maps API key for the Routes API."""
        return self._env.google.google_maps_api_key

    @property
    def google_routes_timeout_seconds(self) -> float:
        """Return the per-request timeout for Google Routes calls."""
        return self._env.google.google_routes_timeout_seconds

    # --- Observability ---

    @property
    def logfire_token(self) -> str:
        """Return the Logfire token, if configured."""
        return self._env.observability.logfire_token

    @property
    def logfire_environment(self) -> str:
        """Return the deployment label used to group traces in the Logfire UI."""
        return self._env.observability.logfire_environment


env = EnvironmentService()
