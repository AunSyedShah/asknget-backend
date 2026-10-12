"""Shapes for the return endpoints: buyer, seller, staff and the public policy."""

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from ninja import Field, Schema

from markets.i18n import pick

from . import services
from .models import ReturnRequest

ReturnStatus = Literal["requested", "approved", "rejected", "escalated", "received", "refunded", "cancelled"]
ReasonKind = Literal["change_of_mind", "defect", "wrong_item", "not_as_described", "other"]


# --------------------------------------------------------------------------------------
# Policy (public)
# --------------------------------------------------------------------------------------


class ReasonOut(Schema):
    code: str
    label: str
    kind: ReasonKind
    requires_photo: bool

    @staticmethod
    def resolve_label(obj):
        return pick(obj.label, default=obj.code)


class PolicyOut(Schema):
    """What the return form shows before the buyer chooses: the market's rules and the reasons on offer."""

    country: str
    accepts_returns: bool
    change_of_mind_days: int
    defect_days: int
    restocking_fee_bps: int
    summary: str
    terms: str
    url: str
    reasons: list[ReasonOut]
    max_photos: int
    allow_escalation: bool
    courier: str = Field(description="Which courier integration handles pickups in this market ('manual' = the buyer posts it)")


# --------------------------------------------------------------------------------------
# An order's returnable lines
# --------------------------------------------------------------------------------------


class LineCheckOut(Schema):
    order_item_id: UUID
    product_name: str
    variant_summary: str
    image_url: str
    qty: int
    line_total_cents: int
    eligible: bool
    why_not: str = Field(description="not_delivered | window_closed | not_returnable | already_requested | already_returned | already_refunded; empty when eligible")
    deadline: Optional[datetime] = Field(None, description="Last day to ask, for the chosen reason (any reason when none was chosen)")
    change_of_mind_until: Optional[datetime]
    defect_until: Optional[datetime]
    store_id: UUID
    store_name: str


class ReturnableOut(Schema):
    order_number: str
    currency: str
    lines: list[LineCheckOut]
    reasons: list[ReasonOut]
    can_request: bool


# --------------------------------------------------------------------------------------
# A request
# --------------------------------------------------------------------------------------


class ReturnLineOut(Schema):
    order_item_id: UUID
    qty: int
    line_total_cents: int
    product_name: str
    variant_summary: str
    image_url: str

    @staticmethod
    def resolve_order_item_id(obj):
        return obj.order_item_id

    @staticmethod
    def resolve_product_name(obj):
        return obj.order_item.product_name

    @staticmethod
    def resolve_variant_summary(obj):
        return obj.order_item.variant_summary

    @staticmethod
    def resolve_image_url(obj):
        return obj.order_item.image_url


class ReturnEventOut(Schema):
    status: str
    actor: str
    note: str
    created_at: datetime


class ShipmentOut(Schema):
    id: UUID
    courier: str
    status: str
    status_label: str
    tracking_number: str
    tracking_url: str
    label_url: str
    dropoff_address: dict = Field(description="Where to send it: the store's returns address")
    created_at: datetime
    delivered_at: Optional[datetime]

    @staticmethod
    def resolve_status_label(obj):
        return obj.get_status_display()


class ReturnRowOut(Schema):
    id: UUID
    reference: str
    order_number: str
    status: ReturnStatus
    status_label: str
    reason: ReasonOut
    items_count: int
    refund_cents: int
    currency: str
    refund_status: str
    created_at: datetime
    is_overdue: bool

    @staticmethod
    def resolve_order_number(obj):
        return obj.order.order_number

    @staticmethod
    def resolve_status_label(obj):
        return obj.get_status_display()

    @staticmethod
    def resolve_items_count(obj):
        return obj.items_count if hasattr(obj, "items_count") else len(obj.items.all())

    @staticmethod
    def resolve_is_overdue(obj):
        return services.is_overdue(obj)


