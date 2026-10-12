from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from core.models import TimeStampedModel

from .defaults import CATEGORIES


def _validate_i18n(value):
    """{"en": "...", "ur": "..."} - at least English, and only languages the platform offers."""
    if not isinstance(value, dict) or not value:
        raise ValidationError('Use a language-keyed object like {"en": "..."}.')
    offered = {code for code, _ in settings.LANGUAGES}
    for code, text in value.items():
        if code not in offered:
            raise ValidationError(f"{code!r} is not a language this platform offers ({', '.join(sorted(offered))}).")
        if not isinstance(text, str) or not text.strip():
            raise ValidationError(f"The {code!r} text is empty.")
    if "en" not in value:
        raise ValidationError("Include an English (\"en\") text: it is the fallback for every other language.")


class NotificationTemplate(TimeStampedModel):
    """The wording of one kind of notification, per language. Switching `is_active` off stops that
    kind being created at all, so an owner can silence a noisy event without a release."""

    kind = models.SlugField(max_length=60, unique=True)
    category = models.CharField(max_length=12, choices=[(c, c.title()) for c in CATEGORIES])
    title = models.JSONField(validators=[_validate_i18n])
    body = models.JSONField(validators=[_validate_i18n], help_text="Use {placeholders} such as {order}, {customer}, {product}; each kind's are listed in the description")
    description = models.CharField(max_length=200, blank=True, help_text="When it fires and which placeholders it has")
    is_active = models.BooleanField(default=True, help_text="Untick to stop creating this kind of notification")

    class Meta(TimeStampedModel.Meta):
        ordering = ["category", "kind"]

    def __str__(self):
        return self.kind


class StoreNotification(TimeStampedModel):
    """Something that happened in a store. Stores the event and its parameters, not the sentence:
    the wording is rendered when read, in the reader's language, from the template."""

    store = models.ForeignKey("sellers.Store", on_delete=models.CASCADE, related_name="notifications")
    category = models.CharField(max_length=12, db_index=True)
    kind = models.SlugField(max_length=60)
    params = models.JSONField(default=dict, blank=True)
    link_type = models.CharField(max_length=20, blank=True, help_text="order | product | payout | customer: what the client should open")
    link_id = models.CharField(max_length=64, blank=True)

    class Meta(TimeStampedModel.Meta):
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["store", "-created_at"]), models.Index(fields=["store", "category", "-created_at"])]

    def __str__(self):
        return f"{self.kind} @ {self.store_id}"


class NotificationRead(TimeStampedModel):
    """Read state is per person: several people share a store, and one reading it must not clear it for the rest."""

    notification = models.ForeignKey(StoreNotification, on_delete=models.CASCADE, related_name="reads")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")

    class Meta(TimeStampedModel.Meta):
        constraints = [models.UniqueConstraint(fields=["notification", "user"], name="uniq_notification_read")]
