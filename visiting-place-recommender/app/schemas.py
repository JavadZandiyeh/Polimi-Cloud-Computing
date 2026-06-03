from __future__ import annotations

from pydantic import BaseModel, Field


class GpsCoordinates(BaseModel):
    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)


class PlaceHours(BaseModel):
    monday: str | None = None
    tuesday: str | None = None
    wednesday: str | None = None
    thursday: str | None = None
    friday: str | None = None
    saturday: str | None = None
    sunday: str | None = None


class PlaceLinks(BaseModel):
    website: str | None = None
    photos: str | None = None
    reviews: str | None = None


class Place(BaseModel):
    title: str = Field(description="The display name of the place.")
    data_id: str | None = Field(
        default=None, description="Alternative place identifier."
    )
    data_cid: str | None = Field(
        default=None, description="Numeric Google Customer ID (CID / ludocid)."
    )
    address: str | None = Field(default=None, description="Full street address.")
    gps_coordinates: GpsCoordinates | None = Field(
        default=None, description="Latitude/longitude of the place."
    )
    phone: str | None = Field(default=None, description="Contact phone number.")
    description: str | None = Field(
        default=None, description="Place description as returned by the API."
    )
    rating: float | None = Field(
        default=None, ge=0.0, le=5.0, description="Average star rating out of 5."
    )
    reviews: int | None = Field(
        default=None, ge=0, description="Total number of user reviews."
    )
    open_state: str | None = Field(
        default=None,
        description="Current operational status, e.g. 'Open' or 'Closes soon'.",
    )
    hours: PlaceHours | None = Field(
        default=None, description="Weekly operating hours keyed by day name."
    )
    price: str | None = Field(
        default=None, description="Price range indicator, e.g. '$', '$$', '$$$'."
    )
    type: list[str] | None = Field(
        default=None, description="Category labels for the place."
    )
    thumbnail: str | None = Field(
        default=None, description="URL of the primary thumbnail image."
    )
    links: PlaceLinks | None = Field(
        default=None, description="Website, photos, and reviews links."
    )
    rank: int = Field(
        ge=0,
        description="0-based position in the ranked list (0 = highest priority, no gaps).",
    )


class PlaceRecommenderOutput(BaseModel):
    """Structured output for the place recommender agent."""

    description: str = Field(
        description=(
            "A natural-language summary of the recommendation outcome. When "
            "`places` is non-empty, briefly describe the overall selection rationale. "
            "When `places` is empty, clearly state which required information is "
            "missing or why no suitable places were found."
        )
    )
    places: list[Place] = Field(
        default_factory=list,
        min_length=0,
        max_length=10,
        description=(
            "The ranked list of recommended places to visit in the destination "
            "city. Empty when required information is missing or no suitable "
            "matches were found."
        ),
    )
