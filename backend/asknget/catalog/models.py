"""Catalog: categories, brands, products, variants, reviews, wishlist.

Screens: Landing Page, Categories (listing + filters), Product Details
(Description / How to Use / Reviews), My Account – Wishlist.

Money is stored as integer minor units (`*_cents`) with a currency code.
"""

from django.conf import settings
from django.db import models
from django.db.models import Q

from core.models import SoftDeleteModel, TimeStampedModel


class Category(TimeStampedModel):
    """Tree: top-level (Beauty) and sub-categories (Makeup, Shoes, Crockery)."""

    parent = models.ForeignKey("self", on_delete=models.CASCADE, null=True, blank=True, related_name="children")
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=120, unique=True)
    image = models.ImageField(upload_to="categories/", null=True, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    product_counts = models.JSONField(
        default=dict, blank=True,
        help_text='Denormalised "128 items" per market, e.g. {"ZA": 128, "PK": 40}; refreshed by catalog.services.refresh_category_counts',
    )

    def product_count_for(self, country) -> int:
        code = getattr(country, "code", country)
        if code is None:  # no market active → every market
            return sum(self.product_counts.values())
        return int(self.product_counts.get(code, 0))

    class Meta(TimeStampedModel.Meta):
        verbose_name_plural = "categories"
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class Brand(TimeStampedModel):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=120, unique=True)
    logo = models.ImageField(upload_to="brands/", null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta(TimeStampedModel.Meta):
        ordering = ["name"]

    def __str__(self):
        return self.name


class Product(SoftDeleteModel):
    class StockStatus(models.TextChoices):
        """Derived from variant stock (see catalog.services.refresh_stock_status);
        only `preorder` is set by hand."""

        IN_STOCK = "in_stock", "In Stock"
        LOW_STOCK = "low_stock", "Low Stock"
        OUT_OF_STOCK = "out_of_stock", "Out of Stock"
        PREORDER = "preorder", "Pre-order"

    AVAILABLE_STOCK_STATUSES = ("in_stock", "low_stock", "preorder")

    class ProductStatus(models.TextChoices):
        """Seller dashboard tabs: Draft → (submit) Pending → Active | Rejected; Active ↔ Archived."""

        DRAFT = "draft", "Draft"
        PENDING = "pending", "Pending review"
        ACTIVE = "active", "Active"
        REJECTED = "rejected", "Rejected"
        ARCHIVED = "archived", "Archived"

    store = models.ForeignKey("sellers.Store", on_delete=models.PROTECT, related_name="products")
    country = models.ForeignKey(
        "markets.Country", on_delete=models.PROTECT, related_name="products",
        help_text="Denormalised from the store so every storefront query filters by market without a join",
    )
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="products")
    brand = models.ForeignKey(Brand, on_delete=models.SET_NULL, null=True, blank=True, related_name="products")

    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True)
    short_description = models.TextField(blank=True, help_text="One-liner under the title on the PDP; the seller wizard's 250-char description")
    tags = models.JSONField(default=list, blank=True, help_text='Seller wizard "Product Tags"; also fed to search')
    description_title = models.CharField(max_length=200, blank=True, help_text='e.g. "Perfecting BB Cream"')
    description = models.TextField(blank=True)

    price_cents = models.PositiveIntegerField()
    compare_at_price_cents = models.PositiveIntegerField(null=True, blank=True, help_text="Struck-through original price")
    cost_price_cents = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Wizard 'Cost Price'. Seller-only — never serialise this to a buyer.",
    )
    barcode = models.CharField(max_length=64, blank=True, help_text="Wizard 'Barcode' (EAN/UPC); not unique — sellers reuse them")
    currency = models.CharField(max_length=3, default="USD")

    stock_status = models.CharField(max_length=20, choices=StockStatus.choices, default=StockStatus.IN_STOCK)
    rating_avg = models.DecimalField(max_digits=2, decimal_places=1, default=0)
    rating_count = models.PositiveIntegerField(default=0)

    is_featured = models.BooleanField(default=False)
    is_best_seller = models.BooleanField(default=False)
    is_new_arrival = models.BooleanField(default=False)

    status = models.CharField(max_length=20, choices=ProductStatus.choices, default=ProductStatus.DRAFT, db_index=True)
    published_at = models.DateTimeField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)
    low_stock_threshold = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Overrides the store default")

    # Wizard step 4 "Shipping" (nothing drawn; the minimum a carrier quote needs)
    weight_grams = models.PositiveIntegerField(null=True, blank=True)
    length_mm = models.PositiveIntegerField(null=True, blank=True)
    width_mm = models.PositiveIntegerField(null=True, blank=True)
    height_mm = models.PositiveIntegerField(null=True, blank=True)
    ships_from = models.CharField(max_length=120, blank=True)
    handling_days = models.PositiveSmallIntegerField(default=1, help_text="Wizard 'Processing Time'")
    delivery_options = models.ManyToManyField(
        "sellers.StoreDeliveryOption", blank=True, related_name="products",
        help_text="Which of the store's options this product supports. **Empty means all of them**, so a "
                  "product never becomes unbuyable just because nobody ticked a box.",
    )

    class Meta(SoftDeleteModel.Meta):
        indexes = [
            models.Index(fields=["status", "category"]),
            models.Index(fields=["status", "price_cents"]),
            # Every storefront query is `country = ? AND status='active' AND deleted_at IS NULL`.
            models.Index(fields=["country", "-published_at"], condition=Q(status="active", deleted_at__isnull=True), name="product_country_live_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(compare_at_price_cents__isnull=True) | Q(compare_at_price_cents__gt=models.F("price_cents")),
                name="product_compare_at_gt_price",
            ),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.country_id and self.store_id:
            self.country_id = self.store.country_id
        super().save(*args, **kwargs)

    @property
    def discount_percent(self) -> int | None:
        """'40% Off' badge — derived, never stored."""
        if not self.compare_at_price_cents:
            return None
        return round((1 - self.price_cents / self.compare_at_price_cents) * 100)

    def effective_low_stock_threshold(self) -> int:
        if self.low_stock_threshold is not None:
            return self.low_stock_threshold
        return self.store.low_stock_threshold


class ProductImage(TimeStampedModel):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="images")
    image = models.ImageField(upload_to="products/")
    alt = models.CharField(max_length=200, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_primary = models.BooleanField(default=False)

    class Meta(TimeStampedModel.Meta):
        ordering = ["sort_order", "created_at"]
        constraints = [
            models.UniqueConstraint(fields=["product"], condition=Q(is_primary=True), name="uniq_primary_image_per_product"),
        ]


class ProductContentSection(TimeStampedModel):
    """'Key Benefits' bullets (Description tab) and numbered 'How to Use' steps."""

    class ContentSection(models.TextChoices):
        BENEFIT = "benefit", "Key Benefit"
        USAGE_STEP = "usage_step", "How-to-Use Step"

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="content_sections")
    section = models.CharField(max_length=20, choices=ContentSection.choices)
    title = models.CharField(max_length=200)
    body = models.TextField()
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta(TimeStampedModel.Meta):
        ordering = ["section", "sort_order"]


