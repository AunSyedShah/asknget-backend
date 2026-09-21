from django.contrib import admin

from .models import Country, KycPolicy, ReturnPolicy


class KycPolicyInline(admin.StackedInline):
    model = KycPolicy
    can_delete = False
    extra = 0
    max_num = 1
    fieldsets = (
        (None, {"fields": ("mode",)}),
        ("Documents", {"fields": ("required_documents", "document_labels", "instructions")}),
        ("Identity fields", {"fields": ("id_number_label", "id_number_regex", "tax_id_label", "company_registration_label")}),
    )


class ReturnPolicyInline(admin.StackedInline):
    model = ReturnPolicy
    can_delete = False
    extra = 0
    max_num = 1


@admin.register(Country)
class CountryAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "subdomain", "currency", "timezone", "default_language", "is_active", "is_default", "sort_order")
    list_filter = ("is_active", "is_default")
    list_editable = ("is_active", "sort_order")
    search_fields = ("code", "name", "subdomain")
    ordering = ("sort_order", "name")
    inlines = [KycPolicyInline, ReturnPolicyInline]
    fieldsets = (
        (None, {"fields": ("code", "name", "subdomain", "is_active", "is_default", "sort_order")}),
        ("Locale", {"fields": ("currency", "timezone", "default_language", "languages", "phone_prefix")}),
        ("Address form", {"fields": ("region_label", "postal_code_regex")}),
    )

    def get_readonly_fields(self, request, obj=None):
        return ("code",) if obj else ()


@admin.register(KycPolicy)
class KycPolicyAdmin(admin.ModelAdmin):
    list_display = ("country", "mode", "id_number_label", "tax_id_label", "updated_at")


@admin.register(ReturnPolicy)
class ReturnPolicyAdmin(admin.ModelAdmin):
    list_display = ("country", "window_days", "is_active", "url", "updated_at")
    list_editable = ("window_days", "is_active")
