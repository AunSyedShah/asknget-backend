from django.contrib import admin

from .models import (
    Cart,
    CartItem,
    DeliveryMethod,
    Order,
    OrderItem,
    OrderStatusEvent,
    Payment,
    Voucher,
    VoucherRedemption,
)


class CartItemInline(admin.TabularInline):
    model = CartItem
    extra = 0


@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "session_token", "voucher", "updated_at")
    search_fields = ("user__email", "session_token")
    inlines = [CartItemInline]


@admin.register(Voucher)
class VoucherAdmin(admin.ModelAdmin):
    list_display = ("code", "type", "value", "used_count", "usage_limit", "starts_at", "ends_at", "is_active")
    list_filter = ("country", "type", "is_active")
    search_fields = ("code",)


@admin.register(VoucherRedemption)
class VoucherRedemptionAdmin(admin.ModelAdmin):
    list_display = ("voucher", "user", "order", "created_at")


@admin.register(DeliveryMethod)
class DeliveryMethodAdmin(admin.ModelAdmin):
    list_display = ("name", "country", "eta_min_days", "eta_max_days", "price_cents", "is_active")
    list_filter = ("country", "is_active")


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ("product_name", "variant_summary", "qty", "unit_price_cents", "line_total_cents")


class OrderStatusEventInline(admin.TabularInline):
    model = OrderStatusEvent
    extra = 0


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0
    readonly_fields = ("provider", "provider_payment_id", "amount_cents", "status", "paid_at")


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("order_number", "user", "contact_email", "status", "payment_status", "total_cents", "placed_at")
    list_filter = ("country", "status", "payment_status", "payment_method_type")
    search_fields = ("order_number", "contact_email", "user__email")
    readonly_fields = ("placed_at",)
    inlines = [OrderItemInline, OrderStatusEventInline, PaymentInline]


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("order", "provider", "method_type", "amount_cents", "status", "paid_at")
    list_filter = ("provider", "status")
    search_fields = ("order__order_number", "provider_payment_id")
