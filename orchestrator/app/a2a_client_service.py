from __future__ import annotations

import json
import time
import uuid
from typing import Any

import httpx
from strands import tool

from environment_service import env


class A2AClientError(Exception):
    """Raised when a downstream A2A agent cannot be reached or fails."""


class A2AClient:
    """Generic A2A JSON-RPC client for the fasta2a / google-adk agents in this project.

    Sends a single text message over the A2A protocol (`message/send`), polls the
    resulting task to completion (`tasks/get`), and returns the structured output
    dict carried in the task artifacts. All downstream agents (VPR, VPC, SDS) expose
    their Pydantic output model as the artifact part `data`, so the same extraction
    works for every agent.
    """

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._poll_interval = env.a2a_poll_interval_seconds
        self._max_wait = env.a2a_max_wait_seconds
        # Some agents (e.g. google-adk's to_a2a) run the whole task inside the
        # message/send call, so that request can block for the full task duration.
        # Allow reads up to max_wait; keep connect short to fail fast on a down agent.
        self._timeout = httpx.Timeout(self._max_wait, connect=env.a2a_timeout_seconds)

    def invoke(self, request_text: str) -> dict[str, Any]:
        """Send `request_text` to the agent and return its structured output dict."""
        message_id = f"orchestrator-{uuid.uuid4().hex}"
        send_payload = {
            "jsonrpc": "2.0",
            "method": "message/send",
            "params": {
                "message": {
                    "role": "user",
                    "parts": [{"kind": "text", "text": request_text}],
                    "kind": "message",
                    "messageId": message_id,
                },
                "configuration": {"acceptedOutputModes": ["application/json"]},
            },
            "id": message_id,
        }

        try:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.post(f"{self._base_url}/", json=send_payload)
                response.raise_for_status()
                task_id = self._extract_task_id(response.json())
                task = self._poll_for_completion(client, task_id)
        except httpx.HTTPError as exc:
            raise A2AClientError(f"A2A request to {self._base_url} failed: {exc}") from exc

        return self._extract_output(task)

    def _poll_for_completion(self, client: httpx.Client, task_id: str) -> dict[str, Any]:
        started = time.monotonic()
        while True:
            if time.monotonic() - started > self._max_wait:
                raise A2AClientError(f"A2A task on {self._base_url} timed out")

            response = client.post(
                f"{self._base_url}/",
                json={
                    "jsonrpc": "2.0",
                    "method": "tasks/get",
                    "params": {"id": task_id},
                    "id": f"{task_id}-poll",
                },
            )
            response.raise_for_status()
            task = response.json().get("result", {})
            state = task.get("status", {}).get("state")
            if state == "completed":
                return task
            if state in {"failed", "canceled", "rejected"}:
                raise A2AClientError(
                    f"A2A task on {self._base_url} ended in state '{state}'"
                )
            time.sleep(self._poll_interval)

    def _extract_task_id(self, payload: dict[str, Any]) -> str:
        task_id = payload.get("result", {}).get("id")
        if not isinstance(task_id, str) or not task_id:
            raise A2AClientError("A2A message/send response did not include a task id")
        return task_id

    def _extract_output(self, task: dict[str, Any]) -> dict[str, Any]:
        """Return the first structured-output dict found in the task artifacts.

        pydantic-ai's A2A integration nests the agent's structured output under a
        single `result` key inside the data part; google-adk exposes the fields
        directly. `_unwrap` normalises both so the orchestrator always sees the
        agent's actual output (e.g. {"description": ..., "places": [...]}).
        """
        for artifact in task.get("artifacts", []):
            for part in artifact.get("parts", []):
                data = part.get("data")
                if isinstance(data, dict):
                    return self._unwrap(data)
        # Fall back to any text part so the orchestrator still gets a signal.
        for artifact in task.get("artifacts", []):
            for part in artifact.get("parts", []):
                text = part.get("text")
                if isinstance(text, str) and text:
                    return {"text": text}
        return {}

    @staticmethod
    def _unwrap(data: dict[str, Any]) -> dict[str, Any]:
        """Unwrap a `{"result": {...}}` envelope (pydantic-ai) to the inner output."""
        result = data.get("result")
        if isinstance(result, dict):
            return result
        return data


