from django.contrib import admin

from .models import StaffProfile


@admin.register(StaffProfile)
class StaffProfileAdmin(admin.ModelAdmin):
    """Which markets a staff member works in. Their roles are edited on the user (Groups); a role's permissions on the group
    (Authentication and Authorization > Groups: the "staff | staff profile | ..." permissions are the staff catalogue)."""

    list_display = ("user", "market_list", "role_list")
    search_fields = ("user__email", "user__full_name")
    autocomplete_fields = ("user",)
    filter_horizontal = ("markets",)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("user").prefetch_related("markets", "user__groups")

    @admin.display(description="Markets")
    def market_list(self, obj):
        return ", ".join(m.code for m in obj.markets.all()) or "every market"

    @admin.display(description="Roles")
    def role_list(self, obj):
        return ", ".join(g.name for g in obj.user.groups.all()) or "none"
