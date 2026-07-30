# Visiting Place Clusterer (VPC)

A2A agent that groups recommended places into **balanced, geographically coherent day clusters**. Built with **pydantic-ai** + **FastA2A**. Clustering itself is a deterministic **balanced K-means** tool (`cluster_places`); the LLM only validates inputs and formats the tool result.

Parent overview: [../README.md](../README.md)

---

## Role

**Gates before clustering:**

1. Non-empty place list where every place has usable latitude/longitude  
2. Clear number of trip days (explicit count or derived from dates)

When inputs are ready, the agent calls `cluster_places` and formats the tool result.

**Output:** `PlaceClustererOutput` with one entry per day (`day` 1-based, `places` as title strings). The orchestrator re-joins titles with full VPR place objects before scheduling.

Algorithm ([app/clustering_service.py](app/clustering_service.py)): size-constrained K-means (k-means++ init); cluster sizes differ by at most one; `k = min(num_days, len(places))`.

---

## Project layout

```text
app/
  main.py
  environment_service.py
  llm_model_service.py      # openai | openrouter | cerebras | azure
  agent_service.py
  clustering_service.py     # balanced K-means
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
| `PUBLIC_URL` | _(required)_ | A2A card URL (e.g. `http://localhost:8002`) |
| `APP_MODE` | `a2a` | `a2a` or `web` |
| `PROVIDER` | `openai` | `openai` \| `openrouter` \| `cerebras` \| `azure` |
| `MODEL_NAME` | `gpt-5.4-mini` | Ignored as model id when `PROVIDER=azure` (deployment name is used) |
| `OPENAI_API_KEY` / `OPENROUTER_API_KEY` / `CEREBRAS_API_KEY` | | Non-Azure providers |
| `AZURE_OPENAI_ENDPOINT` | | e.g. `https://<resource>.openai.azure.com` |
| `AZURE_OPENAI_DEPLOYMENT` | | Deployment name (model id for Azure path) |
| `AZURE_OPENAI_API_VERSION` | `2024-12-01-preview` | Azure API version |
| `AZURE_OPENAI_API_KEY` | | Azure key |
| `LOGFIRE_TOKEN` / `LOGFIRE_ENVIRONMENT` | | Optional |

---

## Run locally

```bash
cd visiting-place-clusterer
uv sync
uv run uvicorn main:app --app-dir app --port 8002
```

- Health: `GET /health` → `ok`
- Agent card: `GET /.well-known/agent-card.json`
- Compose: host port `8002`

---

## Dependencies

`fastapi`, `uvicorn`, `pydantic-ai`, `pydantic-settings`, `fasta2a[pydantic-ai]`, `logfire[httpx]`, `pyyaml`. Python ≥ 3.12. Clustering runs locally (no maps API).

---

## Azure deployment (Container Apps + Azure OpenAI)

Deploy the **container** on **Azure Container Apps (ACA)** and the **LLM** on **Azure AI Foundry / Azure OpenAI** (`PROVIDER=azure`).

```text
Dockerfile / image  ──►  Azure Container Apps   (agent, public HTTPS)
                                  │  calls
                                  ▼
                    Azure OpenAI deployment (e.g. gpt-4o-mini)
```

### Resources

| Thing | Value |
| --- | --- |
| Resource group / region | `polimi-cloud` / `uksouth` |
| Foundry / Azure OpenAI account | `visiting-place-clusterer` |
| Endpoint | `https://visiting-place-clusterer.openai.azure.com` |
| Model deployment | `gpt-4o-mini` (gpt-4o-mini · 2024-07-18 · GlobalStandard) |
| Container registry | `ca72f0c87afdacr.azurecr.io` |
| Container Apps environment | `polimi-cloud-env` |
| Container App | `visiting-place-clusterer` |
| Public URL | `https://visiting-place-clusterer.icypond-686fddf2.uksouth.azurecontainerapps.io` |

Runtime: `APP_MODE=a2a`, `PROVIDER=azure`. Store the API key as an ACA secret (e.g. `azure-openai-key`) and reference it with `AZURE_OPENAI_API_KEY=secretref:azure-openai-key`.

