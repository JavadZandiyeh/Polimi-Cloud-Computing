from __future__ import annotations

import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx
import logfire
from opentelemetry import context as otel_context
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
            raise A2AClientError(
                f"A2A request to {self._base_url} failed: {exc}"
            ) from exc

        return self._extract_output(task)

    def _poll_for_completion(
        self, client: httpx.Client, task_id: str
    ) -> dict[str, Any]:
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


def _call_agent(base_url: str, request_text: str, agent: str) -> str:
    """Invoke a downstream agent and return its result as a JSON string for the LLM.

    Wraps the call in a Logfire span named after the target agent so each A2A round-trip
    shows up as its own labelled, timed step in the orchestrator's trace.
    """
    with logfire.span("a2a call → {agent}", agent=agent) as span:
        span.set_attribute("a2a.base_url", base_url)
        span.set_attribute("a2a.request_chars", len(request_text))
        try:
            result = A2AClient(base_url).invoke(request_text)
        except A2AClientError as exc:
            span.set_attribute("a2a.ok", False)
            span.set_attribute("a2a.error", str(exc))
            return json.dumps({"error": str(exc)})
        span.set_attribute("a2a.ok", True)
        if isinstance(result, dict):
            span.set_attribute("a2a.result_keys", sorted(result.keys()))
        return json.dumps(result, default=str)


# ---------------------------------------------------------------------------
# Strands tools — one per downstream agent
# ---------------------------------------------------------------------------


@tool
def recommend_visiting_places(request: str) -> str:
    """Recommend places to visit in a destination city (Agent 1 — VPR).

    Call this first, once the destination city AND the interests/experience type are known,
    to obtain the main attractions for the trip. The agent searches Google Maps and returns
    places that are strong candidates for the schedule. Keep its full JSON output verbatim —
    you will need each place's details later both to schedule days and to render the final
    itinerary (addresses, hours, images, links).

    Args:
        request: A natural-language trip-planning brief for the recommender. MUST name the
            destination city and the interests/experience type (e.g. historical, food, art,
            nightlife) unless the user explicitly wants general recommendations. Also include
            any known context: budget, trip length, accommodation area, travel style, and
            things to avoid.

    Returns:
        A JSON string with `description` (summary, or — when `places` is empty — which
        required information is still missing) and `places`: a ranked list (rank 0 = best,
        no gaps, up to 10). Each place has: `title`, `address`, `gps_coordinates`
        {latitude, longitude}, `phone`, `description`, `rating` (0–5), `reviews` (count),
        `open_state`, `hours` {monday…sunday: free-text window or null}, `price` ("$"–"$$$$"),
        `type` (category labels), `thumbnail` (image URL), `links` {website, photos, reviews},
        and `rank`. On failure, a JSON object with an `error` key.
    """
    return _call_agent(env.vpr_url, request, "visiting-place-recommender")


@tool
def cluster_visiting_places(request: str) -> str:
    """Group recommended places into per-day clusters (Agent 2 — VPC).

    Call this after `recommend_visiting_places` to split the places across the trip
    days so each day is geographically coherent and travel within a day is minimised.

    Args:
        request: A natural-language request that MUST embed both (a) the list of places
            from the recommender — for clustering, each place needs at least its `title`,
            `gps_coordinates` {latitude, longitude}, and `type` — and (b) the number of
            trip days (or the start/end dates from which the day count is derived). You may
            paste the recommender's `places` JSON directly.

    Returns:
        A JSON string with `description` and `clusters`: one entry per trip day, each with a
        1-based `day` index and `places` — a list of place TITLE STRINGS only (not full
        objects). Re-join each title with the matching place object from the recommender's
        output to recover its coordinates, address, hours, and other details before
        scheduling that day. `clusters` is empty when the places or number of days are
        missing. On failure, a JSON object with an `error` key.
    """
    return _call_agent(env.vpc_url, request, "visiting-place-clusterer")


