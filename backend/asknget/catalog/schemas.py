"""Schemas for the public catalog, reviews and wishlist.

Screens: Landing Page, Categories (listing + filter sidebar), Product Details
(Description / How to Use / Reviews tabs), My Account – Wishlist.
"""

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from ninja import Field, FilterSchema, Schema
from pydantic import model_validator

from core.validators import EmailStr, NonEmptyStr

SortLit = Literal["relevance", "new", "price_asc", "price_desc", "rating", "popular"]
ReviewSortLit = Literal["new", "top", "low"]

# --------------------------------------------------------------------------- #
# Lookups
# --------------------------------------------------------------------------- #


class CategoryOut(Schema):
    """One node of the tree; `product_count` feeds '128 items' in the sidebar."""

    id: UUID
    name: str
    slug: str
    image_url: Optional[str] = None
    product_count: int
    children: list["CategoryOut"] = []


class CategoryRefOut(Schema):
    id: UUID
    name: str
    slug: str


class BrandOut(Schema):
    id: UUID
    name: str
    slug: str
    logo_url: Optional[str] = None


class StoreRefOut(Schema):
    id: UUID
    display_name: str
    slug: str


class ColorFacetOut(Schema):
    value: str
    swatch_hex: str


class ProductFiltersOut(Schema):
    """Everything the 'Filters Options' sidebar needs in one call."""

    categories: list[CategoryOut]
    brands: list[BrandOut]
    colors: list[ColorFacetOut]
    price_min_cents: int
    price_max_cents: int
    currency: str


# --------------------------------------------------------------------------- #
# Products
# --------------------------------------------------------------------------- #


class ProductQuery(FilterSchema):
    """Query string for GET /products. Every field optional; combine freely."""

    q: Optional[str] = Field(None, max_length=120, description="'Search Products' — hybrid keyword + semantic ranking")
    category: Optional[str] = Field(None, description="Category slug; includes descendants")
    brand: Optional[list[str]] = Field(None, description="Brand slugs (repeat the param)")
    color: Optional[list[str]] = Field(None, description="Colour option values, e.g. color=Black&color=Red")
    price_min: Optional[int] = Field(None, ge=0, description="Cents")
    price_max: Optional[int] = Field(None, ge=0, description="Cents")
    in_stock: Optional[bool] = Field(None, description="true → In Stock only, false → Out of Stock only")
    store: Optional[str] = Field(None, description="Store slug")
    featured: Optional[bool] = None
    best_seller: Optional[bool] = None
    new_arrival: Optional[bool] = None
    sort: Optional[SortLit] = Field(None, description="Default: relevance when q is given, otherwise new")

    @model_validator(mode="after")
    def _price_range(self):
        if self.price_min is not None and self.price_max is not None and self.price_min > self.price_max:
            raise ValueError("price_min must be ≤ price_max")
        return self


class ProductCardOut(Schema):
    """Product tile used on Landing, Categories, Wishlist and 'You may also like'.
    Serialises straight from a Product instance (resolvers below)."""

    id: UUID
    slug: str
    name: str
    category: CategoryRefOut
    brand: Optional[str] = None
    price_cents: int
    compare_at_price_cents: Optional[int] = None
    discount_percent: Optional[int] = Field(None, description="'40% Off' badge")
    currency: str
    image_url: Optional[str] = None
    stock_status: str
    rating_avg: float
    rating_count: int
    is_wishlisted: Optional[bool] = Field(None, description="Only present when the caller is signed in")

    @staticmethod
    def resolve_brand(obj):
        return obj.brand.name if obj.brand_id else None

    @staticmethod
    def resolve_image_url(obj):
        images = list(obj.images.all())
        primary = next((i for i in images if i.is_primary), images[0] if images else None)
        return primary.image.url if primary else None

    @staticmethod
    def resolve_is_wishlisted(obj, context):
        from . import services

        ids = services.request_wishlist_ids(context.get("request"))
        return (obj.id in ids) if ids is not None else None


class ProductImageOut(Schema):
    id: UUID
    url: str = Field(alias="image.url")
    alt: str
    is_primary: bool


class OptionValueOut(Schema):
    id: UUID
    value: str
    swatch_hex: str


class OptionTypeOut(Schema):
    """'Select Size' / 'Choose your shade'."""

    id: UUID
    name: str
    values: list[OptionValueOut]


class VariantOut(Schema):
    id: UUID
    sku: str
    option_value_ids: list[UUID]
    price_cents: int = Field(alias="effective_price_cents")
    compare_at_price_cents: Optional[int] = None
    stock_qty: int
    in_stock: bool

    @staticmethod
    def resolve_option_value_ids(obj):
        return [v.id for v in obj.option_values.all()]

    @staticmethod
    def resolve_compare_at_price_cents(obj):
        return obj.compare_at_price_cents if obj.price_cents is not None else obj.product.compare_at_price_cents

    @staticmethod
    def resolve_in_stock(obj):
        return obj.stock_qty > 0