class SellerReturnRowOut(ReturnRowOut):
    customer_name: str

    @staticmethod
    def resolve_customer_name(obj):
        return f"{obj.order.contact_first_name} {obj.order.contact_last_name}".strip()


class AdminReturnRowOut(SellerReturnRowOut):
    store_id: UUID
    store_name: str
    country: str = Field(alias="country_id")
    can_decide: bool

    @staticmethod
    def resolve_store_id(obj):
        return obj.store_id

    @staticmethod
    def resolve_store_name(obj):
        return obj.store.display_name

    @staticmethod
    def resolve_can_decide(obj):
        return obj.status in (ReturnRequest.Status.ESCALATED, ReturnRequest.Status.RECEIVED)


class ReturnOut(ReturnRowOut):
    """Buyer view of one return: the 'Return status' page."""

    order_id: UUID
    store_name: str
    is_final: bool
    note: str
    seller_note: str
    staff_note: str
    items_cents: int
    discount_cents: int
    restocking_fee_cents: int
    delivery_refund_cents: int
    response_due_at: Optional[datetime]
    approved_at: Optional[datetime]
    refunded_at: Optional[datetime]
    items: list[ReturnLineOut]
    photos: list[str]
    events: list[ReturnEventOut]
    shipment: Optional[ShipmentOut]
    can_cancel: bool
    can_escalate: bool
    can_add_tracking: bool

    @staticmethod
    def resolve_order_id(obj):
        return obj.order_id

    @staticmethod
    def resolve_store_name(obj):
        return obj.store.display_name

    @staticmethod
    def resolve_items(obj):
        return obj.items.all()

    @staticmethod
    def resolve_photos(obj):
        return [p.image.url for p in obj.photos.all()]

    @staticmethod
    def resolve_events(obj):
        return obj.events.all()

    @staticmethod
    def resolve_shipment(obj):
        shipments = sorted(obj.shipments.all(), key=lambda s: s.created_at, reverse=True)
        return shipments[0] if shipments else None

    @staticmethod
    def resolve_can_cancel(obj):
        shipment = ReturnOut.resolve_shipment(obj)
        sent = shipment is not None and shipment.status in ("in_transit", "delivered")
        return obj.status in (ReturnRequest.Status.REQUESTED, ReturnRequest.Status.APPROVED) and not sent

    @staticmethod
    def resolve_can_escalate(obj):
        return obj.status == ReturnRequest.Status.REJECTED and not obj.is_final

    @staticmethod
    def resolve_can_add_tracking(obj):
        return obj.status == ReturnRequest.Status.APPROVED


class SellerReturnOut(ReturnOut):
    """Seller / staff view: adds who is asking and what can be done."""

    customer_name: str
    customer_email: str
    customer_phone: str
    received_condition: str
    restocked: bool
    can_approve: bool
    can_reject: bool
    can_receive: bool

    @staticmethod
    def resolve_customer_name(obj):
        return f"{obj.order.contact_first_name} {obj.order.contact_last_name}".strip()

    @staticmethod
    def resolve_customer_email(obj):
        return obj.order.contact_email

    @staticmethod
    def resolve_customer_phone(obj):
        return obj.order.contact_phone

    @staticmethod
    def resolve_can_approve(obj):
        return obj.status == ReturnRequest.Status.REQUESTED

    @staticmethod
    def resolve_can_reject(obj):
        return obj.status == ReturnRequest.Status.REQUESTED

    @staticmethod
    def resolve_can_receive(obj):
        return obj.status == ReturnRequest.Status.APPROVED


class AdminReturnOut(SellerReturnOut):
    store_id: UUID
    country: str = Field(alias="country_id")
    can_decide: bool

    @staticmethod
    def resolve_store_id(obj):
        return obj.store_id

    @staticmethod
    def resolve_can_decide(obj):
        return obj.status in (ReturnRequest.Status.ESCALATED, ReturnRequest.Status.RECEIVED)