class ProductOptionType(TimeStampedModel):
    """e.g. Size, Shade, Color — per product."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="option_types")
    name = models.CharField(max_length=50)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta(TimeStampedModel.Meta):
        ordering = ["sort_order"]
        constraints = [models.UniqueConstraint(fields=["product", "name"], name="uniq_option_type_per_product")]

    def __str__(self):
        return f"{self.product_id}:{self.name}"


class ProductOptionValue(TimeStampedModel):
    """e.g. 30 ml, Ivory, Black."""

    option_type = models.ForeignKey(ProductOptionType, on_delete=models.CASCADE, related_name="values")
    value = models.CharField(max_length=80)
    swatch_hex = models.CharField(max_length=7, blank=True, help_text="#RRGGBB for colour swatches")
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta(TimeStampedModel.Meta):
        ordering = ["sort_order"]
        constraints = [models.UniqueConstraint(fields=["option_type", "value"], name="uniq_option_value_per_type")]

    def __str__(self):
        return self.value


class ProductVariant(TimeStampedModel):
    """A purchasable SKU = one value per option type."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="variants")
    sku = models.CharField(max_length=64, unique=True)
    option_values = models.ManyToManyField(ProductOptionValue, related_name="variants", blank=True)
    price_cents = models.PositiveIntegerField(null=True, blank=True, help_text="Overrides product price when set")
    compare_at_price_cents = models.PositiveIntegerField(null=True, blank=True)
    stock_qty = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.sku

    @property
    def effective_price_cents(self) -> int:
        return self.price_cents if self.price_cents is not None else self.product.price_cents

    def summary(self) -> str:
        """'Size: 30 ml | Shade: Natural' — snapshotted onto order lines."""
        values = self.option_values.select_related("option_type").order_by("option_type__sort_order", "option_type__name")
        return " | ".join(f"{v.option_type.name}: {v.value}" for v in values)


