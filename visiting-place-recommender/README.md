# Visiting Place Recommender (VPR)

A2A agent that recommends attractions in a destination city from a natural-language brief. Built with **pydantic-ai** and exposed via **FastA2A**.

Parent overview: [../README.md](../README.md)

---

## Role

- Requires an identifiable **destination city** and an **experience type/category** (unless the user asks for general recommendations) before searching.
- Searches places via **SerpApi’s Google Maps engine** (`search_places`, `get_place_details`).
- Returns ranked places (`rank` starting at 0) with title, address, GPS, hours, rating, price, thumbnail, and links.
- Caps recommendations at a practical list size (orchestrator tools document up to ~10 places per call).

Place search is wired through SerpApi (`McpService.google_maps_serpapi` in [app/main.py](app/main.py)).

---

## Project layout

```text
app/
  main.py
  environment_service.py
  llm_model_service.py      # openai | openrouter | cerebras
  agent_service.py
  mcp_service.py            # SerpApi place search / details tools
  schemas.py                # Place, PlaceRecommenderOutput
  utils.py
  files/
    system_prompts.yml
    agent_cards.yml
Dockerfile
railway.toml
pyproject.toml
```

---

## Configuration

`PUBLIC_URL` is **required**. Example `.env`:

| Variable | Default | Purpose |
| --- | --- | --- |
| `PUBLIC_URL` | _(required)_ | A2A agent card URL (e.g. `http://localhost:8001`) |
| `APP_MODE` | `a2a` | `a2a` (typical) or `web` |
| `PROVIDER` | `openai` | `openai` \| `openrouter` \| `cerebras` |
| `MODEL_NAME` | `gpt-5.4-mini` | See `ModelEnum` in `environment_service.py` |
| `OPENAI_API_KEY` / `OPENROUTER_API_KEY` / `CEREBRAS_API_KEY` | | Matching provider key |
| `SERPAPI_API_KEY` | | SerpApi key for Maps search |
| `LOGFIRE_TOKEN` | | Optional |
| `LOGFIRE_ENVIRONMENT` | `local` | Optional |

---

## Run

```bash
cd visiting-place-recommender
uv sync
# PUBLIC_URL=http://localhost:8001 and keys in .env or the shell
uv run uvicorn main:app --app-dir app --port 8001
```

- Health: `GET /health` → `ok`
- Agent card: `GET /.well-known/agent-card.json`
- A2A: `POST /` with JSON-RPC `message/send` / `tasks/get`

Compose maps host `8001` → container `8000`. [railway.toml](railway.toml) supports Railway Dockerfile deploys with `/health`.

---

## Output shape

Structured `PlaceRecommenderOutput` ([app/schemas.py](app/schemas.py)):

- `description` — recommendation summary  
- `places` — ranked list of `Place` objects grounded in search results

---

## Dependencies

`fastapi`, `uvicorn`, `pydantic-ai`, `pydantic-settings`, `fasta2a[pydantic-ai]`, `logfire[httpx]`, `pyyaml`. Python ≥ 3.12.