@tool
def schedule_single_day_plan(request: str) -> str:
    """Build a single day's timetable from a cluster of places (Agent 3 — SDS).

    Call this once per trip day, passing that day's cluster of places. The scheduler
    orders the visits, estimates travel between them, and inserts lunch
    (it calls the food recommender itself — you do NOT call it).

    Args:
        request: A JSON object string matching the DaySchedulingRequest schema for ONE day.
          Build each place from the matching recommender place (looked up by title from the
          day's cluster), NOT from the cluster entry (which is just a title):
            {
              "places": [
                {
                  "id": "<slug derived from the place title>",
                  "name": "<place title, unchanged>",
                  "location": {"latitude": <gps_coordinates.latitude>,
                               "longitude": <gps_coordinates.longitude>,
                               "address": "<place address if known>"},
                  "estimated_visit_duration_minutes": <int; estimate from type — major
                      museums/sites ~120, churches/landmarks ~60, parks/markets/squares ~90;
                      never 0>,
                  "priority_score": <float; higher = visit earlier; derive from rank,
                                     e.g. 1.0 for rank 0 and decreasing for higher ranks>,
                  "category": "<optional, e.g. first label from type>",
                  "summary": "<optional, the place's description>",
                  "opening_hours": [
                     {"day_of_week": "<lowercase weekday>",
                      "open_time": "HH:MM:SS", "close_time": "HH:MM:SS"}
                  ]
                }
              ],
              "day_start": "YYYY-MM-DDTHH:MM:SS",
              "day_end": "YYYY-MM-DDTHH:MM:SS",
              "food_budget_per_day": <optional float EUR>,
              "preferences": ["<optional cuisine/activity strings>"],
              "acceptable_transport_modes": ["walking" | "driving" | "transit" | "bicycling"]
            }
          `opening_hours` is OPTIONAL: include an entry only for weekdays whose recommender
          `hours` clearly state a single open–close window, converted to 24h "HH:MM:SS";
          omit closed or ambiguous days (the scheduler then treats the place as always open).
          `places` must contain at least one place.

    Returns:
        A JSON string with `description`, `day_schedule` (`date` plus ordered `events` of
        type visit/travel/meal), `unscheduled_places`, and `warnings`. On failure, a JSON
        object with an `error` key.
    """
    return _call_agent(env.sds_url, request, "single-day-plan-scheduler")


@tool
def schedule_days_plan(request: str) -> str:
    """Schedule SEVERAL trip days AT ONCE, in parallel (Agent 3 — SDS, fan-out).

    Use this INSTEAD of calling `schedule_single_day_plan` once per day for the initial
    scheduling pass: pass every day's cluster together and they are sent to the scheduler
    CONCURRENTLY (the days are independent), so an N-day trip is scheduled in roughly the
    time of one day instead of N sequential calls. Use `schedule_single_day_plan` afterwards
    only to RE-RUN an individual day that came back incomplete (gap filling, retries).

    Args:
        request: A JSON ARRAY string whose elements are each a DaySchedulingRequest for ONE
            day — exactly the same per-day object documented on `schedule_single_day_plan`
            (one entry per cluster, in day order). An object of the form {"days": [ ... ]}
            with that same array under `days` is also accepted.

    Returns:
        A JSON ARRAY string of per-day results, in the SAME ORDER as the input days. Each
        element is the scheduler's output for that day (`description`, `day_schedule`,
        `unscheduled_places`, `warnings`) or a JSON object with an `error` key if that day
        failed. Validate each element exactly as you would a single-day result, and re-run
        any failing/incomplete day with `schedule_single_day_plan`.
    """
    try:
        parsed = json.loads(request)
    except json.JSONDecodeError as exc:
        return json.dumps({"error": f"schedule_days_plan: invalid JSON request: {exc}"})

    if isinstance(parsed, dict):
        # Accept {"days": [ ... ]}, or a single day object passed on its own.
        parsed = parsed["days"] if "days" in parsed else [parsed]
    if not isinstance(parsed, list) or not parsed:
        return json.dumps(
            {"error": "schedule_days_plan: request must be a non-empty JSON array of days"}
        )

    day_requests = [json.dumps(day, default=str) for day in parsed]

    # The worker runs in a separate thread, so attach the current OTEL context inside it;
    # otherwise the per-day spans would not nest under the parent fan-out span.
    parent_ctx = otel_context.get_current()

    def _schedule_one(indexed: tuple[int, str]) -> str:
        index, day_request = indexed
        token = otel_context.attach(parent_ctx)
        try:
            with logfire.span("schedule day {day}", day=index + 1):
                return _call_agent(env.sds_url, day_request, "single-day-plan-scheduler")
        finally:
            otel_context.detach(token)

    # Fan out: the days are independent, so call the scheduler concurrently. Each
    # _call_agent is a blocking A2A round-trip, so threads (not asyncio) parallelise it.
    with logfire.span(
        "schedule {count} days in parallel", count=len(day_requests)
    ) as span:
        with ThreadPoolExecutor(max_workers=len(day_requests)) as pool:
            results = list(pool.map(_schedule_one, enumerate(day_requests)))
        span.set_attribute(
            "days.failed", sum(1 for r in results if '"error"' in r)
        )

    # Results are already JSON strings; re-join into a single JSON array for the LLM.
    return f"[{','.join(results)}]"


ORCHESTRATOR_TOOLS = [
    recommend_visiting_places,
    cluster_visiting_places,
    schedule_days_plan,
    schedule_single_day_plan,
]
