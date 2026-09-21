"""Cart, vouchers, delivery methods, orders, status timeline, payments.

Screens: Cart, Checkout, Order Placed, Track Order, My Account – My Orders
(All / Ongoing / Completed / Cancelled).
"""

from django.conf import settings
from django.db import models
from django.db.models import Q

from core.models import TimeStampedModel


class Cart(TimeStampedModel):
    """One open cart per user; guests are keyed by `session_token`."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, related_name="cart")
    session_token = models.CharField(max_length=64, unique=True, null=True, blank=True)
    order_note = models.TextField(blank=True, help_text="'Order Notes (Optional)'")
    voucher = models.ForeignKey("Voucher", on_delete=models.SET_NULL, null=True, blank=True)
    delivery_method = models.ForeignKey(
        "DeliveryMethod", on_delete=models.SET_NULL, null=True, blank=True, help_text="Chosen 'Delivery Type'; drives the cart's Shipping Cost"
    )
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta(TimeStampedModel.Meta):
        constraints = [
            models.CheckConstraint(
                condition=Q(user__isnull=False) | Q(session_token__isnull=False),
                name="cart_has_owner_or_session",
            ),
        ]


class CartItem(TimeStampedModel):
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.CASCADE)
    qty = models.PositiveSmallIntegerField(default=1)

    class Meta(TimeStampedModel.Meta):
        constraints = [
            models.UniqueConstraint(fields=["cart", "variant"], name="uniq_variant_per_cart"),
            models.CheckConstraint(condition=Q(qty__gte=1), name="cart_item_qty_positive"),
        ]


class Voucher(TimeStampedModel):
    """'Discount Voucher → Apply'."""

    class VoucherType(models.TextChoices):
        PERCENT = "percent", "Percentage"
        FIXED = "fixed", "Fixed amount"

    code = models.CharField(max_length=40, unique=True)
    country = models.ForeignKey("markets.Country", on_delete=models.PROTECT, null=True, blank=True, related_name="vouchers",
                                help_text="Blank = valid in every market (amounts are in the cart's currency)")
    type = models.CharField(max_length=10, choices=VoucherType.choices)
    value = models.PositiveIntegerField(help_text="Percent (0–100) or amount in cents")
    min_subtotal_cents = models.PositiveIntegerField(null=True, blank=True)
    max_discount_cents = models.PositiveIntegerField(null=True, blank=True)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    usage_limit = models.PositiveIntegerField(null=True, blank=True)
    used_count = models.PositiveIntegerField(default=0)
    per_user_limit = models.PositiveSmallIntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta(TimeStampedModel.Meta):
        constraints = [
            models.CheckConstraint(
                condition=~Q(type="percent") | Q(value__lte=100),
                name="voucher_percent_lte_100",
            ),
        ]

    def __str__(self):
        return self.code


class VoucherRedemption(TimeStampedModel):
    voucher = models.ForeignKey(Voucher, on_delete=models.CASCADE, related_name="redemptions")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    order = models.OneToOneField("Order", on_delete=models.CASCADE, related_name="voucher_redemption")


class DeliveryMethod(TimeStampedModel):
    """Standard (5–7 days) / Express (2–3 days)."""

    country = models.ForeignKey("markets.Country", on_delete=models.PROTECT, related_name="delivery_methods")
    name = models.CharField(max_length=60)
    eta_min_days = models.PositiveSmallIntegerField()
    eta_max_days = models.PositiveSmallIntegerField()
    price_cents = models.PositiveIntegerField()
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta(TimeStampedModel.Meta):
        ordering = ["sort_order"]
        constraints = [models.UniqueConstraint(fields=["country", "name"], name="uniq_delivery_method_per_country")]

    def __str__(self):
        return f"{self.name} ({self.eta_min_days}–{self.eta_max_days} days)"


class Order(TimeStampedModel):
    class OrderStatus(models.TextChoices):
        PENDING_PAYMENT = "pending_payment", "Pending Payment"
        CONFIRMED = "confirmed", "Confirmed"
        PROCESSING = "processing", "Processing"
        PACKED = "packed", "Packed"
        SHIPPED = "shipped", "Shipped"
        OUT_FOR_DELIVERY = "out_for_delivery", "Out for Delivery"
        DELIVERED = "delivered", "Delivered"
        CANCELLED = "cancelled", "Cancelled"
        RETURNED = "returned", "Returned"
        REFUNDED = "refunded", "Refunded"

    # The My Orders tabs group statuses like this.
    ONGOING_STATUSES = (OrderStatus.PENDING_PAYMENT, OrderStatus.CONFIRMED, OrderStatus.PROCESSING, OrderStatus.PACKED, OrderStatus.SHIPPED, OrderStatus.OUT_FOR_DELIVERY)
    COMPLETED_STATUSES = (OrderStatus.DELIVERED,)
    CANCELLED_STATUSES = (OrderStatus.CANCELLED, OrderStatus.RETURNED, OrderStatus.REFUNDED)
    # TODO(design): §10 Q4 — buyer may cancel until the parcel ships.
    CANCELLABLE_STATUSES = (OrderStatus.PENDING_PAYMENT, OrderStatus.CONFIRMED, OrderStatus.PROCESSING)

    class OrderPaymentMethod(models.TextChoices):
        CARD = "card", "Card"
        BANK_TRANSFER = "bank_transfer", "Bank Transfer"
        PAYPAL = "paypal", "PayPal"
        GOOGLE_PAY = "google_pay", "Google Pay"
        APPLE_PAY = "apple_pay", "Apple Pay"

    class OrderPaymentStatus(models.TextChoices):
        UNPAID = "unpaid", "Unpaid"
        AUTHORIZED = "authorized", "Authorized"
        PAID = "paid", "Paid"
        FAILED = "failed", "Failed"
        REFUNDED = "refunded", "Refunded"
        PARTIALLY_REFUNDED = "partially_refunded", "Partially Refunded"

    order_number = models.CharField(max_length=20, unique=True, help_text="e.g. ORD-00123")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="orders")
    country = models.ForeignKey("markets.Country", on_delete=models.PROTECT, related_name="orders", help_text="The market the cart belonged to")
    return_window_days = models.PositiveSmallIntegerField(default=0, help_text="Return policy in force at checkout (snapshot)")
    status = models.CharField(max_length=20, choices=OrderStatus.choices, default=OrderStatus.PENDING_PAYMENT, db_index=True)

    # Checkout – Contact Info
    contact_first_name = models.CharField(max_length=80)
    contact_last_name = models.CharField(max_length=80)
    contact_email = models.EmailField(db_index=True)  # Track Order looks up by number + email
    contact_phone = models.CharField(max_length=20)

    # Address snapshots — never FK to Address, it can change later
    shipping_address = models.JSONField()
    billing_address = models.JSONField(null=True, blank=True, help_text="Only when 'Ship to a different address'")

    delivery_method = models.ForeignKey(DeliveryMethod, on_delete=models.SET_NULL, null=True, blank=True)
    delivery_method_snapshot = models.JSONField(default=dict)
    special_note = models.TextField(blank=True)

    subtotal_cents = models.PositiveIntegerField()
    shipping_cents = models.PositiveIntegerField(default=0)
    discount_cents = models.PositiveIntegerField(default=0)
    voucher_code = models.CharField(max_length=40, blank=True)
    total_cents = models.PositiveIntegerField()
    currency = models.CharField(max_length=3, default="USD")

    payment_method_type = models.CharField(max_length=20, choices=OrderPaymentMethod.choices)
    payment_status = models.CharField(max_length=20, choices=OrderPaymentStatus.choices, default=OrderPaymentStatus.UNPAID)

    placed_at = models.DateTimeField(auto_now_add=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.TextField(blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="cancelled_orders"
    )

    class Meta(TimeStampedModel.Meta):
        ordering = ["-placed_at"]

    def __str__(self):
        return self.order_number

    @property
    def group(self) -> str:
        """'ongoing' | 'completed' | 'cancelled' — the My Orders tab bucket."""
        if self.status in self.COMPLETED_STATUSES:
            return "completed"
        if self.status in self.CANCELLED_STATUSES:
            return "cancelled"
        return "ongoing"

    @property
    def can_cancel(self) -> bool:
        # TODO(design): confirm cancellation window with team lead.
        return self.status in self.CANCELLABLE_STATUSES


class OrderItem(TimeStampedModel):
    """Snapshot of what was bought — product data may change after purchase.

    `status` is the **per-store fulfilment status**: a buyer order can span
    stores, each seller moves only their own lines (all together), and the
    buyer-facing Order.status is derived from the parts (orders.services)."""

    class FulfilmentStatus(models.TextChoices):
        PENDING = "pending", "Pending"          # paid, waiting for the seller
        PROCESSING = "processing", "Processing"
        PACKED = "packed", "Packed"
        SHIPPED = "shipped", "Shipped"
        DELIVERED = "delivered", "Delivered"
        CANCELLED = "cancelled", "Cancelled"
        RETURNED = "returned", "Returned"

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    store = models.ForeignKey("sellers.Store", on_delete=models.PROTECT, related_name="order_items")
    product = models.ForeignKey("catalog.Product", on_delete=models.SET_NULL, null=True, related_name="order_items")
    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.SET_NULL, null=True, related_name="order_items")

    product_name = models.CharField(max_length=200)
    variant_summary = models.CharField(max_length=255, blank=True, help_text="'Size: 30 ml | Shade: Natural'")
    image_url = models.URLField(blank=True)
    qty = models.PositiveSmallIntegerField()
    unit_price_cents = models.PositiveIntegerField()
    compare_at_price_cents = models.PositiveIntegerField(null=True, blank=True)
    line_total_cents = models.PositiveIntegerField()
    status = models.CharField(max_length=20, choices=FulfilmentStatus.choices, default=FulfilmentStatus.PENDING, db_index=True)

    class Meta(TimeStampedModel.Meta):
        ordering = ["created_at"]
        constraints = [models.CheckConstraint(condition=Q(qty__gte=1), name="order_item_qty_positive")]
        indexes = [models.Index(fields=["store", "status"])]

    def __str__(self):
        return f"{self.qty} × {self.product_name}"


class OrderStatusEvent(TimeStampedModel):
    """Feeds the Track Order timeline."""

    class OrderEventActor(models.TextChoices):
        SYSTEM = "system", "System"
        BUYER = "buyer", "Buyer"
        SELLER = "seller", "Seller"
        ADMIN = "admin", "Admin"

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="status_events")
    store = models.ForeignKey(
        "sellers.Store", on_delete=models.CASCADE, null=True, blank=True, related_name="order_events",
        help_text="Set for per-store fulfilment steps; null for order-level events (placed, paid)",
    )
    status = models.CharField(max_length=20, choices=Order.OrderStatus.choices)
    note = models.CharField(max_length=255, blank=True)
    location = models.CharField(max_length=120, blank=True)
    actor = models.CharField(max_length=10, choices=OrderEventActor.choices, default=OrderEventActor.SYSTEM)
    actor_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta(TimeStampedModel.Meta):
        ordering = ["created_at"]


class Payment(TimeStampedModel):
    class PaymentTransactionStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        AUTHORIZED = "authorized", "Authorized"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        REFUNDED = "refunded", "Refunded"

    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="payments")
    provider = models.CharField(max_length=40)
    provider_payment_id = models.CharField(max_length=255, blank=True, db_index=True)
    method_type = models.CharField(max_length=20, choices=Order.OrderPaymentMethod.choices)
    amount_cents = models.PositiveIntegerField()
    currency = models.CharField(max_length=3, default="USD")
    status = models.CharField(max_length=20, choices=PaymentTransactionStatus.choices, default=PaymentTransactionStatus.PENDING)
    raw_response = models.JSONField(default=dict, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.order.order_number} – {self.get_status_display()}"
