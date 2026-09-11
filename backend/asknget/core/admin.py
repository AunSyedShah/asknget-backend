from django.contrib import admin

from .models import Bank, SiteSetting


@admin.register(Bank)
class BankAdmin(admin.ModelAdmin):
    list_display = ("name", "country", "swift", "is_active")
    list_filter = ("country", "is_active")
    search_fields = ("name", "swift")


@admin.register(SiteSetting)
class SiteSettingAdmin(admin.ModelAdmin):
    list_display = ("key", "description", "updated_at")
    search_fields = ("key",)
