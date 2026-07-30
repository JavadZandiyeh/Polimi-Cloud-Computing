# Single-Day Plan Scheduler MCP Server

MCP tool server used by the **Single-Day Plan Scheduler**. It exposes routing and place tools over streamable HTTP MCP (no LLM of its own).

Parent overview: [../README.md](../README.md)  
Consumer: [../single-day-plan-scheduler/README.md](../single-day-plan-scheduler/README.md)

---

## Tools

| Tool | Implementation | Notes |
| --- | --- | --- |
| `route_estimate` | Google Routes API `computeRoutes` | Modes: `walking`, `driving`, `transit`, `bicycling` |
| `place_details` | Local place-metadata helper | Returns structured place detail entries for given ids |

Built with **FastMCP** (`mcp.server.fastmcp`), `stateless_http=True`, ASGI app + `/health` wrapper ([app/main.py](app/main.py)).

---

## Project layout

```text
app/
  main.py                 # FastMCP tools + ASGI app
  environment_service.py
  routes_service.py       # Google Routes client + place_details
  schemas.py              # RouteEstimate, PlaceDetails, …
Dockerfile
pyproject.toml
```

---

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `GOOGLE_MAPS_API_KEY` | | Required for `route_estimate` |
| `GOOGLE_ROUTES_TIMEOUT_SECONDS` | `5` | HTTP timeout for Routes API |
| `LOGFIRE_TOKEN` | | Optional |
| `LOGFIRE_ENVIRONMENT` | `local` | Optional |

No LLM keys. No `PUBLIC_URL` / `APP_MODE`.

---

## Run

```bash
cd single-day-plan-scheduler-mcp
uv sync
uv run uvicorn main:app --app-dir app --port 8005
```

- Health: `GET /health` → `ok`
- MCP (streamable HTTP): typically `http://localhost:8005/mcp` (SDS default `MCP_URL`)

Compose: host port `8005` → container `8000`.

---

## Dependencies

`mcp`, `uvicorn`, `pydantic`, `pydantic-settings`, `httpx`, `logfire[httpx]`. Python ≥ 3.12.
