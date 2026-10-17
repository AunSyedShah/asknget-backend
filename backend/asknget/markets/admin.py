from django.contrib import admin

from .models import Country, DocumentRequirement, DocumentType, KycPolicy, ReturnPolicy


class KycPolicyInline(admin.StackedInline):
    model = KycPolicy
    can_delete = False
    extra = 0
    max_num = 1
    fieldsets = (
        (None, {"fields": ("mode",)}),
        ("Documents", {"fields": ("instructions",), "description": "Which documents: the Document requirements below."}),
        ("Identity fields", {"fields": ("id_number_label", "id_number_regex", "tax_id_label", "company_registration_label")}),
    )


class DocumentRequirementInline(admin.TabularInline):
    """What each kind of seller in this market uploads. No rows for an account type = the platform defaults."""

    model = DocumentRequirement
    extra = 0
    fields = ("account_type", "document_type", "required", "step", "label", "instructions", "sort_order")
    autocomplete_fields = ("document_type",)


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
    inlines = [KycPolicyInline, DocumentRequirementInline, ReturnPolicyInline]
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
    list_display = ("country", "window_days", "defect_days", "restocking_fee_bps", "is_active", "updated_at")
    list_editable = ("window_days", "defect_days", "restocking_fee_bps", "is_active")


@admin.register(DocumentType)
class DocumentTypeAdmin(admin.ModelAdmin):
    """The documents sellers can be asked for. Add one here, then require it per market (Countries > Document requirements)."""

    list_display = ("code", "english_name", "accepted_types", "max_mb", "default_step", "is_active", "sort_order")
    list_editable = ("is_active", "sort_order")
    search_fields = ("code",)

    @admin.display(description="Name (English)")
    def english_name(self, obj):
        return (obj.name or {}).get("en", "")

    def get_readonly_fields(self, request, obj=None):
        return ("code",) if obj else ()                  # stored on every upload: it cannot change once used

    def has_delete_permission(self, request, obj=None):
        return False                                     # switch it off instead; uploads point at it
