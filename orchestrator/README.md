# Orchestrator (Agent 0)

Top-level agent of the multi-agent travel planner. Users chat through a **web UI**; the agent coordinates specialist agents over **A2A** and returns a Markdown day-by-day itinerary.

Built with the **Amazon Strands Agents SDK** (`strands-agents[openai]`). Default LLM provider is OpenAI (`gpt-5.4-mini`); AWS Bedrock is optional.

Parent overview: [../README.md](../README.md)

---

## Role

1. Collect destination, trip duration/dates, and interests (hard gates before tools).
2. Call downstream A2A tools in order: recommend places → cluster by day → schedule days.
3. Validate and retry incomplete days; optionally fetch more places if days have spare capacity.
4. Render a detail-rich Markdown itinerary (images, addresses, hours, ratings, lunch).

The orchestrator does **not** call the food recommender directly — SDS inserts lunch via FPR.

---

## How it works

Tools in [app/a2a_client_service.py](app/a2a_client_service.py):

| Tool | Target | Purpose |
| --- | --- | --- |
| `recommend_visiting_places` | VPR | Attractions for the destination |
| `cluster_visiting_places` | VPC | One place-title cluster per day |
| `schedule_days_plan` | SDS | Fan-out: schedule all days in parallel (`ThreadPoolExecutor`) |
| `schedule_single_day_plan` | SDS | Retry / re-plan a single incomplete day |

A2A client flow: JSON-RPC `message/send` → poll `tasks/get` until completed → extract structured artifact data (unwraps pydantic-ai `{"result": ...}` envelopes).

Web UI ([app/web_service.py](app/web_service.py)): Starlette app with SSE streaming and one Strands `Agent` instance per browser session. Serving mode is `APP_MODE=web`.

---

## Project layout

```text
app/
  main.py                 # ASGI app + /health
  environment_service.py  # settings (app, llm, downstream, observability)
  llm_model_service.py    # Strands OpenAI or Bedrock model
  agent_service.py        # Strands agent factory + agent card/prompt loaders
  a2a_client_service.py   # A2A client + orchestrator tools
  web_service.py          # Chat UI (SSE) + session store
  utils.py                # Cached YAML loader
  files/
    system_prompts.yml
    agent_cards.yml
Dockerfile
railway.toml              # Railway Dockerfile deploy + /health check
pyproject.toml
```

---

## Configuration

Create `orchestrator/.env` (also loaded when running outside Compose). Environment variables (pydantic-settings field names → env names are uppercase):

| Variable | Default | Purpose |
| --- | --- | --- |
| `PUBLIC_URL` | `http://localhost:8000` | Public base URL |
| `APP_MODE` | `web` | Must be `web` |
| `PROVIDER` | `openai` | `openai` or `bedrock` |
| `MODEL_NAME` | `gpt-5.4-mini` | Also allows `gpt-5.4` or Bedrock Claude id |
| `OPENAI_API_KEY` | _(empty)_ | Required when `PROVIDER=openai` |
| `AWS_REGION` | `us-east-1` | Used when `PROVIDER=bedrock` |
| `VPR_URL` | `http://visiting-place-recommender:8000` | VPR A2A base URL |
| `VPC_URL` | `http://visiting-place-clusterer:8000` | VPC A2A base URL |
| `SDS_URL` | `http://single-day-plan-scheduler:8000` | SDS A2A base URL |
| `TIMEOUT_SECONDS` | `15` | A2A connect timeout |
| `POLL_INTERVAL_SECONDS` | `1` | Task poll interval |
| `MAX_WAIT_SECONDS` | `300` | Max wait / read timeout for a task |
| `LOGFIRE_TOKEN` | _(empty)_ | Optional Logfire export |
| `LOGFIRE_ENVIRONMENT` | `local` | Deployment label in Logfire |

For local `uv` runs (agents on host ports), set e.g.:

```env
VPR_URL=http://localhost:8001
VPC_URL=http://localhost:8002
SDS_URL=http://localhost:8003
```

---

## Run locally

Start VPR, VPC, SDS (and SDS’s MCP + FPR dependencies), then:

```bash
cd orchestrator
uv sync
uv run uvicorn main:app --app-dir app --port 8000
```

- Chat UI: http://localhost:8000/
- Health: `curl http://localhost:8000/health` → `ok`

Or from the repo root: `docker compose up --build` (orchestrator on host port `8000`).

### Railway

[railway.toml](railway.toml) builds from the Dockerfile and health-checks `/health`. Set `PUBLIC_URL` to the public domain (e.g. `https://${{RAILWAY_PUBLIC_DOMAIN}}`) and point `VPR_URL` / `VPC_URL` / `SDS_URL` at the deployed specialist agents.

---

## Dependencies

From [pyproject.toml](pyproject.toml): `strands-agents[openai]`, `fastapi`, `uvicorn`, `pydantic-settings`, `httpx`, `logfire[httpx]`, `pyyaml`. Python ≥ 3.12.
