"""Landing-page banners, newsletter, contact form.

Screens: Landing Page (hero, Top Discounted, newsletter), Contact Us,
"Contact Seller Support" on the application screens.
"""

from django.conf import settings
from django.db import models

from core.models import TimeStampedModel


class Banner(TimeStampedModel):
    class BannerPlacement(models.TextChoices):
        HERO = "hero", "Hero"
        TOP_DISCOUNT = "top_discount", "Top Discounted tile"
        CATEGORY_STRIP = "category_strip", "Category strip"

    country = models.ForeignKey("markets.Country", on_delete=models.CASCADE, null=True, blank=True, related_name="banners",
                                help_text="Blank = shown in every market")
    placement = models.CharField(max_length=20, choices=BannerPlacement.choices, db_index=True)
    eyebrow = models.CharField(max_length=80, blank=True, help_text='e.g. "TECH ESSENTIALS"')
    title = models.CharField(max_length=120)
    subtitle = models.CharField(max_length=255, blank=True)
    badge_text = models.CharField(max_length=40, blank=True, help_text='e.g. "UP TO 50% OFF"')
    cta_label = models.CharField(max_length=40, blank=True)
    cta_url = models.CharField(max_length=255, blank=True)
    image = models.ImageField(upload_to="banners/")
    sort_order = models.PositiveSmallIntegerField(default=0)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta(TimeStampedModel.Meta):
        ordering = ["placement", "sort_order"]

    def __str__(self):
        return f"[{self.placement}] {self.title}"


class NewsletterSubscriber(TimeStampedModel):
    email = models.EmailField(unique=True)
    source = models.CharField(max_length=40, blank=True, help_text="e.g. landing_footer")
    unsubscribed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.email


class ContactMessage(TimeStampedModel):
    class ContactSubject(models.TextChoices):
        # TODO(design): dropdown options not in Figma — confirm.
        GENERAL = "general", "General enquiry"
        ORDER_ISSUE = "order_issue", "Order issue"
        RETURNS = "returns", "Returns & refunds"
        PAYMENT = "payment", "Payment"
        SELLER_SUPPORT = "seller_support", "Seller support"
        OTHER = "other", "Other"

    class ContactStatus(models.TextChoices):
        NEW = "new", "New"
        IN_PROGRESS = "in_progress", "In Progress"
        CLOSED = "closed", "Closed"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    seller_application = models.ForeignKey(
        "sellers.SellerApplication", on_delete=models.SET_NULL, null=True, blank=True,
        help_text="Set when raised via 'Contact Seller Support'",
    )
    first_name = models.CharField(max_length=80)
    last_name = models.CharField(max_length=80)
    email = models.EmailField()
    phone = models.CharField(max_length=20, blank=True)
    subject = models.CharField(max_length=20, choices=ContactSubject.choices, default=ContactSubject.GENERAL)
    message = models.TextField()
    status = models.CharField(max_length=20, choices=ContactStatus.choices, default=ContactStatus.NEW, db_index=True)

    def __str__(self):
        return f"{self.get_subject_display()} – {self.email}"
