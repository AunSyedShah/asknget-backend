"""Seller portal schemas (Figma: Seller Dashboard, Products, Inventory, Orders, Customers).

Every KPI is {value, previous, change_pct} so cards can render "12.8% vs. previous period".
"""

from datetime import date, datetime
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


# --------------------------------------------------------------------------- #
# Orders
# --------------------------------------------------------------------------- #

OrderTabLit = Literal["all", "pending", "processing", "shipped", "delivered", "cancelled", "returned"]
OrderSortLit = Literal["newest", "oldest", "total", "total_asc"]
FulfilmentLit = Literal["processing", "packed", "shipped", "delivered", "cancelled", "returned"]


class SellerOrderQuery(FilterSchema):
    tab: OrderTabLit = "all"
    q: Optional[str] = Field(None, max_length=120, description="Order number, customer name or email")
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    sort: OrderSortLit = "newest"


class SellerOrderRowOut(Schema):
    id: UUID
    order_number: str
    customer: str
    customer_email: str
    items_count: int
    placed_at: datetime
    total_cents: int = Field(..., description="This store's lines only")
    currency: str
    payment_method: str
    payment_status: str
    status: str = Field(..., description="Per-store fulfilment status")


class OrderTabsOut(Schema):
    all: int
    pending: int
    processing: int
    shipped: int
    delivered: int
    cancelled: int
    returned: int


class SellerOrdersOverviewOut(Schema):
    period: str
    total_orders: Kpi
    pending: Kpi
    processing: Kpi
    delivered: Kpi
    monthly: list[SeriesPoint] = Field(..., description="Jan..Dec of the current year: delivered / pending / refunded")
    status_share: dict[str, float] = Field(..., description="'Orders Status' donut: % of period orders per status")
    latest: list[SellerOrderRowOut]
    tabs: OrderTabsOut


class TimelineStageOut(Schema):
    key: str
    label: str
    at: Optional[datetime] = None
    state: Literal["done", "waiting", "skipped"]
    note: str = ""


class SellerOrderItemOut(Schema):
    id: UUID
    product_id: Optional[UUID] = None
    product_slug: Optional[str] = None
    product_name: str
    variant_summary: str
    image_url: Optional[str] = None
    qty: int
    unit_price_cents: int
    line_total_cents: int
    status: str


class OrderCustomerOut(Schema):
    name: str
    email: str
    phone: str
    customer_key: str = Field(..., description="Use with /seller/customers/{key}")


class OrderPaymentOut(Schema):
    method: str
    status: str
    amount_cents: int
    currency: str


class OrderSummaryBlockOut(Schema):
    subtotal_cents: int
    shipping_cents: int
    discount_cents: int
    tax_cents: int
    total_cents: int
    currency: str
    multi_store: bool = Field(..., description="Order also contains other stores' items; shipping/discount are order-level")
    order_total_cents: int
    shipping_shared: bool


class SellerOrderDetailOut(Schema):
    id: UUID
    order_number: str
    placed_at: datetime
    status: str
    allowed_next: list[str]
    customer: OrderCustomerOut
    shipping_address: dict
    billing_address: Optional[dict] = None
    delivery_method: Optional[dict] = None
    special_note: str
    payment: OrderPaymentOut
    timeline: list[TimelineStageOut]
    items: list[SellerOrderItemOut]
    summary: OrderSummaryBlockOut
    cancel_reason: str


class SellerInvoiceOut(SellerOrderDetailOut):
    invoice_number: str
    issued_at: datetime
    seller: dict


class OrderStatusIn(Schema):
    status: FulfilmentLit
    note: str = Field("", max_length=255)


class OrderCancelIn(Schema):
    reason: NonEmptyStr = Field(max_length=255)


# --------------------------------------------------------------------------- #
# Customers
# --------------------------------------------------------------------------- #

CustomerTabLit = Literal["all", "new", "returning", "one_time"]
CustomerSortLit = Literal["recent", "spent", "orders", "name"]


class CustomerQuery(FilterSchema):
    tab: CustomerTabLit = "all"
    q: Optional[str] = Field(None, max_length=120, description="'Search by customer name or email...'")
    status: Optional[Literal["active", "inactive"]] = None
    sort: CustomerSortLit = "recent"


class CustomerRowOut(Schema):
    key: str = Field(..., description="Buyer user id, or lower-cased email for guests")
    user_id: Optional[str] = None
    name: str
    email: str
    phone: str
    orders_count: int
    total_spent_cents: int
    currency: str
    first_order_at: Optional[datetime] = None
    last_order_at: Optional[datetime] = None
    last_order_number: str
    last_order_id: str
    type: Literal["new", "returning", "one_time"]
    status: Literal["active", "inactive"]


class CustomerTabsOut(Schema):
    all: int
    new: int
    returning: int
    one_time: int


class BreakdownSliceOut(Schema):
    count: int
    pct: float


class CustomersOverviewOut(Schema):
    period: str
    total_customers: Kpi
    new_customers: Kpi
    returning_customers: Kpi
    avg_customer_value_cents: Kpi
    monthly: list[SeriesPoint]
    breakdown: dict[str, BreakdownSliceOut]
    recent: list[CustomerRowOut]
    tabs: CustomerTabsOut


class CustomerNoteOut(Schema):
    id: UUID
    body: str
    author: Optional[str] = None
    created_at: datetime