class ReturnTabsOut(Schema):
    all: int
    open: int
    requested: int
    approved: int
    rejected: int
    escalated: int
    received: int
    refunded: int
    cancelled: int
    overdue: int


# --------------------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------------------


class ReturnCreateIn(Schema):
    """Multipart form: these fields, plus `photos` (files)."""

    reason: str = Field(description="A ReturnReason code from /returns/policy")
    note: str = Field("", max_length=1000)
    item_ids: list[UUID] = Field(description="Order line ids; all from the same store. Repeat the field once per line.")


class TrackingIn(Schema):
    tracking_number: str = Field(min_length=3, max_length=80)
    carrier: str = Field("", max_length=60, description="Courier name, free text")


class EscalateIn(Schema):
    note: str = Field("", max_length=500)


class RejectIn(Schema):
    reason: str = Field(min_length=3, max_length=500, description="Shown to the buyer")


class ReceivedIn(Schema):
    condition: Literal["good", "damaged"]
    restock: bool = Field(True, description="Put the items back in stock (ignored when damaged)")


class DecideIn(Schema):
    outcome: Literal["approve", "reject"]
    note: str = Field("", max_length=500, description="Required when rejecting")


class RefundPaidIn(Schema):
    reference: str = Field("", max_length=255, description="Bank / card reference staff paid the buyer with")


# --- staff: the rules ---------------------------------------------------------------------


class PolicyAdminOut(Schema):
    country: str
    country_name: str
    is_active: bool
    window_days: int
    defect_days: int
    restocking_fee_bps: int
    summary: dict
    terms: dict
    url: str


class PolicyAdminIn(Schema):
    is_active: bool
    window_days: int = Field(ge=0, le=3650)
    defect_days: int = Field(ge=0, le=3650)
    restocking_fee_bps: int = Field(ge=0, le=10000)
    summary: dict = Field(default_factory=dict, description='{"en": "...", "ur": "..."}')
    terms: dict = Field(default_factory=dict)
    url: str = ""


class ReasonAdminOut(Schema):
    id: UUID
    code: str
    label: dict
    kind: ReasonKind
    requires_photo: bool
    countries: list[str]
    is_active: bool
    sort_order: int

    @staticmethod
    def resolve_countries(obj):
        return sorted(c.code for c in obj.countries.all())


class ReasonAdminIn(Schema):
    code: str = Field(pattern=r"^[a-z0-9_-]{2,40}$")
    label: dict
    kind: ReasonKind
    requires_photo: bool = False
    countries: list[str] = Field(default_factory=list, description="ISO codes; empty = every market")
    is_active: bool = True
    sort_order: int = 0


class ReasonAdminPatch(Schema):
    label: Optional[dict] = None
    kind: Optional[ReasonKind] = None
    requires_photo: Optional[bool] = None
    countries: Optional[list[str]] = None
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None


class RuleAdminOut(Schema):
    id: UUID
    country: Optional[str] = Field(None, alias="country_id")
    category_id: UUID
    category_name: str
    change_of_mind_days: Optional[int]
    defect_days: Optional[int]
    note: str
    is_active: bool

    @staticmethod
    def resolve_category_name(obj):
        return obj.category.name


class RuleAdminIn(Schema):
    country: Optional[str] = Field(None, description="ISO code; empty = every market")
    category_id: UUID
    change_of_mind_days: Optional[int] = Field(None, ge=0, le=3650, description="Empty = follow the market; 0 = not accepted")
    defect_days: Optional[int] = Field(None, ge=0, le=3650)
    note: str = ""
    is_active: bool = True


class RuleAdminPatch(Schema):
    change_of_mind_days: Optional[int] = Field(None, ge=0, le=3650)
    defect_days: Optional[int] = Field(None, ge=0, le=3650)
    note: Optional[str] = None
    is_active: Optional[bool] = None
    clear_change_of_mind: bool = Field(False, description="Set true to go back to following the market")
    clear_defect: bool = False


class WebhookOut(Schema):
    applied: int

