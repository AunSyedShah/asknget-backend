"""Schemas for cart, checkout, orders and tracking.

Screens: Cart, Checkout, Order Placed, Track Order, My Account – My Orders.
Money is integer cents + currency everywhere.
"""

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from ninja import Field, Schema
from pydantic import model_validator

from core.validators import EmailStr, NonEmptyStr, PhoneStr, PostalCode

PaymentMethodLit = Literal["card", "bank_transfer", "paypal", "google_pay", "apple_pay"]
OrderTabLit = Literal["all", "ongoing", "completed", "cancelled"]

# --------------------------------------------------------------------------- #
# Cart
# --------------------------------------------------------------------------- #


class CartItemIn(Schema):
    variant_id: UUID
    qty: int = Field(1, ge=1, le=99)


class CartItemPatchIn(Schema):
    qty: int = Field(ge=1, le=99)


class CartPatchIn(Schema):
    order_note: Optional[str] = Field(None, max_length=1000, description="'Order Notes (Optional)'")


class VoucherApplyIn(Schema):
    code: NonEmptyStr = Field(max_length=40)


class CartLineOut(Schema):
    id: UUID
    variant_id: UUID
    product_id: UUID
    product_slug: str
    product_name: str
    variant_summary: str = Field(description="'Size: 30 ml | Shade: Natural'")
    image_url: Optional[str]
    store_name: str
    unit_price_cents: int
    compare_at_price_cents: Optional[int]
    qty: int
    line_total_cents: int
    available_qty: int
    in_stock: bool = Field(description="False when qty exceeds stock — checkout will refuse")


class DeliveryMethodOut(Schema):
    id: UUID
    name: str
    eta_min_days: int
    eta_max_days: int
    eta_label: str = Field(description="'5 - 7 working days'")
    price_cents: int

    @staticmethod
    def resolve_eta_label(obj):
        return f"{obj.eta_min_days} - {obj.eta_max_days} working days"


class VoucherOut(Schema):
    code: str
    type: str
    value: int
    label: str = Field(description="'Discount (20%)'")


class CartSummaryOut(Schema):
    subtotal_cents: int
    shipping_cents: int
    discount_cents: int
    total_cents: int
    currency: str
    item_count: int


class DeliveryOptionOut(Schema):
    """One of a seller's own delivery options."""

    id: UUID
    name: str
    eta_label: str
    kind: str = Field(description="delivery | pickup")
    price_cents: int = Field(description="What it costs for this cart now (0 when the free-delivery threshold is met)")
    waived: bool = Field(False, description="The free-delivery threshold removed the fee")
    follows_rule: bool = Field(False, description="The price comes from the seller's or the platform's free-over / fee rule, not a fixed price")


class DeliveryRuleOut(Schema):
    """Free delivery over an amount, otherwise a fee: show 'Add R120 more for free delivery' when remaining_cents > 0."""

    free_over_cents: int = Field(description="0 = this seller has no free-delivery threshold")
    fee_cents: int
    store_subtotal_cents: int = Field(description="This seller's items in the cart, before any voucher")
    remaining_cents: int = Field(description="How much more of this seller's items for free delivery; 0 when met or no threshold")
    free: bool


class CartDeliveryOut(Schema):
    """Shipping is chosen per seller: each store in the cart gets its own line."""

    store_id: UUID
    store_name: str
    options: list[DeliveryOptionOut] = Field(description="Only options every item from this store supports")
    selected_option_id: Optional[UUID]
    price_cents: int
    rule: DeliveryRuleOut


class DeliveryChoiceIn(Schema):
    store_id: UUID
    option_id: UUID


class CartOut(Schema):
    id: UUID
    cart_token: Optional[str] = Field(None, description="Guests: send back as X-Cart-Token on every cart/checkout call")
    country: Optional[str] = Field(None, description="Market the cart belongs to: its lines' country, or the request's market while empty")
    items: list[CartLineOut]
    order_note: str
    voucher: Optional[VoucherOut]
    deliveries: list[CartDeliveryOut] = Field(description="One per store in the cart; summary.shipping_cents is their sum")
    summary: CartSummaryOut
    can_checkout: bool
    problems: list[str] = Field(description="Why checkout is blocked, if it is")


# --------------------------------------------------------------------------- #
# Checkout
# --------------------------------------------------------------------------- #


class ContactIn(Schema):
    """'Contact Info' block."""

    first_name: NonEmptyStr = Field(max_length=80)
    last_name: NonEmptyStr = Field(max_length=80)
    email: EmailStr
    phone: PhoneStr


class AddressIn(Schema):
    """'Delivery Address' block (inline). Alternatively pass a saved `address_id`."""

    address: NonEmptyStr = Field(max_length=255)
    city: NonEmptyStr = Field(max_length=100)
    postal_code: PostalCode
    province: str = Field("", max_length=100)
    country: str = Field("", max_length=2, description="ISO-2; blank = the market you are shopping in")
    delivery_instructions: str = Field("", max_length=500)


