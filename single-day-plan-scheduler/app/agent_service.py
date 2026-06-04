from __future__ import annotations

from enum import Enum
from pathlib import Path

from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from google.adk.agents import LlmAgent, SequentialAgent
from google.adk.agents.base_agent import BaseAgent
from google.adk.a2a.utils.agent_to_a2a import to_a2a
from google.adk.models.lite_llm import LiteLlm
from pydantic import BaseModel
from starlette.applications import Starlette

from environment_service import env
from schemas import DaySchedulingResult
from utils import FileStore


class AgentEnum(str, Enum):
    PLAN_SCHEDULER = "plan_scheduler"


class AgentHandle:
    """Runtime wrapper around a built Google ADK agent."""

    def __init__(self, agent_enum: AgentEnum, agent: BaseAgent) -> None:
        self.agent_enum = agent_enum
        self.agent = agent

    @property
    def a2a(self) -> Starlette:
        """Expose the agent as an A2A ASGI app for orchestrator integration."""
        return to_a2a(
            self.agent,
            agent_card=AgentService.get_agent_card(self.agent_enum),
        )

    @property
    def web(self) -> Starlette:
        """Web chat UI is not provided by ADK at the per-agent ASGI level."""
        raise NotImplementedError(
            "single-day-plan-scheduler only supports A2A mode; "
            "use the Google ADK `adk web` CLI for an interactive UI."
        )


class AgentService:
    """Loads agent configuration and builds Google ADK agents."""

    _FILES_DIR = Path(__file__).parent / "files"

    @staticmethod
    def _load_prompt(key: str) -> str:
        """Return the prompt stored under `key` in system_prompts.yml."""
        data = FileStore.load_yaml(AgentService._FILES_DIR / "system_prompts.yml")
        return data[key]

    @staticmethod
    def get_system_prompt(agent: AgentEnum) -> str:
        """Return the system prompt (instruction) for the given agent."""
        return AgentService._load_prompt(agent.value)

    @staticmethod
    def get_agent_card(agent: AgentEnum) -> AgentCard:
        """Return the A2A agent card for the given agent."""
        data = FileStore.load_yaml(AgentService._FILES_DIR / "agent_cards.yml")
        card = dict(data[agent.value])
        skills = [AgentSkill(**skill) for skill in card.get("skills", [])]
        return AgentCard(
            name=card["name"],
            description=card["description"],
            version=card["version"],
            url=env.public_url,
            provider=card.get("provider"),
            capabilities=AgentCapabilities(),
            default_input_modes=card.get(
                "input_modes", ["application/json", "text/plain"]
            ),
            default_output_modes=card.get("output_modes", ["application/json"]),
            skills=skills,
        )

    @staticmethod
    def get_output_type(agent: AgentEnum) -> type[BaseModel]:
        """Return the structured output schema for the given agent."""
        match agent:
            case AgentEnum.PLAN_SCHEDULER:
                return DaySchedulingResult
            case _:
                raise ValueError(f"Invalid agent: {agent.value}")

    @staticmethod
    def create(
        agent_enum: AgentEnum,
        model: LiteLlm | str,
        toolsets: list | None = None,
        tools: list | None = None,
    ) -> AgentHandle:
        """Build the scheduler agent and return a handle to expose it.

        Google ADK cannot reliably combine `output_schema` with `tools` on the same
        LlmAgent: when an output schema is enforced (via the model's response_format on
        the LiteLLM/OpenAI path), the model is pushed to emit the final JSON immediately
        and skips the tool calls — so route_estimate and recommend_restaurants never run.

        To get BOTH tool use and a structured result, we use a SequentialAgent:
          1. planner   — has the tools, NO output_schema, so it freely calls
             route_estimate and recommend_restaurants and writes the full day plan as
             JSON text into state["raw_day_plan"].
          2. formatter — has the output_schema and NO tools, so it just converts the
             planner's JSON into the strict DaySchedulingResult that is returned to A2A.
        """
        description = AgentService.get_agent_card(agent_enum).description

        planner = LlmAgent(
            model=model,
            name=f"{agent_enum.value}_planner",
            description=description,
            instruction=AgentService.get_system_prompt(agent_enum),
            tools=[*(tools or []), *(toolsets or [])],
            output_key="raw_day_plan",
        )
        formatter = LlmAgent(
            model=model,
            name=f"{agent_enum.value}_formatter",
            description=description,
            instruction=AgentService._load_prompt(f"{agent_enum.value}_formatter"),
            output_schema=AgentService.get_output_type(agent_enum),
            output_key="day_plan",
        )
        agent = SequentialAgent(
            name=agent_enum.value,
            description=description,
            sub_agents=[planner, formatter],
        )
        return AgentHandle(agent_enum=agent_enum, agent=agent)
