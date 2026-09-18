"""Seller portal (dashboard) data that is not derived from orders/products.

Almost everything the dashboard shows is computed from catalog / orders /
discovery tables per store (see docs/sprint-3-plan.md). The only stored thing
is a store's private note about a customer."""

from django.conf import settings
from django.db import models

from core.models import TimeStampedModel


class CustomerNote(TimeStampedModel):
    """'Add Note' on the Customers screen. Customers are derived from orders, so
    they are addressed by a key: the buyer's user id, or the lower-cased
    contact email for guest checkouts."""

    store = models.ForeignKey("sellers.Store", on_delete=models.CASCADE, related_name="customer_notes")
    customer_key = models.CharField(max_length=254, db_index=True)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    body = models.TextField()

    class Meta(TimeStampedModel.Meta):
        indexes = [models.Index(fields=["store", "customer_key", "-created_at"])]

    def __str__(self):
        return f"{self.store_id}:{self.customer_key}"
