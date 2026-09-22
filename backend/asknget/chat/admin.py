from django.contrib import admin

from .models import Conversation, Message


class MessageInline(admin.TabularInline):
    model = Message
    extra = 0
    readonly_fields = ("sender_role", "sender", "body", "flagged_at", "flag_reason", "created_at")
    can_delete = False


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("store", "customer", "last_message_at", "seller_unread", "customer_unread", "closed_at")
    list_filter = ("store__country", "store", "closed_at")
    search_fields = ("store__display_name", "customer__email", "last_message_preview")
    readonly_fields = ("store", "customer", "order", "last_message_at", "last_message_preview",
                       "seller_unread", "customer_unread", "seller_last_read_at", "customer_last_read_at")
    inlines = [MessageInline]


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("conversation", "sender_role", "flag_reason", "created_at")
    list_filter = ("sender_role", "flag_reason")
    search_fields = ("body",)
    readonly_fields = ("conversation", "sender_role", "sender", "body", "flagged_at", "flag_reason")
