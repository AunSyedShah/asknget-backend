from django.contrib import admin

from .models import ReturnEvent, ReturnItem, ReturnPhoto, ReturnReason, ReturnRequest, ReturnRule, ReturnShipment


@admin.register(ReturnReason)
class ReturnReasonAdmin(admin.ModelAdmin):
    list_display = ("code", "kind", "requires_photo", "is_active", "sort_order")
    list_editable = ("requires_photo", "is_active", "sort_order")
    list_filter = ("kind", "is_active")
    filter_horizontal = ("countries",)
    search_fields = ("code",)


@admin.register(ReturnRule)
class ReturnRuleAdmin(admin.ModelAdmin):
    list_display = ("category", "country", "change_of_mind_days", "defect_days", "is_active", "note")
    list_filter = ("country", "is_active")
    list_editable = ("change_of_mind_days", "defect_days", "is_active")
    autocomplete_fields = ("category",)
    search_fields = ("category__name", "note")


class ReturnItemInline(admin.TabularInline):
    model = ReturnItem
    extra = 0
    can_delete = False
    readonly_fields = ("order_item", "qty", "line_total_cents")


class ReturnEventInline(admin.TabularInline):
    model = ReturnEvent
    extra = 0
    can_delete = False
    readonly_fields = ("created_at", "status", "actor", "actor_user", "note")


class ReturnShipmentInline(admin.StackedInline):
    model = ReturnShipment
    extra = 0
    can_delete = False


class ReturnPhotoInline(admin.TabularInline):
    model = ReturnPhoto
    extra = 0
    can_delete = False


@admin.register(ReturnRequest)
class ReturnRequestAdmin(admin.ModelAdmin):
    """Read-mostly: money and status move through the API/services so the ledger and timeline stay in step."""

    list_display = ("reference", "status", "country", "store", "reason_kind", "refund_cents", "refund_status", "created_at")
    list_filter = ("status", "country", "reason_kind", "refund_status")
    search_fields = ("reference", "order__order_number", "order__contact_email")
    inlines = [ReturnItemInline, ReturnShipmentInline, ReturnPhotoInline, ReturnEventInline]

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
