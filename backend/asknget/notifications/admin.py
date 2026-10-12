from django.contrib import admin

from .models import NotificationTemplate, StoreNotification


@admin.register(NotificationTemplate)
class NotificationTemplateAdmin(admin.ModelAdmin):
    list_display = ("kind", "category", "english_title", "is_active", "updated_at")
    list_filter = ("category", "is_active")
    list_editable = ("is_active",)
    search_fields = ("kind", "description")
    readonly_fields = ("kind",)

    @admin.display(description="Title (English)")
    def english_title(self, obj):
        return (obj.title or {}).get("en", "")

    def has_add_permission(self, request):
        return False             # kinds exist because code emits them; new wording is an edit, not a new row

    def has_delete_permission(self, request, obj=None):
        return False             # switch a kind off with `is_active` instead


@admin.register(StoreNotification)
class StoreNotificationAdmin(admin.ModelAdmin):
    list_display = ("created_at", "store", "category", "kind")
    list_filter = ("category", "kind")
    search_fields = ("store__display_name", "kind")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
