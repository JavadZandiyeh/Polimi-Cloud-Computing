from __future__ import annotations

import logging
import time
from typing import Annotated, Optional

import httpx
import logfire
from pydantic import Field
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.toolsets import FunctionToolset

from environment_service import env

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 3
_RETRY_DELAYS = (1, 2)  # seconds before attempt 2 and 3


def _serpapi_request(params: dict) -> dict:
    params = {k: v for k, v in params.items() if v is not None}
    params.setdefault("engine", "google_maps")
    params["api_key"] = env.serpapi_api_key
    with httpx.Client(timeout=30) as client:
        response = client.get("https://serpapi.com/search", params=params)
        response.raise_for_status()
        return response.json()


def _safe_query(params: dict) -> dict:
    """Echo the request params for diagnostics, never including the API key."""
    return {k: v for k, v in params.items() if v is not None and k != "api_key"}


def _request_safe(params: dict) -> dict:
    """Call _serpapi_request with retries; return a detailed error dict instead of raising.

    On success returns the raw SerpApi JSON. On failure returns a structured `error`
    object so the caller (and the orchestrator above it) can see exactly what went wrong
    and decide whether re-calling is worthwhile:
        {
          "error": {
            "message": "<human-readable reason>",
            "type": "client_error" | "server_error" | "timeout"
                    | "connection_error" | "unexpected_error",
            "retryable": <bool>,        # false = re-calling with the same input won't help
            "status_code": <int|null>,  # HTTP status when the failure was an HTTP response
            "attempts": <int>,          # how many times we tried before giving up
            "query": { ... }            # the request params (API key stripped) for context
          }
        }
    """
    error: dict = {
        "message": "unknown error",
        "type": "unexpected_error",
        "retryable": False,
        "status_code": None,
        "attempts": 0,
        "query": _safe_query(params),
    }
    for attempt in range(_MAX_ATTEMPTS):
        error["attempts"] = attempt + 1
        try:
            return _serpapi_request(params)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            try:
                message = exc.response.json().get("error", str(exc))
            except Exception:
                message = str(exc)
            client_error = status < 500
            error.update(
                message=message,
                type="client_error" if client_error else "server_error",
                # 4xx (bad query, auth, quota) won't fix itself; 5xx might.
                retryable=not client_error,
                status_code=status,
            )
            if client_error:
                logger.error("SerpApi client error (not retried): %s", message)
                break
            logger.warning(
                "SerpApi server error (attempt %d/%d): %s",
                attempt + 1,
                _MAX_ATTEMPTS,
                message,
            )
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            error.update(
                message=str(exc),
                type=(
                    "timeout"
                    if isinstance(exc, httpx.TimeoutException)
                    else "connection_error"
                ),
                retryable=True,
            )
            logger.warning(
                "SerpApi request failed (attempt %d/%d): %s",
                attempt + 1,
                _MAX_ATTEMPTS,
                error["message"],
            )
        except Exception as exc:
            error.update(message=str(exc), type="unexpected_error", retryable=False)
            logger.error("SerpApi unexpected error: %s", error["message"])
            break
        if attempt < _MAX_ATTEMPTS - 1:
            time.sleep(_RETRY_DELAYS[attempt])

    logger.error("SerpApi request failed after %d attempt(s): %s", error["attempts"], error["message"])
    return {"error": error}


