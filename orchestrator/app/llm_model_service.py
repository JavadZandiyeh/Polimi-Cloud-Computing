from __future__ import annotations

from strands.models import Model

from environment_service import ModelEnum, ProviderEnum, env


class LlmModelService:
    """Builds Strands Agents SDK models for configured providers."""

    @staticmethod
    def create(provider: ProviderEnum, model: ModelEnum) -> Model:
        """Build and return a Strands Model for the given provider and model name."""
        model_name = model.value

        match provider:
            case ProviderEnum.OPENAI:
                from strands.models.openai import OpenAIModel

                return OpenAIModel(
                    client_args={"api_key": env.openai_api_key},
                    model_id=model_name,
                )
            case ProviderEnum.BEDROCK:
                from strands.models import BedrockModel

                return BedrockModel(
                    model_id=model_name,
                    region_name=env.aws_region,
                )
            case _:
                raise ValueError(f"Unsupported LLM provider: {provider.value}")
