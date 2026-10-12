"""A per-store record of money: what was earned, what the platform took, what went back to
buyers, what was paid out.

Append-only by design. A mistake is corrected by a new entry (an adjustment), never by
editing history, and every balance is *computed* from the entries, so there is no stored
total that can drift from the truth.
"""

from django.conf import settings
from django.db import models
from django.db.models import Q

from core.models import TimeStampedModel


class LedgerCounter(models.Model):
    """A locked row that hands out the next number for references (TXN-20261012-10482)."""

    name = models.CharField(max_length=20, primary_key=True)
    value = models.BigIntegerField(default=10000)


class Payout(TimeStampedModel):
    """Money moved from the platform to a store. Created and completed by staff: until a
    payment provider exists nothing here moves real money, it records that staff did."""

    class Status(models.TextChoices):
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    store = models.ForeignKey("sellers.Store", on_delete=models.PROTECT, related_name="payouts")
    amount_cents = models.PositiveBigIntegerField()
    currency = models.CharField(max_length=3)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PROCESSING, db_index=True)
    reference = models.CharField(max_length=30, unique=True)
    note = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta(TimeStampedModel.Meta):
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.reference} {self.amount_cents} ({self.status})"


class LedgerEntry(TimeStampedModel):
    class Type(models.TextChoices):
        SALE = "sale", "Sale"                         # + what the buyer paid this store (items - its voucher share + its delivery)
        FEE = "fee", "Fee"                            # - the platform's commission
        REFUND = "refund", "Refund"                   # - the sale, given back to the buyer
        FEE_REFUND = "fee_refund", "Fee refund"       # + the commission, given back with it
        ADJUSTMENT = "adjustment", "Adjustment"       # +/- staff correction, always with a note
        PAYOUT = "payout", "Payout"                   # - money sent to the store

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    store = models.ForeignKey("sellers.Store", on_delete=models.PROTECT, related_name="ledger_entries")
    type = models.CharField(max_length=12, choices=Type.choices, db_index=True)
    amount_cents = models.BigIntegerField(help_text="Signed, in the store's market currency: + is the seller's gain, - is their loss")
    currency = models.CharField(max_length=3)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    order = models.ForeignKey("orders.Order", on_delete=models.PROTECT, null=True, blank=True, related_name="ledger_entries")
    payout = models.ForeignKey(Payout, on_delete=models.PROTECT, null=True, blank=True, related_name="entries")
    reference = models.CharField(max_length=30, unique=True)
    description = models.CharField(max_length=255, blank=True)
    available_at = models.DateTimeField(
        null=True, blank=True, db_index=True,
        help_text="From this moment the amount counts toward the available balance. Empty = not yet (a sale is held until delivery + the hold policy)",
    )
    meta = models.JSONField(default=dict, blank=True, help_text="How the amount was worked out: commission rates and the rules behind them, the hold policy used")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta(TimeStampedModel.Meta):
        ordering = ["-created_at", "-reference"]
        indexes = [
            models.Index(fields=["store", "-created_at"]),
            models.Index(fields=["store", "type", "-created_at"]),
            models.Index(fields=["store", "available_at"]),
        ]
        constraints = [
            # One posting of each kind per order and store: confirming a payment twice, or refunding twice, cannot double-count.
            models.UniqueConstraint(
                fields=["order", "store", "type"], name="uniq_ledger_posting_per_order_store",
                condition=Q(type__in=["sale", "fee", "refund", "fee_refund"]),
            ),
            models.CheckConstraint(
                name="ledger_amount_sign_matches_type",
                condition=(
                    Q(type="sale", amount_cents__gt=0) | Q(type="fee", amount_cents__lte=0) | Q(type="refund", amount_cents__lt=0)
                    | Q(type="fee_refund", amount_cents__gte=0) | Q(type="payout", amount_cents__lt=0) | Q(type="adjustment") & ~Q(amount_cents=0)
                ),
            ),
            models.CheckConstraint(
                name="ledger_postings_belong_to_an_order",
                condition=~Q(type__in=["sale", "fee", "refund", "fee_refund"]) | Q(order__isnull=False),
            ),
        ]

    def __str__(self):
        return f"{self.reference} {self.type} {self.amount_cents}"