def search_places(
    q: Annotated[
        str, Field(description="Search query, e.g. 'coffee shops' or 'pizza near me'")
    ],
    location: Annotated[
        Optional[str],
        Field(
            description=(
                "Location name to center the search on, e.g. 'Milan, Italy'. "
                "Do not combine with lat/lon."
            )
        ),
    ] = None,
    lat: Annotated[
        Optional[float],
        Field(
            description="GPS latitude. Must be paired with lon. Do not combine with location."
        ),
    ] = None,
    lon: Annotated[
        Optional[float],
        Field(
            description="GPS longitude. Must be paired with lat. Do not combine with location."
        ),
    ] = None,
    z: Annotated[
        Optional[int],
        Field(ge=3, le=30, description="Map zoom level (3-30). Used with lat/lon."),
    ] = None,
    nearby: Annotated[
        Optional[bool],
        Field(
            description="Set to true to force results closer to the location. Omit or leave false otherwise."
        ),
    ] = None,
    hl: Annotated[
        Optional[str], Field(description="Two-letter language code, e.g. 'en', 'fr'.")
    ] = None,
    gl: Annotated[
        Optional[str], Field(description="Two-letter country code, e.g. 'us', 'uk'.")
    ] = None,
    start: Annotated[
        Optional[int],
        Field(ge=0, le=100, description="Pagination offset (0, 20, 40, …, 100)."),
    ] = None,
) -> dict:
    """Search Google Maps for places matching the query and optional location.

    Use either location (name) or lat+lon (coordinates), never both.
    Returns a list of local results, each containing title, address, rating,
    reviews, phone, website, hours, GPS coordinates, and more.
    On failure returns {"error": {...}} — a detailed object with `message`, `type`,
    `retryable`, `status_code`, `attempts`, and the `query` that failed. When
    `retryable` is false, re-calling with the same input will not help.
    """
    # SerpApi's google_maps engine needs a zoom level: baked into `ll` for the
    # coordinate path, sent as the standalone `z` param for the location-name path
    # (it returns "Missing `z` or `m` parameter" when `location` is used without it).
    zoom = z if z is not None else 14
    ll: str | None = None
    resolved_location: str | None = location
    z_param: int | None = None
    if lat is not None and lon is not None:
        ll = f"@{lat},{lon},{zoom}z"
        resolved_location = None
    elif resolved_location is not None:
        z_param = zoom

    with logfire.span("serpapi search {query!r}", query=q) as span:
        span.set_attribute("serpapi.location", resolved_location)
        span.set_attribute("serpapi.coordinates", ll)
        result = _request_safe(
            dict(
                type="search",
                q=q,
                ll=ll,
                location=resolved_location,
                z=z_param,
                nearby=nearby if nearby else None,
                hl=hl,
                gl=gl,
                start=start,
            )
        )
        if "error" in result:
            span.set_attribute("serpapi.ok", False)
        else:
            span.set_attribute("serpapi.ok", True)
            span.set_attribute(
                "serpapi.result_count", len(result.get("local_results") or [])
            )
        return result


def get_place_details(
    place_id: Annotated[
        Optional[str],
        Field(
            description="Unique Google Maps place identifier. Cannot be combined with data_cid."
        ),
    ] = None,
    data_cid: Annotated[
        Optional[str],
        Field(
            description="Google Customer ID (CID) of the place. Cannot be combined with place_id."
        ),
    ] = None,
    hl: Annotated[
        Optional[str], Field(description="Two-letter language code, e.g. 'en', 'fr'.")
    ] = None,
    gl: Annotated[
        Optional[str], Field(description="Two-letter country code, e.g. 'us', 'uk'.")
    ] = None,
) -> dict:
    """Retrieve detailed information about a specific Google Maps place.

    Supply either place_id or data_cid (not both). Returns full place data
    including description, operating hours, service options, photos link,
    and reviews link.
    On failure returns {"error": {...}} — a detailed object with `message`, `type`,
    `retryable`, `status_code`, `attempts`, and the `query` that failed. When
    `retryable` is false, re-calling with the same input will not help.
    """
    if not place_id and not data_cid:
        raise ValueError("Provide either place_id or data_cid.")
    if place_id and data_cid:
        raise ValueError("Provide only one of place_id or data_cid, not both.")
    return _request_safe(
        dict(
            type="place",
            place_id=place_id,
            data_cid=data_cid,
            hl=hl,
            gl=gl,
        )
    )


class McpService:
    """Creates MCP services backed by environment configuration."""

    @staticmethod
    def google_maps() -> MCPToolset:
        """Create an MCP toolset for the Google Maps API (official MCP server)."""
        return MCPToolset(
            "https://mapstools.googleapis.com/mcp",
            headers={"X-Goog-Api-Key": env.google_maps_api_key},
        )

    @staticmethod
    def google_maps_serpapi() -> FunctionToolset:
        """Create a toolset that calls SerpApi's Google Maps REST API."""
        return FunctionToolset([search_places, get_place_details])
