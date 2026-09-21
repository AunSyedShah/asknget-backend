from django.contrib import admin

from .models import (
    SellerApplication,
    SellerApplicationDirector,
    SellerApplicationStatusEvent,
    SellerDocument,
    SellerInfoRequest,
    SellerInfoRequestItem,
    Store,
)


class DirectorInline(admin.TabularInline):
    model = SellerApplicationDirector
    extra = 0


class DocumentInline(admin.TabularInline):
    model = SellerDocument
    fk_name = "application"
    extra = 0
    fields = ("type", "file", "file_name", "version", "status", "reviewer_note")


class StatusEventInline(admin.TabularInline):
    model = SellerApplicationStatusEvent
    extra = 0
    readonly_fields = ("from_status", "to_status", "actor", "note", "created_at")
    can_delete = False


@admin.register(SellerApplication)
class SellerApplicationAdmin(admin.ModelAdmin):
    list_display = ("application_number", "store_display_name", "user", "account_type", "status",
                    "current_step", "submitted_at")
    list_filter = ("country", "status", "account_type")
    search_fields = ("application_number", "store_display_name", "legal_business_name", "user__email")
    # Encrypted identifiers are shown decrypted to staff but never edited here —
    # the review API is the write path and it logs who did what.
    readonly_fields = ("id_passport_number", "rep_id_passport_number", "account_number", "account_number_last4",
                       "created_at", "updated_at")
    filter_horizontal = ("categories",)
    inlines = [DirectorInline, DocumentInline, StatusEventInline]


class InfoRequestItemInline(admin.TabularInline):
    model = SellerInfoRequestItem
    extra = 0


@admin.register(SellerInfoRequest)
class SellerInfoRequestAdmin(admin.ModelAdmin):
    list_display = ("application", "reviewer", "created_at", "resolved_at")
    inlines = [InfoRequestItemInline]


@admin.register(SellerDocument)
class SellerDocumentAdmin(admin.ModelAdmin):
    list_display = ("application", "type", "version", "status", "file_name", "created_at")
    list_filter = ("type", "status")


@admin.register(Store)
class StoreAdmin(admin.ModelAdmin):
    list_display = ("display_name", "slug", "owner", "status", "activated_at")
    list_filter = ("country", "status")
    search_fields = ("display_name", "slug", "owner__email")
    prepopulated_fields = {"slug": ("display_name",)}

    def get_readonly_fields(self, request, obj=None):
        # A store's market never changes: its products, prices and orders live there.
        return ("country", "application", "owner") if obj else ()
