"""Customer return requests, and the rules (set by staff, per market) that decide what can be returned.

Rules are data:  `markets.ReturnPolicy` (per country: windows, restocking fee, wording),
`ReturnReason` (the choices on the form) and `ReturnRule` (per-category overrides, e.g. opened
cosmetics). What applied is *snapshotted* onto the order line at checkout (`OrderItem.return_terms`)
and onto the request, so editing a rule never rewrites an existing order or request.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from core.models import TimeStampedModel
from markets.models import validate_i18n


class ReturnReason(TimeStampedModel):
    """One choice on the return form. `kind` decides which window applies and who bears the cost."""

    class Kind(models.TextChoices):
        CHANGE_OF_MIND = "change_of_mind", "Change of mind"
        DEFECT = "defect", "Defective or damaged"
        WRONG_ITEM = "wrong_item", "Wrong item sent"
        NOT_AS_DESCRIBED = "not_as_described", "Not as described"
        OTHER = "other", "Other"

    code = models.SlugField(max_length=40, unique=True)
    label = models.JSONField(help_text='i18n, e.g. {"en": "Item arrived damaged", "ur": "..."}')
    kind = models.CharField(max_length=20, choices=Kind.choices)
    requires_photo = models.BooleanField(default=False, help_text="The buyer must attach at least one photo")
    countries = models.ManyToManyField("markets.Country", blank=True, related_name="return_reasons", help_text="Empty = offered in every market")
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta(TimeStampedModel.Meta):
        ordering = ["sort_order", "code"]

    def __str__(self):
        return self.code

    @property
    def seller_fault(self) -> bool:
        """The seller bears the cost: a defect or the wrong item. Change of mind never does."""
        return self.kind in (self.Kind.DEFECT, self.Kind.WRONG_ITEM, self.Kind.NOT_AS_DESCRIBED)

    @property
    def is_change_of_mind(self) -> bool:
        return self.kind == self.Kind.CHANGE_OF_MIND

    def clean(self):
        validate_i18n(self.label, field="label")
        if not self.label:
            raise ValidationError({"label": "A label is required."})


class ReturnRule(TimeStampedModel):
    """Overrides the market's return policy for a category (the nearest parent that has a rule wins),
    optionally only in one market. `None` means "no override"; `0` days means "not accepted"."""

    country = models.ForeignKey("markets.Country", on_delete=models.CASCADE, null=True, blank=True, related_name="return_rules", help_text="Empty = every market")
    category = models.ForeignKey("catalog.Category", on_delete=models.CASCADE, related_name="return_rules")
    change_of_mind_days = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Empty = follow the market's policy. 0 = change of mind not accepted (e.g. opened cosmetics)")
    defect_days = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Empty = follow the market's policy. 0 = not accepted")
    note = models.CharField(max_length=200, blank=True, help_text="Why (internal)")
    is_active = models.BooleanField(default=True)

    class Meta(TimeStampedModel.Meta):
        ordering = ["category__name", "country_id"]
        constraints = [
            models.UniqueConstraint(fields=["country", "category"], name="uniq_return_rule_per_country_category"),
            # SQL treats empty countries as all different, so the rule above cannot stop two "every market" rules.
            models.UniqueConstraint(fields=["category"], condition=Q(country__isnull=True), name="uniq_return_rule_all_markets_per_category"),
        ]

    def __str__(self):
        return f"{self.category} @ {self.country_id or 'all markets'}"


class ReturnRequest(TimeStampedModel):
    """A buyer asking to send items from one store's part of an order back. One store per request, because each
    store fulfils, is paid and refunds separately."""

    class Status(models.TextChoices):
        REQUESTED = "requested", "Requested"          # waiting for the seller
        APPROVED = "approved", "Approved"             # the buyer may send the goods back
        REJECTED = "rejected", "Rejected"             # the seller said no (the buyer may escalate unless `is_final`)
        ESCALATED = "escalated", "Escalated"          # the buyer disputes a rejection: staff decide
        RECEIVED = "received", "Received"             # arrived damaged: staff decide whether a refund is due
        REFUNDED = "refunded", "Refunded"             # done: the money went back
        CANCELLED = "cancelled", "Cancelled"          # the buyer withdrew it

    OPEN = (Status.REQUESTED, Status.APPROVED, Status.ESCALATED, Status.RECEIVED)

    class RefundStatus(models.TextChoices):
        NONE = "none", "Not due"
        PENDING = "pending", "Owed to the buyer"      # recorded; the card/bank payment is made by staff until a PSP exists
        COMPLETED = "completed", "Paid to the buyer"

    reference = models.CharField(max_length=30, unique=True, help_text="RET-00123-1")
    order = models.ForeignKey("orders.Order", on_delete=models.PROTECT, related_name="return_requests")
    store = models.ForeignKey("sellers.Store", on_delete=models.PROTECT, related_name="return_requests")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="return_requests")
    country = models.ForeignKey("markets.Country", on_delete=models.PROTECT, related_name="return_requests")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.REQUESTED, db_index=True)
    is_final = models.BooleanField(default=False, help_text="A rejection staff upheld, or that cannot be escalated")

    reason = models.ForeignKey(ReturnReason, on_delete=models.PROTECT, related_name="requests")
    reason_kind = models.CharField(max_length=20, choices=ReturnReason.Kind.choices, help_text="Snapshot of the reason's kind")
    note = models.TextField(blank=True, help_text="What the buyer wrote")
    seller_note = models.CharField(max_length=500, blank=True, help_text="The seller's reason for rejecting, shown to the buyer")
    staff_note = models.CharField(max_length=500, blank=True)
    terms = models.JSONField(default=dict, blank=True, help_text="The windows, fee and wording that applied when the request was made")
    response_due_at = models.DateTimeField(null=True, blank=True, help_text="The seller should answer by now; after it the request is 'overdue'")

    # Money. Amounts are in the order's currency.
    currency = models.CharField(max_length=3)
    items_cents = models.PositiveIntegerField(default=0, help_text="Value of the returned lines, before any voucher share")
    discount_cents = models.PositiveIntegerField(default=0, help_text="The voucher share that came off those lines")
    restocking_fee_cents = models.PositiveIntegerField(default=0)
    delivery_refund_cents = models.PositiveIntegerField(default=0)
    refund_cents = models.PositiveIntegerField(default=0, help_text="What goes back to the buyer: items - voucher share - restocking fee + delivery refund")
    refund_status = models.CharField(max_length=10, choices=RefundStatus.choices, default=RefundStatus.NONE)
    refund_note = models.CharField(max_length=255, blank=True, help_text="Bank or card reference staff paid it with")

    received_condition = models.CharField(max_length=10, blank=True, choices=[("good", "Good"), ("damaged", "Damaged")])
    restocked = models.BooleanField(default=False)
    escalated_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)
    refunded_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta(TimeStampedModel.Meta):
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["store", "status", "-created_at"]), models.Index(fields=["user", "-created_at"]), models.Index(fields=["status", "response_due_at"])]
        constraints = [models.CheckConstraint(name="return_request_refund_non_negative", condition=Q(refund_cents__gte=0))]

    def __str__(self):
        return f"{self.reference} ({self.status})"

    @property
    def is_open(self) -> bool:
        return self.status in self.OPEN


class ReturnItem(models.Model):
    """A line being returned (whole lines only: the line's stock, status and money are all per line)."""

    request = models.ForeignKey(ReturnRequest, on_delete=models.CASCADE, related_name="items")
    order_item = models.ForeignKey("orders.OrderItem", on_delete=models.PROTECT, related_name="return_items")
    qty = models.PositiveSmallIntegerField()
    line_total_cents = models.PositiveIntegerField(help_text="Snapshot of the line's value")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["request", "order_item"], name="uniq_return_item_per_request")]

    def __str__(self):
        return f"{self.qty} x {self.order_item_id}"


