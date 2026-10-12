from django.contrib import admin

from .models import StoreActivity, StoreMember, TeamRole


@admin.register(TeamRole)
class TeamRoleAdmin(admin.ModelAdmin):
    list_display = ("name", "key", "counts_as_manager", "is_active", "sort_order", "updated_at")
    list_editable = ("is_active", "sort_order")
    prepopulated_fields = {"key": ("name",)}
    search_fields = ("name", "key")
    fieldsets = (
        (None, {"fields": ("name", "key", "description", "is_active", "sort_order", "counts_as_manager")}),
        ("Access", {"fields": ("permissions",), "description": "Sections: dashboard, orders, products, inventory, customers, marketing, analytics, "
                                                                 "transactions. Levels: none, view, manage. Changing a role does not change the access of people already assigned to it."}),
    )


@admin.register(StoreMember)
class StoreMemberAdmin(admin.ModelAdmin):
    list_display = ("email", "store", "role", "status", "joined_at", "last_active_at")
    list_filter = ("status", "role")
    search_fields = ("email", "store__display_name", "user__full_name")
    autocomplete_fields = ("store", "user", "invited_by")
    readonly_fields = ("invite_token_hash", "invite_expires_at", "joined_at", "last_active_at")


@admin.register(StoreActivity)
class StoreActivityAdmin(admin.ModelAdmin):
    list_display = ("created_at", "store", "actor", "action", "summary")
    list_filter = ("action",)
    search_fields = ("summary", "store__display_name", "actor__email")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):          # append-only: the record of what people did is not editable
        return False

    def has_change_permission(self, request, obj=None):
        return False
