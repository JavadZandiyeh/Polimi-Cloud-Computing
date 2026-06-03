from __future__ import annotations

import contextvars
from collections.abc import Callable
from enum import Enum
from pathlib import Path

from fasta2a import FastA2A
from fasta2a.pydantic_ai import agent_to_a2a
from pydantic_ai import Agent
from pydantic_ai.models import Model
from starlette.applications import Starlette

from clustering_service import ClusteringService
from environment_service import env
from schemas import Place, PlaceCluster, PlaceClustererOutput
from utils import FileStore


class AgentEnum(str, Enum):
    PLACE_CLUSTERER = "place_clusterer"


# Holds the day-clusters produced by the most recent `cluster_places` call so the
# final output can be rebuilt server-side from the exact place objects the tool
# grouped. The model therefore never re-serialises places into the output (which
# previously dropped fields such as `description` and failed output validation).
# Safe because the A2A worker processes tasks sequentially within a single task, and
# `build_clusterer_output` clears the value at the end of every run.
_clusters_var: contextvars.ContextVar[list[list[Place]] | None] = (
    contextvars.ContextVar("vpc_clusters", default=None)
)


def cluster_places(places: list[Place], num_days: int) -> dict[str, list[str]]:
    """Group places into geographically coherent day-clusters using K-means.

    Pass the full list of places to visit and the number of trip days. The grouping
    is stored and attached to the final result automatically — every place is kept
    exactly as given and assigned to exactly one day, with none added, dropped, or
    modified. You receive a compact `day -> place names` map so you can describe how
    the trip was split across days; you do not need to repeat the places yourself.
    """
    clusters = ClusteringService.cluster(places, num_days)
    _clusters_var.set(clusters)
    return {
        f"day_{day}": [place.name for place in group]
        for day, group in enumerate(clusters, start=1)
    }


def build_clusterer_output(description: str) -> PlaceClustererOutput:
    """Assemble and return the final clusterer result.

    Provide only the natural-language `description`. The day-clusters from the most
    recent `cluster_places` call are attached automatically with every place
    preserved exactly. If `cluster_places` was not called (required information was
    missing), the result has empty `clusters`, so the `description` must explain what
    is still needed.
    """
    clusters = _clusters_var.get()
    _clusters_var.set(None)  # reset so a later task cannot inherit this grouping
    return PlaceClustererOutput(
        description=description,
        clusters=[
            PlaceCluster(day=day, places=group)
            for day, group in enumerate(clusters or [], start=1)
        ],
    )


class AgentHandle:
    """Runtime wrapper around a built pydantic-ai agent."""

    def __init__(
        self,
        agent_enum: AgentEnum,
        agent: Agent[None, PlaceClustererOutput],
    ) -> None:
        self.agent_enum = agent_enum
        self.agent = agent

    @property
    def a2a(self) -> FastA2A:
        """Expose the agent as a FastA2A ASGI app for orchestrator integration."""
        return agent_to_a2a(self.agent, **AgentService.get_agent_card(self.agent_enum))

    @property
    def web(self) -> Starlette:
        """Expose the agent as a Starlette ASGI app serving a web chat UI."""
        return self.agent.to_web()


class AgentService:
    """Loads agent configuration and builds pydantic-ai agents."""

    _FILES_DIR = Path(__file__).parent / "files"

    @staticmethod
    def get_system_prompt(agent: AgentEnum) -> str:
        """Return the system prompt for the given agent."""
        data = FileStore.load_yaml(AgentService._FILES_DIR / "system_prompts.yml")
        return data[agent.value]

    @staticmethod
    def get_agent_card(agent: AgentEnum) -> dict:
        """Return the A2A agent card for the given agent."""
        data = FileStore.load_yaml(AgentService._FILES_DIR / "agent_cards.yml")
        card = dict(data[agent.value])
        card["url"] = env.public_url
        return card

    @staticmethod
    def get_output_type(agent: AgentEnum) -> Callable[..., PlaceClustererOutput]:
        """Return the output function the agent calls to produce its result.

        Using a function (rather than a bare schema) lets the result be assembled
        server-side from the grouping computed by the tool, so the model only
        supplies the summary `description` and never re-serialises the places.
        """
        match agent:
            case AgentEnum.PLACE_CLUSTERER:
                return build_clusterer_output
            case _:
                raise ValueError(f"Invalid agent: {agent.value}")

    @staticmethod
    def get_tools(agent: AgentEnum) -> list:
        """Return the function tools registered for the given agent."""
        match agent:
            case AgentEnum.PLACE_CLUSTERER:
                return [cluster_places]
            case _:
                raise ValueError(f"Invalid agent: {agent.value}")

    @staticmethod
    def create(
        agent_enum: AgentEnum,
        model: Model,
    ) -> AgentHandle:
        """Build a pydantic-ai agent and return a handle to expose it."""
        agent: Agent[None, PlaceClustererOutput] = Agent(
            model=model,
            system_prompt=AgentService.get_system_prompt(agent_enum),
            tools=AgentService.get_tools(agent_enum),
            output_type=AgentService.get_output_type(agent_enum),
        )
        return AgentHandle(agent_enum=agent_enum, agent=agent)
