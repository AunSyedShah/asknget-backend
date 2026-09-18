"""Seller portal schemas (Figma: Seller Dashboard, Products, Inventory, Orders, Customers).

Every KPI is {value, previous, change_pct} so cards can render "12.8% vs. previous period".
"""

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from ninja import Field, FilterSchema, Schema
from pydantic import field_validator, model_validator

from core.validators import NonEmptyStr

PeriodLit = Literal["today", "week", "month", "quarter", "year"]


def clean_tags(v):
    if v is None:
        return v
    seen, out = set(), []
    for t in v:
        t = t.strip()[:40]
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out


class Kpi(Schema):
    value: float
    previous: Optional[float] = None
    change_pct: Optional[float] = Field(None, description="vs previous period; null when there is nothing to compare")


class SeriesPoint(Schema):
    label: str
    values: dict[str, float]


# --------------------------------------------------------------------------- #
# Products
# --------------------------------------------------------------------------- #

ProductTabLit = Literal["all", "active", "pending", "draft", "rejected", "archived"]
StockTabLit = Literal["in", "low", "out"]
ProductSortLit = Literal["updated", "created", "price", "price_desc", "sold", "name"]


class SellerProductQuery(FilterSchema):
    status: ProductTabLit = "all"
    q: Optional[str] = Field(None, max_length=120, description="'Search products...' (name, SKU, tag)")
    brand: Optional[list[str]] = Field(None, description="Brand slugs")
    category: Optional[str] = Field(None, description="Category slug (includes descendants)")
    stock: Optional[StockTabLit] = None
    sort: ProductSortLit = "updated"


class SellerProductRowOut(Schema):
    """One row of the Products table."""

    id: UUID
    name: str
    slug: str
    image_url: Optional[str] = None
    category: Optional[str] = None
    brand: Optional[str] = None
    stock_qty: int
    stock_status: str
    updated_at: datetime
    price_cents: int
    compare_at_price_cents: Optional[int] = None
    currency: str
    sold_qty: int
    earned_cents: int
    status: str


class ProductTabsOut(Schema):
    all: int
    active: int
    pending: int
    draft: int
    rejected: int
    archived: int


class SellerProductKpisOut(Schema):
    total_products: Kpi
    new_added: Kpi
    sold_qty: Kpi = Field(..., description="'Total Purchase' — units sold in the period")
    returned_qty: Kpi
    period: str
    tabs: ProductTabsOut


class ProductCreateIn(Schema):
    """Wizard step 1 — 'Save & Continue' creates the draft."""

    name: NonEmptyStr = Field(max_length=200)
    category_id: UUID = Field(..., description="The leaf: 'Subcategory' when one is chosen, else 'Category'")
    brand_id: Optional[UUID] = None
    tags: list[str] = Field([], max_length=20)
    short_description: str = Field("", max_length=250, description="'Product Description' 0/250")

    @field_validator("tags")
    @classmethod
    def _tags(cls, v):
        return clean_tags(v)


class ProductPatchIn(Schema):
    """Any editable field; the wizard sends whichever step it is on (autosave)."""

    name: Optional[NonEmptyStr] = Field(None, max_length=200)
    category_id: Optional[UUID] = None
    brand_id: Optional[UUID] = None
    tags: Optional[list[str]] = Field(None, max_length=20)
    short_description: Optional[str] = Field(None, max_length=250)
    description_title: Optional[str] = Field(None, max_length=200)
    description: Optional[str] = Field(None, max_length=10000)
    price_cents: Optional[int] = Field(None, ge=0)
    compare_at_price_cents: Optional[int] = Field(None, ge=0, description="Send 0 to clear")
    currency: Optional[str] = Field(None, min_length=3, max_length=3)
    low_stock_threshold: Optional[int] = Field(None, ge=0)
    preorder: Optional[bool] = Field(None, description="Manual 'Pre-order' flag; otherwise stock status is derived")
    weight_grams: Optional[int] = Field(None, ge=0)
    length_mm: Optional[int] = Field(None, ge=0)
    width_mm: Optional[int] = Field(None, ge=0)
    height_mm: Optional[int] = Field(None, ge=0)
    ships_from: Optional[str] = Field(None, max_length=120)
    handling_days: Optional[int] = Field(None, ge=0, le=30)
    is_featured: Optional[bool] = None
    is_new_arrival: Optional[bool] = None

    @field_validator("tags")
    @classmethod
    def _tags(cls, v):
        return clean_tags(v)


class OptionValueIn(Schema):
    value: NonEmptyStr = Field(max_length=80)
    swatch_hex: str = Field("", pattern=r"^(#[0-9A-Fa-f]{6})?$")


class OptionTypeIn(Schema):
    name: NonEmptyStr = Field(max_length=50)
    values: list[OptionValueIn] = Field(min_length=1, max_length=50)


class OptionsIn(Schema):
    """Wizard step 3 — option types; variants are generated for every combination.
    Existing variants that still match a combination keep their SKU, price and stock."""

    options: list[OptionTypeIn] = Field([], max_length=3)

    @model_validator(mode="after")
    def _unique(self):
        names = [o.name.lower() for o in self.options]
        if len(names) != len(set(names)):
            raise ValueError("Option names must be unique")
        for o in self.options:
            vals = [v.value.lower() for v in o.values]
            if len(vals) != len(set(vals)):
                raise ValueError(f"Duplicate values in option '{o.name}'")
        return self


class VariantPatchIn(Schema):
    sku: Optional[NonEmptyStr] = Field(None, max_length=64)
    price_cents: Optional[int] = Field(None, ge=0, description="Send 0 to inherit the product price")
    compare_at_price_cents: Optional[int] = Field(None, ge=0)
    stock_qty: Optional[int] = Field(None, ge=0, description="Sets stock; logged as a manual movement")
    is_active: Optional[bool] = None


