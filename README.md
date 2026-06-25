# Polimi Cloud Computing — Multi-Agent Travel Planner

A multi-agent system that turns a single natural-language request
(*"Plan a 2-day trip to Milan, I like history and food, mid budget, 2026-06-10 to 2026-06-11"*)
into a complete, polished, **day-by-day travel itinerary** — attractions, meals, opening
hours, travel times, ratings, prices and images.

The project is intentionally **polyglot at the framework level**: each agent is built with a
*different* agent development kit (pydantic-ai, Google ADK, Amazon Strands), runs in its own
container, and talks to the others over open standards (**A2A** and **MCP**). This was a
deliberate goal of the Cloud Computing project — to integrate heterogeneous agent SDKs,
model providers, and cloud platforms behind a single coherent product.

---

## 1. Overview — what the project is

The system is a **supervised agentic pipeline**. A top-level orchestrator chats with the user,
gathers the required inputs (destination, dates/duration, interests, budget), and then drives a
chain of specialist agents to assemble the final plan. Each specialist does one job well and
exposes it as a remote, callable service.

### The agents

| # | Agent | Folder | Role | Built with |
|---|-------|--------|------|------------|
| **0** | **Orchestrator** | [orchestrator/](orchestrator/) | Web chat UI; gathers requirements and coordinates Agents 1–3 over A2A; assembles the final itinerary. | **Amazon Strands Agents SDK** |
| **1** | **Visiting Place Recommender (VPR)** | [visiting-place-recommender/](visiting-place-recommender/) | Finds attractions in the destination city (with images, address, hours, coordinates, rating, price, links). | **pydantic-ai** + FastA2A |
| **2** | **Visiting Place Clusterer (VPC)** | [visiting-place-clusterer/](visiting-place-clusterer/) | Groups the recommended places into one balanced, geographically-coherent cluster **per trip day**. | **pydantic-ai** + FastA2A |
| **3** | **Single-Day Plan Scheduler (SDS)** | [single-day-plan-scheduler/](single-day-plan-scheduler/) | Builds each day's timetable: orders visits, estimates travel, inserts breakfast/lunch/dinner. Called once per day. | **Google ADK** (via LiteLLM) |
| **4** | **Food Place Recommender (FPR)** | [food-place-recommender/](food-place-recommender/) | Returns restaurant candidates for a meal slot near a location, within budget. Called by Agent 3. | **pydantic-ai** + FastA2A |
| — | **SDS MCP Server** | [single-day-plan-scheduler-mcp/](single-day-plan-scheduler-mcp/) | Tool server (not an LLM agent) providing route/place tools to Agent 3. | **FastMCP** (MCP) |

### How they are connected

```text
                          ┌───────────────────────────────┐
        Browser  ◄──SSE──►│  Agent 0: Orchestrator (web)  │   Amazon Strands Agents SDK
                          └───────────────┬───────────────┘
                          exposes Agents 1–3 to the LLM as tools
              ┌───────────────────────────┼───────────────────────────┐
              │ A2A                        │ A2A                        │ A2A (once per day)
              ▼                            ▼                            ▼
  ┌────────────────────┐      ┌────────────────────┐      ┌──────────────────────────┐
  │ Agent 1: VPR       │      │ Agent 2: VPC       │      │ Agent 3: SDS             │
  │ recommend places   │ ───► │ cluster into days  │ ───► │ schedule one day         │
  │ pydantic-ai        │      │ pydantic-ai        │      │ Google ADK               │
  └─────────┬──────────┘      └────────────────────┘      └─────┬──────────────┬─────┘
            │ tools                  balanced K-means            │ MCP          │ A2A
            ▼                                                    ▼              ▼
   Google Maps / SerpAPI                              ┌──────────────────┐  ┌──────────────┐
   (place search)                                     │ SDS MCP server   │  │ Agent 4: FPR │
                                                      │ Google Routes API│  │ restaurants  │
                                                      │ (travel times)   │  │ Google Places│
                                                      └──────────────────┘  └──────────────┘
```

The orchestrator's pipeline ([orchestrator/app/a2a_client_service.py](orchestrator/app/a2a_client_service.py)):

1. **recommend_visiting_places** → Agent 1 (VPR) — attractions for the city.
2. **cluster_visiting_places** → Agent 2 (VPC) — one cluster of places per day.
3. **schedule_single_day_plan** → Agent 3 (SDS), once per day — each day's timetable. SDS in
   turn calls the **MCP server** for travel estimates and **Agent 4 (FPR)** for meals.