class StockMovement(TimeStampedModel):
    """Every change to a variant's stock, so 'inventory movements' can be audited.
    `qty_after` is the variant's stock right after this movement."""

    class Reason(models.TextChoices):
        SALE = "sale", "Sale"
        CANCEL = "cancel", "Order cancelled"
        RETURN = "return", "Return"
        MANUAL = "manual", "Manual adjustment"
        INITIAL = "initial", "Initial stock"

    variant = models.ForeignKey(ProductVariant, on_delete=models.CASCADE, related_name="movements")
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="stock_movements")
    delta = models.IntegerField()
    qty_after = models.PositiveIntegerField()
    reason = models.CharField(max_length=20, choices=Reason.choices)
    note = models.CharField(max_length=255, blank=True)
    order = models.ForeignKey("orders.Order", on_delete=models.SET_NULL, null=True, blank=True, related_name="stock_movements")
    actor_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta(TimeStampedModel.Meta):
        indexes = [models.Index(fields=["product", "-created_at"])]


class Review(SoftDeleteModel):
    class ReviewStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="reviews")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="reviews")
    order_item = models.OneToOneField(
        "orders.OrderItem", on_delete=models.SET_NULL, null=True, blank=True, related_name="review",
        help_text="Set ⇒ 'Verified Purchase' badge",
    )
    reviewer_name = models.CharField(max_length=120)
    reviewer_email = models.EmailField(help_text="Never published")
    rating = models.PositiveSmallIntegerField()
    body = models.TextField()
    status = models.CharField(max_length=20, choices=ReviewStatus.choices, default=ReviewStatus.PENDING, db_index=True)

    class Meta(SoftDeleteModel.Meta):
        constraints = [
            models.CheckConstraint(condition=Q(rating__gte=1, rating__lte=5), name="review_rating_1_to_5"),
        ]

    @property
    def is_verified_purchase(self) -> bool:
        return self.order_item_id is not None


class ReviewMedia(TimeStampedModel):
    class ReviewMediaType(models.TextChoices):
        IMAGE = "image", "Image"
        VIDEO = "video", "Video"

    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name="media")
    file = models.FileField(upload_to="reviews/")
    type = models.CharField(max_length=10, choices=ReviewMediaType.choices)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta(TimeStampedModel.Meta):
        ordering = ["sort_order"]


class WishlistItem(TimeStampedModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="wishlist_items")
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="wishlisted_by")

    class Meta(TimeStampedModel.Meta):
        constraints = [models.UniqueConstraint(fields=["user", "product"], name="uniq_wishlist_item")]
