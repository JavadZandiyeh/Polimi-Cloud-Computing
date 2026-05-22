from __future__ import annotations

import logfire
from typing import Any

from agent import AgentFactory
from config import AgentEnum, AppModeEnum, ModelEnum, ProviderEnum, settings
from llm import create_model
from mcps import create_google_maps_mcp


def _add_health_route(asgi_app: Any) -> Any:
    """Wrap an ASGI app with a /health endpoint that always returns 200 ok."""

    async def app(scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") == "http" and scope.get("path") == "/health":
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})
            return
        await asgi_app(scope, receive, send)

    return app


if settings.logfire_token:
    logfire.configure(token=settings.logfire_token)
    logfire.instrument_pydantic_ai()

model = create_model(ProviderEnum.OPENAI, ModelEnum.GPT_5_4_MINI.value)

google_maps_mcp = create_google_maps_mcp()

agent_factory = AgentFactory(
    agent_enum=AgentEnum.PLACE_RECOMMENDER,
    model=model,
    toolsets=[google_maps_mcp],
)

_base_app = (
    agent_factory.a2a if settings.app_mode == AppModeEnum.A2A else agent_factory.web
)
app = _add_health_route(_base_app)