The orchestrator then re-joins everything and returns a friendly, image-rich itinerary, keeping
per-session conversation state so it can ask follow-ups and make adjustments. It recovers from
downstream errors (retry or ask the user) and keeps the days that succeeded rather than aborting.

### Final expected output

A **complete day-by-day itinerary**: for each day, an ordered list of attractions with exact
visit times, travel between them, and inserted breakfast/lunch/dinner — each entry enriched with
images, address, opening hours, coordinates, rating, price level and links.

---

## 2. Technologies — agent development & telemetry

### Agent development kits (deliberately one per agent)

| SDK | Where used | Notes |
|-----|-----------|-------|
| **[pydantic-ai](https://ai.pydantic.dev/)** | VPR, VPC, FPR | Type-safe agent framework. Agents are exposed as A2A services via **FastA2A** (`fasta2a[pydantic-ai]`). Tools come from `MCPToolset` / `FunctionToolset`. |
| **[Google ADK](https://google.github.io/adk-docs/)** (`google-adk[a2a]`) | SDS | Google's Agent Development Kit. Non-Google models are reached through **LiteLLM** (`LiteLlm`), so the same OpenAI/OpenRouter/Cerebras models work; Gemini is passed natively. |
| **[Amazon Strands Agents SDK](https://strandsagents.com/)** (`strands-agents[openai]`) | Orchestrator | The agent layer used for Bedrock AgentCore, but here configured with **Strands' OpenAI model provider**. Optional Bedrock provider is wired but off by default. |

### Inter-agent & tool protocols

- **A2A (Agent-to-Agent) protocol** — JSON-RPC over HTTP (`message/send`, `tasks/get`, agent
  cards at `/.well-known/agent-card.json`). The orchestrator is a generic A2A **client**; Agents
  1–4 are A2A **servers** (FastA2A for the pydantic-ai agents, `google-adk[a2a]` for SDS).
- **MCP (Model Context Protocol)** — `mcp` / **FastMCP**. The SDS MCP server publishes
  `route_estimate` and `place_details` tools that the scheduler consumes as an MCP toolset.

### Web/serving stack (shared by every service)

- **FastAPI** + **Uvicorn** (ASGI) — HTTP serving, `/health` checks, and (orchestrator) a
  Starlette chat UI with **SSE streaming**.
- **pydantic** / **pydantic-settings** — typed schemas and a singleton, validated
  `EnvironmentService` per service (provider/model/credentials read from `.env` or real env vars).
- **PyYAML** — agent cards and system prompts are loaded from `app/files/*.yml`.

### External data APIs (the "real-world" grounding)

- **Google Maps Places** — restaurant search in FPR (`maps.googleapis.com/maps/api/place`).
- **SerpAPI / Google Maps** — attraction search in VPR.
- **Google Maps Platform Routes API** — travel distance/duration in the SDS MCP server.

### Telemetry / observability

- **[Pydantic Logfire](https://logfire.pydantic.dev/)** (`logfire[httpx,litellm]`) is the unified
  tracing layer across **all** services. Each `EnvironmentService` configures Logfire with the
  service name/version and a deployment label, then instruments the relevant frameworks:
  - `logfire.instrument_openai()` — captures every LLM call,
  - `logfire.instrument_httpx()` — captures outbound A2A / API calls,
  - `litellm` instrumentation in the Google ADK scheduler.
  - Export is gated on a token (`send_to_logfire="if-token-present"`), so traces are emitted only
    when `LOGFIRE_TOKEN` is set; otherwise spans stay local. A single chat turn shows up as one
    drill-down trace spanning the orchestrator and every downstream agent.

### Packaging & build tooling

- **uv** — dependency management and locking (`pyproject.toml` + `uv.lock`) in every service.
- **Docker** — one `Dockerfile` per service; **docker-compose** ([docker-compose.yml](docker-compose.yml))
  wires all six containers on a shared bridge network for local end-to-end runs.
- **Ruff** — linting (`.ruff_cache/`).

---

## 3. Where they are deployed

The services are cloud-portable containers; the repo ships configuration for the platforms that
were actually used:

| Platform | What runs there | Config |
|----------|-----------------|--------|
| **Railway** | **Orchestrator (Agent 0)** and **Visiting Place Recommender (Agent 1)** — Dockerfile builds with a `/health` healthcheck; `PUBLIC_URL` bound to `${{RAILWAY_PUBLIC_DOMAIN}}`. | [orchestrator/railway.toml](orchestrator/railway.toml), [visiting-place-recommender/railway.toml](visiting-place-recommender/railway.toml) |
| **Microsoft Azure** | **Visiting Place Clusterer (Agent 2)** — the container runs on **Azure Container Apps (ACA)** (image in **Azure Container Registry**, logs in **Log Analytics**), and the **LLM model is hosted on Azure AI Foundry / Azure OpenAI**. Pinned to a single replica because FastA2A keeps task state in memory. | [visiting-place-clusterer/README.md](visiting-place-clusterer/README.md) |
| **Local / any host** | All six services together via **docker-compose** (orchestrator on `:8000`, VPR `:8001`, VPC `:8002`, SDS `:8003`, FPR `:8004`, MCP `:8005`). | [docker-compose.yml](docker-compose.yml) |

> **Google Cloud Run** is the natural home for the **Google ADK scheduler (Agent 3)** and its
> MCP server (Google's own deployment target for ADK agents). Those services are containerized
> and Cloud-Run-ready, but this repo does not yet check in Cloud Run deployment config — they run
> via docker-compose today. **AWS Bedrock** is wired as an optional provider for the Strands
> orchestrator but is disabled by default (it uses OpenAI).

---

## 4. Models & providers

The same logical models are reachable through **several different providers/gateways** — chosen
per agent to spread cost, fit each SDK, and demonstrate provider portability. Provider selection
is a single `PROVIDER` env var per service, resolved in each `llm_model_service.py`.

| Provider / gateway | How it's reached | Used by | Example model(s) |
|--------------------|------------------|---------|------------------|
| **OpenAI (direct)** | `AsyncOpenAI` → pydantic-ai `OpenAIResponsesModel`; Strands `OpenAIModel`; ADK via `LiteLlm("openai/…")` | Orchestrator, VPR, VPC, SDS | `gpt-5.4`, `gpt-5.4-mini` |
| **OpenAI via OpenRouter** | `AsyncOpenAI(base_url="https://openrouter.ai/api/v1")` → pydantic-ai `OpenRouterModel`; ADK via `LiteLlm("openrouter/…")` | **FPR** (default), VPR, VPC, SDS | `openai/gpt-4o-mini-2024-07-18`, `openai/gpt-oss-120b:free` |
| **OpenAI via Azure AI Foundry (Azure OpenAI)** | `AsyncAzureOpenAI` → pydantic-ai `AzureProvider` / `OpenAIChatModel` (model name = Azure **deployment** name) | **VPC** (default in production) | `gpt-4o-mini` deployment (model `gpt-4o-mini`, ver `2024-07-18`, GlobalStandard) |
| **Cerebras** | pydantic-ai `CerebrasProvider`; ADK via `LiteLlm("cerebras/…")` (with a JSON-schema transformer that strips unsupported numeric `format`) | VPR, VPC, FPR, SDS | `qwen-3-235b-a22b-instruct-2507` |
| **Google (Gemini)** | Native ADK model string | SDS | `gemini-2.0-flash` |
| **AWS Bedrock** | Strands `BedrockModel` (optional, off by default) | Orchestrator | `anthropic.claude-3-5-sonnet-20240620-v1:0` |

**Key takeaway:** the *same* OpenAI-class model can be served three ways in this project — **direct
from OpenAI**, **through OpenRouter**, and **through Azure AI Foundry's Azure OpenAI** — plus
alternative providers (Cerebras, Google Gemini, optional AWS Bedrock). Each agent's
`llm_model_service.py` is the single switch point that maps a `PROVIDER` value to the right client.

---

## 5. Running locally

End-to-end with Docker:

```bash
docker compose up --build
# Orchestrator chat UI:  http://localhost:8000
# Health:                curl http://localhost:8000/health  -> ok
```

Or per-service with uv (each in its own folder), e.g. the recommender:

```bash
cd visiting-place-recommender
uv sync
uv run uvicorn main:app --app-dir app --port 8001
```

Each service has its own `.env` (provider, model, credentials) and a `README.md` with deployment
details. See each agent's folder for specifics.

---

## License

See [LICENSE.txt](LICENSE.txt).
