from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class MealSlot(str, Enum):
    breakfast = "breakfast"
    lunch = "lunch"
    dinner = "dinner"


class Location(BaseModel):
    """Geographic coordinates with an optional human-readable address."""

    latitude: float
    longitude: float
    address: Optional[str] = None


class RestaurantCandidate(BaseModel):
    """A restaurant candidate returned by the food recommender."""

    id: str = Field(description="Google Places place_id or a generated UUID.")
    name: str = Field(description="The display name of the restaurant.")
    location: Location = Field(
        description="Geographic coordinates and address of the restaurant."
    )
    price_level: Optional[int] = Field(
        None,
        ge=0,
        le=4,
        description=(
            "Google Places price scale: 0 (free) → 4 (most expensive). "
            "Frequently absent, as many places carry no price level."
        ),
    )
    cuisines: Optional[list[str]] = Field(
        None,
        description=(
            "Best-effort venue labels derived from the Places 'types' field "
            "(e.g. 'Bar', 'Café', 'Bakery'). Usually empty for restaurants, since "
            "the Nearby Search API does not expose specific cuisines."
        ),
    )
    rating: Optional[float] = Field(
        None,
        ge=0,
        le=5,
        description="Average user rating on a 0–5 scale.",
    )
    description: str = Field(
        default="",
        description=(
            "A short rationale explaining why this restaurant was selected and ranked here, "
            "grounded in the meal slot, budget, rating, price level, and any stated preferences."
        ),
    )


class FoodRecommenderInput(BaseModel):
    """Input schema expected by the food recommender agent (sent as JSON by the orchestrator)."""

    timeofday: MealSlot
    searchcenter: Location
    searchradiusmeters: int = Field(1000, ge=100, le=50_000)
    budgetpermealperperson: Optional[float] = None
    preferences: Optional[list[str]] = None


class FoodRecommenderOutput(BaseModel):
    """Structured output for the food recommender agent."""

    description: str = Field(
        description=(
            "A natural-language summary of the outcome. When `restaurantcandidates` is non-empty, "
            "briefly confirm the search results (meal slot, area, count). When "
            "`restaurantcandidates` is empty, clearly state which required information is missing "
            "(meal slot or search location) or why no restaurants were found."
        )
    )
    restaurantcandidates: list[RestaurantCandidate] = Field(
        default_factory=list,
        description=(
            "The ranked list of restaurant candidates, sorted by rating descending "
            "then by price_level ascending. Empty when the search yields no results "
            "or required information is missing."
        ),
    )
