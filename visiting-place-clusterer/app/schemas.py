from __future__ import annotations

from pydantic import BaseModel, Field


class GpsCoordinates(BaseModel):
    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)


class Place(BaseModel):
    """Minimal place representation used for clustering — only the fields needed."""

    title: str = Field(description="The display name of the place.")
    gps_coordinates: GpsCoordinates | None = Field(
        default=None, description="Latitude/longitude used for geographic clustering."
    )
    type: list[str] | None = Field(
        default=None, description="Category labels used to estimate visit duration."
    )


class PlaceCluster(BaseModel):
    """A geographically coherent group of place titles intended for a single trip day."""

    day: int = Field(
        description=(
            "The 1-based day index this cluster is planned for. Starts at 1 and "
            "increases by 1 with no gaps."
        ),
        ge=1,
    )
    places: list[str] = Field(
        description="Titles of the places assigned to this day, in no particular order."
    )


class PlaceClustererOutput(BaseModel):
    """Structured output for the visiting place clusterer agent."""

    description: str = Field(
        description=(
            "A natural-language summary of the clustering outcome. When `clusters` "
            "is non-empty, briefly describe how the places were grouped across the "
            "trip days. When `clusters` is empty, clearly state which required "
            "information is missing (e.g. the list of places or the number of days)."
        )
    )
    clusters: list[PlaceCluster] = Field(
        default_factory=list,
        description=(
            "One entry per trip day, each holding the titles of places assigned to "
            "that day. Empty when required information is missing."
        ),
    )