def _call_agent(base_url: str, request_text: str) -> str:
    """Invoke a downstream agent and return its result as a JSON string for the LLM."""
    try:
        result = A2AClient(base_url).invoke(request_text)
    except A2AClientError as exc:
        return json.dumps({"error": str(exc)})
    return json.dumps(result, default=str)


# ---------------------------------------------------------------------------
# Strands tools — one per downstream agent
# ---------------------------------------------------------------------------


@tool
def recommend_visiting_places(request: str) -> str:
    """Recommend places to visit in a destination city (Agent 1 — VPR).

    Call this first, once the destination city is known, to obtain the main
    attractions for the trip. The agent searches Google Maps and returns places
    that are strong candidates for the schedule.

    Args:
        request: A natural-language trip-planning brief for the recommender. Include the
            destination city (required) and any known context: interests/experience type
            (e.g. historical, food, nightlife), budget, trip length, accommodation area,
            travel style, and things to avoid.

    Returns:
        A JSON string with `description` (summary or what is still missing) and `places`:
        a ranked list where each place has `name`, `place_url`, `photos_url`, `reviews_url`,
        `lat`, `lng`, `description`, and `rank` (0 = best). `places` is empty when required
        information is missing. On failure, a JSON object with an `error` key.
    """
    return _call_agent(env.vpr_url, request)


@tool
def cluster_visiting_places(request: str) -> str:
    """Group recommended places into per-day clusters (Agent 2 — VPC).

    Call this after `recommend_visiting_places` to split the places across the trip
    days so each day is geographically coherent and travel within a day is minimised.

    Args:
        request: A natural-language request that MUST embed both (a) the full list of
            places from the recommender — keep each place's `name`, `lat`, `lng`, and
            `rank` — and (b) the trip duration (start and end dates, or an explicit
            number of days). You may paste the recommender's `places` JSON directly.

    Returns:
        A JSON string with `description` and `clusters`: one entry per trip day, each with
        a 1-based `day` index and its assigned `places` (same fields as the recommender).
        `clusters` is empty when the places or trip duration are missing. On failure, a
        JSON object with an `error` key.
    """
    return _call_agent(env.vpc_url, request)


@tool
def schedule_single_day_plan(request: str) -> str:
    """Build a single day's timetable from a cluster of places (Agent 3 — SDS).

    Call this once per trip day, passing that day's cluster of places. The scheduler
    orders the visits, estimates travel between them, and inserts lunch and dinner
    (it calls the food recommender itself — you do NOT call it).

    Args:
        request: A JSON object string matching the DaySchedulingRequest schema for ONE day:
            {
              "places": [
                {
                  "id": "<slug-from-name>",
                  "name": "<place name>",
                  "location": {"latitude": <lat>, "longitude": <lng>},
                  "estimated_visit_duration_minutes": <int, e.g. 60-120>,
                  "priority_score": <float; higher = visit earlier; derive from rank,
                                     e.g. higher for rank 0>,
                  "category": "<optional>",
                  "summary": "<optional, the place's description>"
                }
              ],
              "day_start": "YYYY-MM-DDTHH:MM:SS",
              "day_end": "YYYY-MM-DDTHH:MM:SS",
              "food_budget_per_day": <optional float EUR>,
              "preferences": ["<optional cuisine/activity strings>"],
              "acceptable_transport_modes": ["walking" | "driving" | "transit" | "bicycling"]
            }
          Map each clustered place's `lat`/`lng` into `location.latitude`/`location.longitude`
          and derive `id` from the name. `places` must contain at least one place.

    Returns:
        A JSON string with `description`, `day_schedule` (`date` plus ordered `events` of
        type visit/travel/meal), `unscheduled_places`, and `warnings`. On failure, a JSON
        object with an `error` key.
    """
    return _call_agent(env.sds_url, request)


ORCHESTRATOR_TOOLS = [
    recommend_visiting_places,
    cluster_visiting_places,
    schedule_single_day_plan,
]
