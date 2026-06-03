# Orchestrator (Agent 0)

Top-level agent of the multi-agent travel planner. Users chat with it through a **web UI**;
it coordinates the three specialist agents over the **A2A protocol** to produce a complete,
day-by-day itinerary.

Unlike the other services (which use `pydantic-ai` or `google-adk`), the orchestrator is built
with the **Amazon Strands Agents SDK** (`strands-agents`) — the agent development layer for
Bedrock AgentCore — but configured with the **same OpenAI model** as the recommender
(`gpt-5.4-mini`) via Strands' OpenAI model provider.

## How it works

The orchestrator exposes the downstream agents to the LLM as **tools** (`app/a2a_client_service.py`).
For each user request it runs this pipeline:

1. **recommend_visiting_places** → Agent 1 (VPR, `:8001`) — attractions in the destination city.
2. **cluster_visiting_places** → Agent 2 (VPC, `:8002`) — groups places into per-day clusters.
3. **schedule_single_day_plan** → Agent 3 (SDS, `:8003`), once per day — builds each day's
   timetable (it inserts meals via Agent 4 and estimates travel itself).

The orchestrator then assembles the per-day schedules into one friendly itinerary. Conversation
state is kept per browser session, so it can ask follow-up questions (supervised agentic loop).

## Project layout

```text
app/
  main.py                 # builds the ASGI app + /health shim
  environment_service.py  # singleton settings (app, llm, downstream agents, observability)
  llm_model_service.py    # builds the Strands model (OpenAI default; Bedrock optional)
  agent_service.py        # configures the Strands Agent; exposes .web / .a2a handles
  a2a_client_service.py   # generic A2A JSON-RPC client + the 3 downstream tools
  web_service.py          # Starlette chat UI (SSE streaming) + per-session agents
  utils.py                # cached YAML loader
  files/
    system_prompts.yml    # orchestrator persona + pipeline contract
    agent_cards.yml       # orchestrator's own A2A card
```

## Run locally

The orchestrator needs Agents 1–3 reachable. Start each agent first (from its own folder), e.g.:

```bash
# in each of visiting-place-recommender / visiting-place-clusterer / single-day-plan-scheduler:
uv sync
uv run uvicorn main:app --app-dir app --port 8001   # VPR (8002 VPC, 8003 SDS)
# SDS also needs single-day-plan-scheduler-mcp and food-place-recommender running.
```

Then the orchestrator:

```bash
cd orchestrator
uv sync
uv run uvicorn main:app --app-dir app --port 8000
```

- Health check: `curl http://localhost:8000/health` → `ok`
- Chat UI: open `http://localhost:8000/`

Try: *"Plan a 2-day trip to Milan, I like history and food, mid budget, 2026-06-10 to 2026-06-11."*

### docker-compose

`docker-compose.yml` already wires the orchestrator on host port `8000` with its dependencies.
When running under compose, point the downstream URLs at the container names (see commented values
in `.env`): `http://visiting-place-recommender:8000`, etc.

## Configuration (`.env`)

| Variable | Default | Purpose |
| --- | --- | --- |
| `APP_MODE` | `web` | Serving mode. The orchestrator only supports `web`. |
| `PROVIDER` | `openai` | LLM provider for Strands (`openai` or `bedrock`). |
| `MODEL_NAME` | `gpt-5.4-mini` | Model id passed to the provider. |
| `OPENAI_API_KEY` | – | OpenAI key (when `PROVIDER=openai`). |
| `AWS_REGION` | `us-east-1` | Region (when `PROVIDER=bedrock`). |
| `VPR_URL` / `VPC_URL` / `SDS_URL` | `localhost:8001/2/3` | Base A2A URLs of Agents 1–3. |
| `TIMEOUT_SECONDS` / `POLL_INTERVAL_SECONDS` / `MAX_WAIT_SECONDS` | `30 / 1 / 180` | A2A task polling. |
| `LOGFIRE_TOKEN` | – | Optional Logfire token. |

## Notes

- Deployment (Bedrock AgentCore / Railway) is out of scope here — this is local development only.
- End-to-end planning requires the downstream agents to be up and a valid OpenAI key.
