"""Customer ↔ seller conversations.

One thread per (store, customer): the seller portal lists people, not tickets
(the design's entry point is "Message Customer" on a customer row). A thread may
carry an `order` for context when it was opened from one.

Buyers may always write; reading and replying is a premium-plan feature
(`portal.entitlements.CHAT_INBOX`), so a free seller sees only counts. Unread
counters and `last_message_at` are denormalised and maintained by the single
write path in `chat.services`, which keeps the inbox one query.
"""

from django.conf import settings
from django.db import models

from core.models import TimeStampedModel


class Conversation(TimeStampedModel):
    store = models.ForeignKey("sellers.Store", on_delete=models.CASCADE, related_name="conversations")
    customer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="conversations",
                                 help_text="Signed-in buyer; guests have no account to come back to (§10 Q45)")
    order = models.ForeignKey("orders.Order", on_delete=models.SET_NULL, null=True, blank=True, related_name="conversations",
                              help_text="Context when the thread was opened from an order")
    subject = models.CharField(max_length=140, blank=True)

    last_message_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_message_preview = models.CharField(max_length=140, blank=True)
    seller_unread = models.PositiveIntegerField(default=0)
    customer_unread = models.PositiveIntegerField(default=0)
    seller_last_read_at = models.DateTimeField(null=True, blank=True)
    customer_last_read_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True, help_text="Seller marked it resolved; a new message reopens it")

    class Meta(TimeStampedModel.Meta):
        ordering = ["-last_message_at", "-created_at"]
        constraints = [models.UniqueConstraint(fields=["store", "customer"], name="uniq_conversation_per_store_customer")]
        indexes = [
            models.Index(fields=["store", "-last_message_at"]),
            models.Index(fields=["customer", "-last_message_at"]),
        ]

    def __str__(self):
        return f"{self.store_id} ↔ {self.customer_id}"


class Message(TimeStampedModel):
    class SenderRole(models.TextChoices):
        CUSTOMER = "customer", "Customer"
        SELLER = "seller", "Seller"
        SYSTEM = "system", "System"

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    sender_role = models.CharField(max_length=10, choices=SenderRole.choices)
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="chat_messages")
    body = models.TextField()
    flagged_at = models.DateTimeField(null=True, blank=True, db_index=True,
                                      help_text="Looks like off-platform contact details — surfaced to staff, never blocked")
    flag_reason = models.CharField(max_length=60, blank=True)

    class Meta(TimeStampedModel.Meta):
        ordering = ["created_at"]
        indexes = [models.Index(fields=["conversation", "created_at"])]

    def __str__(self):
        return f"{self.sender_role}: {self.body[:40]}"