Recommended replica settings for this service: `--min-replicas 1 --max-replicas 1`.

### One-time setup

```bash
az login
az extension add -n containerapp --upgrade
az provider register -n Microsoft.App
az provider register -n Microsoft.OperationalInsights
az provider register -n Microsoft.ContainerRegistry

az cognitiveservices account deployment create \
  -n visiting-place-clusterer -g polimi-cloud \
  --deployment-name gpt-4o-mini \
  --model-name gpt-4o-mini --model-version 2024-07-18 --model-format OpenAI \
  --sku-name GlobalStandard --sku-capacity 10
```

### Build and push (linux/amd64)

Build locally and push to ACR (useful on Azure for Students where ACR cloud builds may be unavailable). Stage a clean context (`Dockerfile`, `pyproject.toml`, `uv.lock`, `README.md`, `.dockerignore`, `app/`) without `.env` / `.venv`:

```bash
SRC="$(pwd)"   # visiting-place-clusterer directory
rm -rf /tmp/vpc-build && mkdir -p /tmp/vpc-build
cp "$SRC/Dockerfile" "$SRC/pyproject.toml" "$SRC/uv.lock" "$SRC/README.md" "$SRC/.dockerignore" /tmp/vpc-build/
cp -R "$SRC/app" /tmp/vpc-build/app
find /tmp/vpc-build -name __pycache__ -type d -prune -exec rm -rf {} +

az acr login -n ca72f0c87afdacr
docker buildx create --name vpcbuilder --driver docker-container --use 2>/dev/null || docker buildx use vpcbuilder
docker buildx build --platform linux/amd64 \
  -t ca72f0c87afdacr.azurecr.io/visiting-place-clusterer:v1 \
  --push /tmp/vpc-build
```

### Create the Container App

```bash
ACR_PWD=$(az acr credential show -n ca72f0c87afdacr --query "passwords[0].value" -o tsv)
DOMAIN=$(az containerapp env show -n polimi-cloud-env -g polimi-cloud --query properties.defaultDomain -o tsv)

az containerapp create \
  --name visiting-place-clusterer -g polimi-cloud \
  --environment polimi-cloud-env \
  --image ca72f0c87afdacr.azurecr.io/visiting-place-clusterer:v1 \
  --target-port 8000 --ingress external \
  --registry-server ca72f0c87afdacr.azurecr.io \
  --registry-username ca72f0c87afdacr --registry-password "$ACR_PWD" \
  --min-replicas 1 --max-replicas 1 --cpu 0.5 --memory 1.0Gi \
  --secrets azure-openai-key="<AZURE_OPENAI_API_KEY>" \
  --env-vars APP_MODE=a2a PROVIDER=azure \
    AZURE_OPENAI_ENDPOINT=https://visiting-place-clusterer.openai.azure.com \
    AZURE_OPENAI_DEPLOYMENT=gpt-4o-mini \
    AZURE_OPENAI_API_VERSION=2024-12-01-preview \
    AZURE_OPENAI_API_KEY=secretref:azure-openai-key \
    "PUBLIC_URL=https://visiting-place-clusterer.$DOMAIN"
```

### Redeploy / verify

```bash
docker buildx build --platform linux/amd64 \
  -t ca72f0c87afdacr.azurecr.io/visiting-place-clusterer:v2 --push /tmp/vpc-build
az containerapp update -n visiting-place-clusterer -g polimi-cloud \
  --image ca72f0c87afdacr.azurecr.io/visiting-place-clusterer:v2

BASE=https://visiting-place-clusterer.icypond-686fddf2.uksouth.azurecontainerapps.io
curl "$BASE/health"
curl "$BASE/.well-known/agent-card.json"
az containerapp logs show -n visiting-place-clusterer -g polimi-cloud --tail 50
```

### Optional follow-ups

- Managed identity for Azure OpenAI and ACR pull (`--registry-identity system`).
- Scale to zero when idle with `--min-replicas 0`, or tear down with `az group delete -n polimi-cloud`.
