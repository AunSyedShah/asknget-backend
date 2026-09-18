"""Schemas for search suggestions and recommendation rails."""

from typing import Literal, Optional
from uuid import UUID

from ninja import Field, Schema

from catalog.schemas import ProductCardOut


class SuggestionOut(Schema):
    label: str
    slug: str
    image_url: Optional[str] = None


class SuggestOut(Schema):
    """Search-bar dropdown: a few products, brands and categories matching the prefix."""

    products: list[SuggestionOut]
    brands: list[SuggestionOut]
    categories: list[SuggestionOut]


class SeedOut(Schema):
    id: UUID
    name: str
    slug: str


class RecommendationsOut(Schema):
    strategy: Literal["personalised", "trending"] = Field(..., description="'trending' until the shopper has some history")
    seeds: list[SeedOut] = Field([], description="Products the personalised picks were derived from ('Because you viewed…')")
    items: list[ProductCardOut]