class SellerVariantOut(Schema):
    id: UUID
    sku: str
    option_values: dict[str, str] = Field(..., description='{"Shade": "Ivory"}')
    price_cents: Optional[int] = None
    effective_price_cents: int
    compare_at_price_cents: Optional[int] = None
    stock_qty: int
    is_active: bool


class SellerImageOut(Schema):
    id: UUID
    url: str
    alt: str
    is_primary: bool
    sort_order: int


class ImageOrderIn(Schema):
    image_ids: list[UUID] = Field(min_length=1, description="New order; the first becomes the primary image")


class ContentSectionIn(Schema):
    section: Literal["benefit", "usage_step"]
    title: NonEmptyStr = Field(max_length=200)
    body: NonEmptyStr = Field(max_length=2000)


class ContentIn(Schema):
    sections: list[ContentSectionIn] = Field([], max_length=20)


class OptionTypeOut(Schema):
    name: str
    values: list[OptionValueIn]


class CompletionOut(Schema):
    """Stepper state: which wizard steps are satisfied."""

    basic_info: bool
    media: bool
    pricing: bool
    shipping: bool
    ready_to_submit: bool
    missing: list[str]


class SellerProductOut(Schema):
    id: UUID
    name: str
    slug: str
    status: str
    rejection_reason: str
    category_id: Optional[UUID] = None
    category_path: list[str]
    brand_id: Optional[UUID] = None
    brand: Optional[str] = None
    tags: list[str]
    short_description: str
    description_title: str
    description: str
    price_cents: int
    compare_at_price_cents: Optional[int] = None
    currency: str
    stock_qty: int
    stock_status: str
    low_stock_threshold: Optional[int] = None
    effective_low_stock_threshold: int
    weight_grams: Optional[int] = None
    length_mm: Optional[int] = None
    width_mm: Optional[int] = None
    height_mm: Optional[int] = None
    ships_from: str
    handling_days: int
    is_featured: bool
    is_new_arrival: bool
    images: list[SellerImageOut]
    option_types: list[OptionTypeOut]
    variants: list[SellerVariantOut]
    key_benefits: list[ContentSectionIn]
    usage_steps: list[ContentSectionIn]
    completion: CompletionOut
    public_url: Optional[str] = Field(None, description="'View Product' — only while active")
    created_at: datetime
    updated_at: datetime
    published_at: Optional[datetime] = None
    submitted_at: Optional[datetime] = None


class StatusChangeIn(Schema):
    """'Change Status' menu: submit (→ pending), unpublish (active → draft), archive, unarchive."""

    action: Literal["submit", "unpublish", "archive", "unarchive"]


class ProductAnalyticsOut(Schema):
    """'View Analytics' for one product."""

    product_id: UUID
    period: str
    views: Kpi
    sold_qty: Kpi
    earned_cents: Kpi
    conversion_rate: Kpi = Field(..., description="orders containing the product / views, in %")
    wishlists: int
    rating_avg: float
    rating_count: int
    daily: list[SeriesPoint] = Field(..., description="last 30 days: views, sold")


class RejectIn(Schema):
    reason: NonEmptyStr = Field(max_length=1000)


class AdminProductRowOut(Schema):
    id: UUID
    name: str
    slug: str
    store: str
    store_slug: str
    category: Optional[str] = None
    price_cents: int
    status: str
    submitted_at: Optional[datetime] = None
    image_url: Optional[str] = None


# --------------------------------------------------------------------------- #
# Inventory
# --------------------------------------------------------------------------- #

InventoryTabLit = Literal["all", "low", "out", "recent"]
InventorySortLit = Literal["updated", "stock", "stock_desc", "name"]


class InventoryQuery(FilterSchema):
    tab: InventoryTabLit = "all"
    q: Optional[str] = Field(None, max_length=120)
    brand: Optional[list[str]] = None
    category: Optional[str] = None
    sort: InventorySortLit = "updated"


class InventoryVariantOut(Schema):
    id: UUID
    sku: str
    option_values: dict[str, str]
    stock_qty: int
    is_active: bool


class InventoryRowOut(Schema):
    id: UUID
    name: str
    slug: str
    image_url: Optional[str] = None
    category: Optional[str] = None
    brand: Optional[str] = None
    on_hand: int = Field(..., description="Sum of active variants' stock")
    reserved: int = Field(..., description="Units in paid orders not yet shipped (informational)")
    available: int
    variant_count: int
    stock_status: str
    status: str
    low_stock_threshold: int
    last_updated: datetime
    variants: list[InventoryVariantOut]


class InventoryTabsOut(Schema):
    all: int
    low: int
    out: int
    recent: int


class InventoryKpisOut(Schema):
    total_products: int
    total_variants: int
    total_units: int
    out_of_stock: int
    low_stock: int
    tabs: InventoryTabsOut


class StockAdjustIn(Schema):
    """Exactly one of `set` / `delta`."""

    set: Optional[int] = Field(None, ge=0, description="New absolute quantity")
    delta: Optional[int] = Field(None, description="Change, e.g. +50 received, -2 damaged")
    note: str = Field("", max_length=255)

    @model_validator(mode="after")
    def _one_of(self):
        if (self.set is None) == (self.delta is None):
            raise ValueError("Provide exactly one of set / delta")
        return self


class StockMovementOut(Schema):
    id: UUID
    delta: int
    qty_after: int
    reason: str
    note: str
    order_number: Optional[str] = None
    actor: Optional[str] = None
    created_at: datetime