class ReturnPhoto(TimeStampedModel):
    request = models.ForeignKey(ReturnRequest, on_delete=models.CASCADE, related_name="photos")
    image = models.ImageField(upload_to="returns/")

    class Meta(TimeStampedModel.Meta):
        ordering = ["created_at"]


class ReturnEvent(TimeStampedModel):
    """The request's timeline, shown to the buyer, the seller and staff."""

    class Actor(models.TextChoices):
        BUYER = "buyer", "Buyer"
        SELLER = "seller", "Seller"
        STAFF = "staff", "Staff"
        SYSTEM = "system", "System"

    request = models.ForeignKey(ReturnRequest, on_delete=models.CASCADE, related_name="events")
    status = models.CharField(max_length=12, choices=ReturnRequest.Status.choices)
    actor = models.CharField(max_length=8, choices=Actor.choices)
    actor_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    note = models.CharField(max_length=500, blank=True)

    class Meta(TimeStampedModel.Meta):
        ordering = ["created_at"]


class ReturnShipment(TimeStampedModel):
    """The parcel going back. THE COURIER SEAM: when a courier integration is plugged in
    (`returns.couriers`), it books the pickup and fills `tracking_number` / `label_url` / `external_id`, and its
    tracking updates change `status` here. With the default `manual` courier the buyer ships it themselves and
    types the tracking number in."""

    class Status(models.TextChoices):
        PENDING = "pending", "Waiting to be sent"
        BOOKED = "booked", "Pickup booked"
        IN_TRANSIT = "in_transit", "In transit"
        DELIVERED = "delivered", "Delivered to the seller"
        FAILED = "failed", "Delivery failed"
        CANCELLED = "cancelled", "Cancelled"

    request = models.ForeignKey(ReturnRequest, on_delete=models.CASCADE, related_name="shipments")
    courier = models.SlugField(max_length=40, default="manual", help_text="Which integration handles it (returns.couriers)")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    tracking_number = models.CharField(max_length=80, blank=True)
    tracking_url = models.URLField(blank=True)
    label_url = models.URLField(blank=True, help_text="Prepaid return label, when the courier issues one")
    external_id = models.CharField(max_length=120, blank=True, help_text="The courier's booking / consignment id")
    pickup_address = models.JSONField(default=dict, blank=True, help_text="The buyer's address (snapshot)")
    dropoff_address = models.JSONField(default=dict, blank=True, help_text="The store's returns address (snapshot)")
    raw = models.JSONField(default=dict, blank=True, help_text="The courier's last response or webhook, kept for support")
    booked_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta(TimeStampedModel.Meta):
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["courier", "tracking_number"]), models.Index(fields=["courier", "external_id"])]

    def __str__(self):
        return f"{self.courier} {self.tracking_number or '-'} ({self.status})"
