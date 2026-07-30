# Food Place Recommender (FPR)

A2A agent that finds restaurant candidates near a geographic point for a meal slot (`breakfast` | `lunch` | `dinner`). Built with **pydantic-ai** + **FastA2A**. Called by the **Single-Day Plan Scheduler** (not directly by the orchestrator).

Parent overview: [../README.md](../README.md)

---

## Role

1. Gate on meal slot + search center (lat/lng).
2. Call Google Places **Nearby Search (Legacy)** once via `PlacesService.search_restaurants`.
3. Sort by rating (then price level), annotate short descriptions, return `FoodRecommenderOutput`.

Candidates include name, location, rating, optional price level and venue labels, photo URL when available, and a short rationale.

---

## Project layout

```text
app/
  main.py
  environment_service.py
  llm_model_service.py
  agent_service.py
  places_service.py       # Google Places Nearby Search wrapper
  schemas.py
  utils.py
  files/
    system_prompts.yml
    agent_cards.yml
Dockerfile
pyproject.toml
```

---

## Configuration

`PUBLIC_URL` is **required**.

| Variable | Default | Purpose |
| --- | --- | --- |
| `PUBLIC_URL` | _(required)_ | A2A card URL (e.g. `http://localhost:8004`) |
| `APP_MODE` | `a2a` | `a2a` or `web` |
| `PROVIDER` | `openai` | `openai` \| `openrouter` \| `cerebras` |
| `MODEL_NAME` | `gpt-5.4-mini` | See `ModelEnum` |
| Provider API keys | | Matching `OPENAI_*` / `OPENROUTER_*` / `CEREBRAS_*` |
| `GOOGLE_PLACES_API_KEY` | | Places Nearby Search + Photo |
| `LOGFIRE_TOKEN` / `LOGFIRE_ENVIRONMENT` | | Optional observability |

---

## Input / output

Input (JSON text in the A2A message), `FoodRecommenderInput`:

- `timeofday`, `searchcenter` `{latitude, longitude, address?}`
- Optional: `searchradiusmeters` (100–50000, default 1000), `budgetpermealperperson`, `preferences`

Output: `restaurantcandidates` + `description` ([app/schemas.py](app/schemas.py)).

---

## Run

```bash
cd food-place-recommender
uv sync
uv run uvicorn main:app --app-dir app --port 8004
```

Compose: host port `8004`. Health: `GET /health`.

SDS defaults to `FOOD_RECOMMENDER_URL=http://localhost:8004`; under Compose set it to `http://food-place-recommender:8000`.

---

## Dependencies

`fastapi`, `uvicorn`, `pydantic-ai`, `pydantic-settings`, `fasta2a[pydantic-ai]`, `httpx`, `logfire[httpx]`, `pyyaml`. Python ≥ 3.12.
