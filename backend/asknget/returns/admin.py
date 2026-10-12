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


class _ReadOnlyInline:
    """A return's parts are written by returns.services only (timeline, stock and ledger stay in step): shown, never edited."""

    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


class ReturnItemInline(_ReadOnlyInline, admin.TabularInline):
    model = ReturnItem


class ReturnEventInline(_ReadOnlyInline, admin.TabularInline):
    model = ReturnEvent
    fields = ("created_at", "status", "actor", "actor_user", "note")
    readonly_fields = ("created_at",)


class ReturnShipmentInline(_ReadOnlyInline, admin.StackedInline):
    model = ReturnShipment


class ReturnPhotoInline(_ReadOnlyInline, admin.TabularInline):
    model = ReturnPhoto


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
