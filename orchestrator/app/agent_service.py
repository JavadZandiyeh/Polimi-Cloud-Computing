from __future__ import annotations

from enum import Enum
from pathlib import Path

from starlette.applications import Starlette
from strands import Agent
from strands.models import Model

from utils import FileStore


class AgentEnum(str, Enum):
    ORCHESTRATOR = "orchestrator"


class AgentHandle:
    """Runtime wrapper around a configured Strands orchestrator agent.

    The web UI keeps one Strands `Agent` per chat session, so this handle exposes a
    factory (`build_agent`) rather than a single shared agent. Each session gets a fresh
    agent that retains its own conversation history.
    """

    def __init__(
        self,
        agent_enum: AgentEnum,
        model: Model,
        system_prompt: str,
        tools: list,
    ) -> None:
        self.agent_enum = agent_enum
        self._model = model
        self._system_prompt = system_prompt
        self._tools = tools

    def build_agent(self) -> Agent:
        """Create a fresh Strands agent with its own (empty) conversation history."""
        return Agent(
            model=self._model,
            system_prompt=self._system_prompt,
            tools=list(self._tools),
        )

    @property
    def web(self) -> Starlette:
        """Expose the orchestrator as a Starlette app serving the chat web UI."""
        from web_service import create_web_app

        return create_web_app(
            agent_factory=self.build_agent,
            agent_card=AgentService.get_agent_card(self.agent_enum),
        )

    @property
    def a2a(self) -> Starlette:
        """A2A server exposure is out of scope — the orchestrator is the top of the tree."""
        raise NotImplementedError(
            "orchestrator only supports web mode; set APP_MODE=web. It consumes the "
            "other agents over A2A but does not expose an A2A server itself."
        )


class AgentService:
    """Loads agent configuration and builds Strands orchestrator agents."""

    _FILES_DIR = Path(__file__).parent / "files"

    @staticmethod
    def get_system_prompt(agent: AgentEnum) -> str:
        """Return the system prompt for the given agent."""
        data = FileStore.load_yaml(AgentService._FILES_DIR / "system_prompts.yml")
        return data[agent.value]

    @staticmethod
    def get_agent_card(agent: AgentEnum) -> dict:
        """Return the agent card for the given agent."""
        data = FileStore.load_yaml(AgentService._FILES_DIR / "agent_cards.yml")
        return dict(data[agent.value])

    @staticmethod
    def create(
        agent_enum: AgentEnum,
        model: Model,
        tools: list | None = None,
    ) -> AgentHandle:
        """Configure a Strands orchestrator agent and return a handle to expose it."""
        return AgentHandle(
            agent_enum=agent_enum,
            model=model,
            system_prompt=AgentService.get_system_prompt(agent_enum),
            tools=tools or [],
        )
