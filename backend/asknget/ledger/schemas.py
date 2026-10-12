from datetime import date, datetime
from typing import Literal, Optional
from uuid import UUID

from ninja import Schema
from pydantic import Field

from portal.schemas import Kpi

EntryType = Literal["sale", "payout", "fee", "refund", "adjustment"]
EntryStatus = Literal["completed", "pending", "processing", "refunded", "failed"]


class EntryOut(Schema):
    """One line of the Transactions table."""

    reference: str = Field(description="TXN-20261012-10482")
    type: EntryType = Field(description="Sale | payout | fee (a returned fee reads as fee, positive) | refund | adjustment")
    amount_cents: int = Field(description="Signed: + is the seller's gain, - their loss")
    currency: str
    status: EntryStatus
    created_at: datetime
    available_at: Optional[datetime] = Field(None, description="From this moment it counts toward the available balance; null = still held")
    order_id: Optional[UUID]
    order_number: Optional[str]
    customer: Optional[str]
    description: str


class TabsOut(Schema):
    sale: int
    payout: int
    fee: int
    refund: int
    adjustment: int
    all: int


class SummaryOut(Schema):
    """'Transaction Summary': what the period added up to."""

    sales_revenue_cents: int
    platform_fees_cents: int = Field(description="Negative; commission taken, net of commission returned with refunds")
    refunds_cents: int = Field(description="Negative")
    adjustments_cents: int = Field(description="Signed")
    net_payout_cents: int


class PendingPayoutOut(Schema):
    value_cents: int = Field(description="Payouts staff have created that are still processing")
    held_cents: int = Field(description="'+R6,240 Processing': earned, but waiting for delivery or the return window")


class OverviewOut(Schema):
    period: str
    currency: str
    total_sales: Kpi
    total_fees: Kpi
    pending_payout: PendingPayoutOut
    available_balance_cents: int = Field(description="'Available to withdraw'")
    recent: list[EntryOut]
    summary: SummaryOut


class BreakdownLine(Schema):
    label: str
    amount_cents: int


class OrderRefOut(Schema):
    id: UUID
    order_number: str
    customer: str
    order_total_cents: int


class PaymentRefOut(Schema):
    method: str
    status: str


class DetailOut(EntryOut):
    order: Optional[OrderRefOut]
    payment: Optional[PaymentRefOut]
    breakdown: list[BreakdownLine]
    hold: Optional[dict] = Field(None, description="The payout hold that applied: {mode, days, started}")


# --- staff -----------------------------------------------------------------------------


class AdjustmentIn(Schema):
    store_id: UUID
    amount_cents: int = Field(description="Signed and non-zero: + credits the store, - debits it")
    note: str = Field(min_length=3, max_length=255, description="Required: why")


class StoreBalanceOut(Schema):
    store_id: UUID
    store_name: str
    currency: str
    available_cents: int
    held_cents: int
    pending_payout_cents: int


class PayoutIn(Schema):
    store_id: UUID
    amount_cents: int = Field(gt=0)
    note: str = Field("", max_length=255)


class PayoutStatusIn(Schema):
    status: Literal["completed", "failed"]


class PayoutOut(Schema):
    id: UUID
    reference: str
    store_id: UUID
    store_name: str
    amount_cents: int
    currency: str
    status: Literal["processing", "completed", "failed"]
    note: str
    created_at: datetime
    completed_at: Optional[datetime]
