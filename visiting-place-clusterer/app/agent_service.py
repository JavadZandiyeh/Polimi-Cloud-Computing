from __future__ import annotations

from enum import Enum
from pathlib import Path

from fasta2a import FastA2A
from fasta2a.pydantic_ai import agent_to_a2a
from pydantic_ai import Agent
from pydantic_ai.models import Model
from starlette.applications import Starlette

from clustering_service import ClusteringService
from environment_service import env
from schemas import Place, PlaceClustererOutput
from utils import FileStore


class AgentEnum(str, Enum):
    PLACE_CLUSTERER = "place_clusterer"


def cluster_places(places: list[Place], num_days: int) -> dict[str, list[str]]:
    """Group places into geographically coherent day-clusters using K-means.

    Pass the full list of places to visit and the number of trip days.
    Returns a compact `day -> place titles` map. Use this map to fill in the
    `clusters` field of the final output — each day's list of titles goes directly
    into the corresponding cluster. Do not invent or drop any place titles.
    """
    clusters = ClusteringService.cluster(places, num_days)
    return {
        f"day_{day}": [place.title for place in group]
        for day, group in enumerate(clusters, start=1)
    }


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
            output_type=PlaceClustererOutput,
        )
        return AgentHandle(agent_enum=agent_enum, agent=agent)