class CustomerDetailOut(CustomerRowOut):
    orders: list[SellerOrderRowOut]
    notes: list[CustomerNoteOut]


class CustomerNoteIn(Schema):
    body: NonEmptyStr = Field(max_length=2000)


# --------------------------------------------------------------------------- #
# Dashboard + header search
# --------------------------------------------------------------------------- #


class SellerRefOut(Schema):
    name: str
    first_name: str
    role: str


class DashboardStoreOut(Schema):
    id: UUID
    name: str
    slug: str
    currency: str
    plan: str
    is_premium: bool


class FeatureOut(Schema):
    key: str
    label: str
    description: str
    included: bool


class PlanOut(Schema):
    """`GET /seller/plan` — what this store's plan unlocks. The upgrade card's copy
    comes from here so the portal never hard-codes feature names."""

    plan: str = Field(description="free | premium")
    plan_label: str
    is_premium: bool
    features: list[str]
    locked_features: list[str]
    catalogue: list[FeatureOut]


class PlanSetIn(Schema):
    is_premium: bool


class PerformanceOut(Schema):
    product_views: Kpi
    conversion_rate: Kpi = Field(..., description="%")
    aov_cents: Kpi
    customer_growth: Kpi


class StockBreakdownOut(Schema):
    in_stock: int
    low_stock: int
    out_of_stock: int


class DashboardOut(Schema):
    period: str
    seller: SellerRefOut
    store: DashboardStoreOut
    plan: PlanOut = Field(description="Sidebar locks 'Chat' and 'Analytics' from locked_features")
    sales_cents: Kpi = Field(..., description="'Total Sales' — booked in the period")
    orders: Kpi
    products: Kpi
    new_products: int
    low_stock: int
    revenue_cents: Kpi = Field(..., description="'Total Revenue' — delivered in the period")
    revenue_series: list[SeriesPoint] = Field(..., description="Jan..Dec: revenue + orders")
    performance: PerformanceOut
    latest_orders: list[SellerOrderRowOut]
    stock_breakdown: StockBreakdownOut


class SearchHitOut(Schema):
    id: str
    label: str
    sub: str = ""


class PortalSearchOut(Schema):
    products: list[SearchHitOut]
    orders: list[SearchHitOut]
    customers: list[SearchHitOut]


# --------------------------------------------------------------------------- #
# Advanced analytics (premium — docs/sprint-5-plan.md §4)
# --------------------------------------------------------------------------- #

ReportLit = Literal["overview", "funnel", "products", "search", "customers", "fulfilment"]
ProductSortLit = Literal["revenue", "units", "views", "conversion"]


class AnalyticsOverviewOut(Schema):
    period: str
    gross_sales_cents: Kpi
    net_sales_cents: Kpi = Field(description="After cancelled and returned lines")
    units: Kpi
    orders: Kpi
    aov_cents: Kpi
    repeat_order_rate: Kpi = Field(description="%")
    cancellation_rate: Kpi = Field(description="%")
    return_rate: Kpi = Field(description="%")


class FunnelStepOut(Schema):
    step: str
    count: Kpi
    from_previous_pct: float
    drop_off_pct: float


class AnalyticsFunnelOut(Schema):
    period: str
    steps: list[FunnelStepOut]
    view_to_purchase_pct: Kpi
    biggest_drop_off: str


class AnalyticsProductRowOut(Schema):
    product_id: UUID
    name: str
    slug: str
    status: str
    stock_status: str
    views: int
    units: int
    orders: int
    revenue_cents: int
    conversion_rate: float


class AnalyticsProductsOut(Schema):
    period: str
    sort: str
    top: list[AnalyticsProductRowOut]
    slow_movers: list[AnalyticsProductRowOut] = Field(description="Live, in stock, nothing sold this period")
    products_with_no_sales: int
    products_total: int


class SearchTermOut(Schema):
    query: str
    searches: int
    product_views: int


class ZeroResultQueryOut(Schema):
    query: str
    searches: int


class AnalyticsSearchOut(Schema):
    period: str
    store_queries: list[SearchTermOut] = Field(description="Typed shortly before viewing one of your products")
    market_zero_result_queries: list[ZeroResultQueryOut] = Field(description="Market-wide searches that found nothing — a gap to stock")
    scanned_events: int
    truncated: bool


class TopCustomerOut(Schema):
    key: str
    name: str
    email: str
    orders: int
    total_spent_cents: int
    last_order_at: Optional[datetime]
    type: str


class AnalyticsCustomersOut(Schema):
    period: str
    customers_total: int
    active_in_period: int
    repeat_customers: int
    repeat_rate: float
    orders_per_customer: float
    spend_per_customer_cents: int
    monthly: list[SeriesPoint]
    top_customers: list[TopCustomerOut]


class StageTimingOut(Schema):
    orders: int = Field(description="Orders with both ends of the stage recorded")
    median_hours: Optional[float]
    average_hours: Optional[float]


class AnalyticsFulfilmentOut(Schema):
    period: str
    orders: int
    by_status: dict[str, int]
    to_shipped: StageTimingOut = Field(description="Hours from payment to this store's 'shipped'")
    to_delivered: StageTimingOut = Field(description="Hours from 'shipped' to 'delivered'")
    cancellation_rate: float
    return_rate: float
