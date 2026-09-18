"""Search & recommendations data: one embedding per product, plus the behaviour
events that personalisation is built from.

Runs on Postgres (pgvector: HNSW index + `<=>` cosine operator, full-text +
trigram search) and on SQLite for local dev/tests (vector stored as text,
similarity computed in Python — fine for a few thousand products).
"""

from django.conf import settings
from django.db import models
from pgvector.django import VectorField


class ProductEmbedding(models.Model):
    """The text card that was embedded and its vector. `text` doubles as the
    keyword-search document (name + brand + category + description + options)."""

    product = models.OneToOneField("catalog.Product", on_delete=models.CASCADE, primary_key=True, related_name="embedding")
    model = models.CharField(max_length=64, help_text="Embedder that produced `vector`, e.g. voyage-3.5-lite")
    text = models.TextField()
    text_hash = models.CharField(max_length=64, help_text="blake2b of `text`; unchanged text is not re-embedded")
    vector = VectorField(dimensions=settings.EMBEDDING_DIMENSIONS)
    is_stale = models.BooleanField(default=False, db_index=True, help_text="Set by catalog signals; cleared by embed_products")
    updated_at = models.DateTimeField(auto_now=True)

    # The HNSW (vector) and GIN/trigram (keyword) indexes are created in the
    # migration, Postgres-only, so this model also migrates cleanly on SQLite.

    def __str__(self):
        return f"{self.product_id} ({self.model})"


class ProductEvent(models.Model):
    """One row per meaningful interaction. Guests are keyed by the cart token so
    recommendations work before sign-in; no PII is stored here.

    Kinds and the weight each carries when building a taste profile:
      view 1 · wishlist 3 · add_to_cart 4 · purchase 6 · search (query log, weight 0)
    """

    class Kind(models.TextChoices):
        VIEW = "view", "Viewed product"
        SEARCH = "search", "Searched"
        WISHLIST = "wishlist", "Added to wishlist"
        ADD_TO_CART = "add_to_cart", "Added to cart"
        PURCHASE = "purchase", "Purchased"

    kind = models.CharField(max_length=20, choices=Kind.choices)
    product = models.ForeignKey("catalog.Product", on_delete=models.CASCADE, null=True, blank=True, related_name="events")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, related_name="product_events")
    cart_token = models.CharField(max_length=64, blank=True, default="")
    query = models.CharField(max_length=200, blank=True, default="", help_text="Search events: the raw query")
    result_count = models.PositiveIntegerField(null=True, blank=True, help_text="Search events: hits (0 ⇒ nothing found)")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["cart_token", "-created_at"]),
            models.Index(fields=["product", "kind", "-created_at"]),
            models.Index(fields=["kind", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.kind} {self.product_id or self.query}"


EVENT_WEIGHTS = {
    ProductEvent.Kind.VIEW: 1.0,
    ProductEvent.Kind.WISHLIST: 3.0,
    ProductEvent.Kind.ADD_TO_CART: 4.0,
    ProductEvent.Kind.PURCHASE: 6.0,
    ProductEvent.Kind.SEARCH: 0.0,
}
