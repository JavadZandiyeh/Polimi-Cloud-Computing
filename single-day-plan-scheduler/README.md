# Single-Day Plan Scheduler (SDS)

A2A agent that turns **one day’s** place cluster into a chronological schedule of visits, travel legs, and **lunch**. Built with **Google ADK** (`google-adk[a2a]`) and **LiteLLM** for non-Gemini models.

Parent overview: [../README.md](../README.md)  
Companion MCP server: [../single-day-plan-scheduler-mcp/README.md](../single-day-plan-scheduler-mcp/README.md)  
Food agent: [../food-place-recommender/README.md](../food-place-recommender/README.md)

---

## Role

Given a `DaySchedulingRequest` (JSON):

1. Order places (greedy nearest-neighbour with opening-hours / duration constraints).
2. Estimate each travel leg via MCP tool `route_estimate` (Google Routes).
3. Insert lunch via `recommend_restaurants` (A2A call to FPR).
4. Return `DaySchedulingResult` (events, `unscheduled_places`, `warnings`).

### Planner + formatter pattern

A `SequentialAgent` ([app/agent_service.py](app/agent_service.py)) separates tool use from structured output:

1. **Planner** — calls routing and restaurant tools; writes the day plan JSON to state.  
2. **Formatter** — maps that plan into a typed `DaySchedulingResult`.

---

## Project layout

```text
app/
  main.py
  environment_service.py
  llm_model_service.py      # openrouter | openai | cerebras | google
  agent_service.py          # SequentialAgent (planner + formatter)
  mcp_service.py            # MCPToolset → SDS MCP URL
  food_recommender_service.py  # A2A client + recommend_restaurants tool
  schemas.py
  utils.py
  files/
    system_prompts.yml      # plan_scheduler + plan_scheduler_formatter
    agent_cards.yml
test_a2a_client.py          # Manual A2A smoke client
Dockerfile
pyproject.toml
```

---

## Configuration

`PUBLIC_URL` is **required**.

| Variable | Default | Purpose |
| --- | --- | --- |
| `PUBLIC_URL` | _(required)_ | A2A card URL (e.g. `http://localhost:8003`) |
| `APP_MODE` | `a2a` | A2A serving mode |
| `PROVIDER` | `openrouter` | `openrouter` \| `openai` \| `cerebras` \| `google` |
| `MODEL_NAME` | `openai/gpt-4o-mini-2024-07-18` | See `ModelEnum` |
| Provider keys | | `OPENROUTER_API_KEY`, `OPENAI_API_KEY`, `CEREBRAS_API_KEY`, or `GOOGLE_API_KEY` |
| `MCP_URL` | `http://localhost:8005/mcp` | Streamable HTTP MCP endpoint |
| `FOOD_RECOMMENDER_URL` | `http://localhost:8004` | FPR A2A base |
| `FOOD_RECOMMENDER_TIMEOUT_SECONDS` | `15` | Connect/request timeout to FPR |
| `FOOD_RECOMMENDER_POLL_INTERVAL_SECONDS` | `1` | A2A poll |
| `FOOD_RECOMMENDER_MAX_WAIT_SECONDS` | `60` | Max wait for FPR task |
| `LOGFIRE_TOKEN` / `LOGFIRE_ENVIRONMENT` | | Optional |

Under Compose, point MCP and FPR at container names, e.g. `http://single-day-plan-scheduler-mcp:8000/mcp` and `http://food-place-recommender:8000`.

---

## Run

```bash
# Terminals: SDS MCP (:8005) and FPR (:8004) must be up
cd single-day-plan-scheduler
uv sync
uv run uvicorn main:app --app-dir app --port 8003
```

Smoke test:

```bash
BASE_URL=http://localhost:8003 uv run python test_a2a_client.py
```

Compose: host port `8003`. Health: `GET /health`.

---

## Schema notes

See [app/schemas.py](app/schemas.py) for `DaySchedulingRequest` / `DaySchedulingResult`. Transport modes: `walking` | `driving` | `transit` | `bicycling`. The day plan includes visit, travel, and lunch meal events.

---

## Dependencies

`google-adk[a2a]`, `litellm`, `mcp`, `uvicorn`, `pydantic`, `pydantic-settings`, `httpx`, `logfire[httpx,litellm]`, `pyyaml`. Python ≥ 3.12.
