from django.contrib import admin

from .models import Bank, SiteSetting, StoredFile


@admin.register(Bank)
class BankAdmin(admin.ModelAdmin):
    list_display = ("name", "country", "swift", "is_active")
    list_filter = ("country", "is_active")
    search_fields = ("name", "swift")


@admin.register(SiteSetting)
class SiteSettingAdmin(admin.ModelAdmin):
    list_display = ("key", "description", "updated_at")
    search_fields = ("key",)

@admin.register(StoredFile)
class StoredFileAdmin(admin.ModelAdmin):
    """Files kept in the database (FILE_STORAGE=database). The bytes are never loaded into the list or the form."""

    list_display = ("name", "content_type", "size", "created_at")
    search_fields = ("name",)
    exclude = ("content",)
    readonly_fields = ("name", "content_type", "size", "etag", "created_at")

    def get_queryset(self, request):
        return super().get_queryset(request).defer("content")

    def has_add_permission(self, request):
        return False
