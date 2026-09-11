from django.contrib import admin

from .models import Banner, ContactMessage, NewsletterSubscriber


@admin.register(Banner)
class BannerAdmin(admin.ModelAdmin):
    list_display = ("title", "placement", "badge_text", "sort_order", "is_active", "starts_at", "ends_at")
    list_filter = ("placement", "is_active")


@admin.register(NewsletterSubscriber)
class NewsletterSubscriberAdmin(admin.ModelAdmin):
    list_display = ("email", "source", "created_at", "unsubscribed_at")
    search_fields = ("email",)


@admin.register(ContactMessage)
class ContactMessageAdmin(admin.ModelAdmin):
    list_display = ("email", "subject", "status", "seller_application", "created_at")
    list_filter = ("subject", "status")
    search_fields = ("email", "first_name", "last_name", "message")