class CheckoutIn(Schema):
    """'Place Order'. Card details never come here — the PSP integration (next
    sprint) will tokenise them client-side; today `payment_method_type` is only
    recorded on the order."""

    contact: ContactIn
    address_id: Optional[UUID] = Field(None, description="Use a saved address (members)")
    address: Optional[AddressIn] = Field(None, description="…or type one in")
    ship_to_different_address: bool = False
    shipping_address: Optional[AddressIn] = None
    deliveries: list[DeliveryChoiceIn] = Field(default_factory=list, description="Per-store delivery choice; omit to use what the cart already has")
    payment_method_type: PaymentMethodLit
    payment_method_id: Optional[UUID] = Field(None, description="Saved card (members, type=card)")
    special_note: str = Field("", max_length=1000)
    channel: Literal["web", "mobile_app", "other"] = Field("web", description="Where the order was placed; feeds the seller's Sales by Channel report")

    @model_validator(mode="after")
    def _addresses(self):
        if not (self.address_id or self.address):
            raise ValueError("Provide address_id or address")
        if self.ship_to_different_address and not self.shipping_address:
            raise ValueError("shipping_address is required when ship_to_different_address is true")
        return self


# --------------------------------------------------------------------------- #
# Orders
# --------------------------------------------------------------------------- #


class OrderItemOut(Schema):
    id: UUID
    product_id: Optional[UUID]
    product_slug: Optional[str] = None
    product_name: str
    variant_summary: str
    image_url: str
    qty: int
    unit_price_cents: int
    compare_at_price_cents: Optional[int]
    line_total_cents: int
    store_name: str = Field(alias="store.display_name")
    can_review: bool

    @staticmethod
    def resolve_product_slug(obj):
        return obj.product.slug if obj.product_id else None

    @staticmethod
    def resolve_can_review(obj):
        return obj.order.status == "delivered" and not hasattr(obj, "review")


class OrderEventOut(Schema):
    status: str
    label: str
    note: str
    location: str
    created_at: datetime

    @staticmethod
    def resolve_label(obj):
        return obj.get_status_display()


class OrderSummaryOut(Schema):
    """Card in the My Orders list."""

    id: UUID
    order_number: str
    status: str
    status_label: str
    group: OrderTabLit
    placed_at: datetime
    total_cents: int
    currency: str
    payment_method_type: str
    payment_status: str
    items: list[OrderItemOut]
    can_cancel: bool
    can_reorder: bool

    @staticmethod
    def resolve_status_label(obj):
        return obj.get_status_display()

    @staticmethod
    def resolve_can_reorder(obj):
        return obj.group in ("completed", "cancelled")


class OrderDetailOut(OrderSummaryOut):
    contact_first_name: str
    contact_last_name: str
    contact_email: str
    contact_phone: str
    shipping_address: dict
    billing_address: Optional[dict]
    delivery_method: dict = Field(alias="delivery_method_snapshot")
    special_note: str
    subtotal_cents: int
    shipping_cents: int
    discount_cents: int
    voucher_code: str
    events: list[OrderEventOut]
    cancelled_at: Optional[datetime]
    cancel_reason: str
    country: str = Field(alias="country_id", description="Market the order was placed in")
    return_window_days: int = Field(description="Return policy snapshotted at checkout (0 = no returns)")
    return_eligible_until: Optional[datetime] = Field(None, description="The last moment any delivered line can still be returned, for any reason "
                                                                        "(per-line terms: category rules and the defect window); null until delivered")

    @staticmethod
    def resolve_events(obj):
        return obj.status_events.all()

    @staticmethod
    def resolve_return_eligible_until(obj):
        from returns.policy import eligible_until      # local: returns reads orders

        return eligible_until(obj)                       # per line: category rules and the defect window count


class OrderPlacedOut(Schema):
    """'Order Placed! Thank You, John Ali…'"""

    order: OrderDetailOut
    detail: str


class CancelIn(Schema):
    reason: str = Field("", max_length=500)


class TrackIn(Schema):
    order_number: NonEmptyStr = Field(max_length=20, description="'Find in your order confirmation mail'")
    email: EmailStr = Field(description="'Enter email you used during checkout'")


class TrackOut(Schema):
    order_number: str
    status: str
    status_label: str
    placed_at: datetime
    delivery_method: dict
    items: list[OrderItemOut]
    events: list[OrderEventOut]


class AdminOrderStatusIn(Schema):
    status: Literal["confirmed", "processing", "shipped", "out_for_delivery", "delivered", "cancelled", "refunded"]
    note: str = Field("", max_length=255)
    location: str = Field("", max_length=120)