class ContentSectionOut(Schema):
    title: str
    body: str


class RatingBreakdownOut(Schema):
    average: float
    count: int
    distribution: dict[int, int] = Field(description="{5: n, 4: n, 3: n, 2: n, 1: n}")


class ProductDetailOut(ProductCardOut):
    short_description: str
    description_title: str
    description: str
    images: list[ProductImageOut]
    option_types: list[OptionTypeOut]
    variants: list[VariantOut]
    key_benefits: list[ContentSectionOut]
    usage_steps: list[ContentSectionOut]
    store: StoreRefOut
    ratings: RatingBreakdownOut

    @staticmethod
    def resolve_key_benefits(obj):
        return [s for s in obj.content_sections.all() if s.section == "benefit"]

    @staticmethod
    def resolve_usage_steps(obj):
        return [s for s in obj.content_sections.all() if s.section == "usage_step"]

    @staticmethod
    def resolve_ratings(obj):
        from . import services

        return services.rating_breakdown(obj)


# --------------------------------------------------------------------------- #
# Reviews
# --------------------------------------------------------------------------- #


class ReviewMediaOut(Schema):
    id: UUID
    url: str = Field(alias="file.url")
    type: str


class ReviewOut(Schema):
    id: UUID
    reviewer_name: str = Field(description="Abbreviated: 'Ayesha K.'")
    rating: int
    body: str
    is_verified_purchase: bool
    media: list[ReviewMediaOut]
    created_at: datetime

    @staticmethod
    def resolve_reviewer_name(obj):
        from .services import abbreviate

        return abbreviate(obj.reviewer_name)


class ReviewIn(Schema):
    """'Add your review' (multipart: send this as form fields + optional `files`)."""

    name: Optional[NonEmptyStr] = Field(None, max_length=120, description="Required for guests; defaults to your profile name")
    email: Optional[EmailStr] = Field(None, description="Required for guests; never published")
    rating: int = Field(ge=1, le=5)
    body: NonEmptyStr = Field(max_length=4000)


class ReviewCreatedOut(Schema):
    id: UUID
    status: str
    detail: str


# --------------------------------------------------------------------------- #
# Wishlist
# --------------------------------------------------------------------------- #


class WishlistItemOut(Schema):
    product: ProductCardOut
    added_at: datetime



# --------------------------------------------------------------------------- #
# Category requests (seller asks, staff decide)
# --------------------------------------------------------------------------- #


class CategoryRefOut(Schema):
    id: UUID
    name: str


class CategoryLinkOut(Schema):
    id: UUID
    name: str
    slug: str


class CategoryRequestIn(Schema):
    name: str = Field(min_length=2, max_length=100, description="'Category Name'")
    reason: str = Field(min_length=3, max_length=250, description="'Why do you need this category?' (0/250)")
    parent_id: Optional[UUID] = Field(None, description="'Parent Category' (a top-level one); empty asks for a new top-level category")


class CategoryRequestOut(Schema):
    id: UUID
    name: str
    reason: str
    status: str = Field(description="pending | approved | rejected | withdrawn")
    parent: Optional[CategoryRefOut]
    category: Optional[CategoryLinkOut] = Field(description="Once approved: the category to use (new, or one that already existed)")
    created_category: bool
    review_note: str = Field(description="Why it was rejected, or the reviewer's note")
    document_url: Optional[str] = Field(description="Signed link to the supporting image; expires, fetch the request again for a new one")
    document_name: str
    created_at: datetime
    reviewed_at: Optional[datetime]


class CategoryRequestStoreOut(Schema):
    id: UUID
    name: str


class CategoryRequestAdminOut(CategoryRequestOut):
    store: CategoryRequestStoreOut
    requested_by: str
    existing_category: Optional[CategoryLinkOut] = Field(description="While pending: a category that already has this name, to approve onto")


class CategoryExistsOut(Schema):
    detail: str = Field(description="category_exists")
    category: CategoryLinkOut
    is_active: bool


class CategoryApproveIn(Schema):
    name: Optional[str] = Field(None, min_length=2, max_length=100, description="Create it under this name instead of the requested one")
    parent_id: Optional[UUID] = Field(None, description="Put it under this top-level category instead (send it, even null, only to change the parent)")
    use_category_id: Optional[UUID] = Field(None, description="Do not create anything: give the seller this existing category")
    note: str = Field("", max_length=500)


class CategoryRejectIn(Schema):
    reason: str = Field(min_length=3, max_length=500, description="Shown to the seller")
