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
    email_subject = models.JSONField(default=dict, blank=True, help_text="Leave empty to use the title")
    email_body = models.JSONField(default=dict, blank=True, help_text="Leave empty to use the body. Same {placeholders}")
    description = models.CharField(max_length=200, blank=True, help_text="When it fires and which placeholders it has")
    is_active = models.BooleanField(default=True, help_text="Untick to stop creating this kind of notification")

    class Meta(TimeStampedModel.Meta):
        ordering = ["category", "kind"]

    def __str__(self):
        return self.kind


class Event(TimeStampedModel):
    """Something that happened, once, whoever is told about it: the source of every notification and the activity log.

    `name` is from the event catalogue (notifications/catalog.py), e.g. "order.placed"."""

    name = models.CharField(max_length=60, db_index=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    market = models.ForeignKey("markets.Country", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    subject_type = models.CharField(max_length=30, blank=True, help_text="order | return | ...")
    subject_id = models.CharField(max_length=64, blank=True)
    payload = models.JSONField(default=dict, blank=True)

    class Meta(TimeStampedModel.Meta):
        indexes = [models.Index(fields=["subject_type", "subject_id"]), models.Index(fields=["market", "-created_at"])]

    def __str__(self):
        return f"{self.name} {self.subject_type}:{self.subject_id}"


class Notification(TimeStampedModel):
    """One line in someone's bell. Stores the event and its parameters, not the sentence: the wording is rendered when read,
    in the reader's language, from the template.

    Who sees it depends on the audience:
      customer  `user`
      seller    the members of `store` who may view the category's portal section
      staff     staff holding `permission` who work in `market` (no market = every market)
    Read state is per person (NotificationRead), so one person reading a shared line does not clear it for the rest."""

    class Audience(models.TextChoices):
        CUSTOMER = "customer", "Customer"
        SELLER = "seller", "Seller team"
        STAFF = "staff", "Staff"

    audience = models.CharField(max_length=10, choices=Audience.choices, default=Audience.SELLER, db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, related_name="notifications")
    store = models.ForeignKey("sellers.Store", on_delete=models.CASCADE, null=True, blank=True, related_name="notifications")
    market = models.ForeignKey("markets.Country", on_delete=models.CASCADE, null=True, blank=True, related_name="+")
    permission = models.CharField(max_length=60, blank=True, help_text="Staff: the permission needed to see it")
    event = models.ForeignKey(Event, on_delete=models.SET_NULL, null=True, blank=True, related_name="notifications")
    category = models.CharField(max_length=12, db_index=True)
    kind = models.SlugField(max_length=60)
    params = models.JSONField(default=dict, blank=True)
    link_type = models.CharField(max_length=20, blank=True, help_text="order | product | payout | customer | return: what the client should open")
    link_id = models.CharField(max_length=64, blank=True)

    class Meta(TimeStampedModel.Meta):
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["store", "-created_at"]),
            models.Index(fields=["store", "category", "-created_at"]),
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["audience", "market", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.kind} @ {self.store_id or self.user_id or self.market_id or 'every market'}"


class NotificationRead(TimeStampedModel):
    """Read state is per person: several people share a store, and one reading it must not clear it for the rest."""

    notification = models.ForeignKey(Notification, on_delete=models.CASCADE, related_name="reads")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")

    class Meta(TimeStampedModel.Meta):
        constraints = [models.UniqueConstraint(fields=["notification", "user"], name="uniq_notification_read")]


class NotificationRule(TimeStampedModel):
    """Who is told about an event and how: one row per event x audience, optionally per market.

    Resolution: the market's row, else the row for every market (market empty), else the default in notifications/catalog.py.
    So a new event works before anyone edits a row, and an admin changes behaviour without a release."""

    event = models.CharField(max_length=60, help_text="From the event catalogue, e.g. order.placed")
    audience = models.CharField(max_length=10, choices=Notification.Audience.choices)
    market = models.ForeignKey("markets.Country", on_delete=models.CASCADE, null=True, blank=True, related_name="+", help_text="Empty = every market")
    is_active = models.BooleanField(default=True, help_text="Off = this audience is not told at all")
    in_app = models.BooleanField(default=True, help_text="The bell")
    email = models.BooleanField(default=False)
    realtime = models.BooleanField(default=True, help_text="Push to an open app at once (WebSocket); off = it shows on the next refresh")
    user_can_opt_out = models.BooleanField(default=True, help_text="Off = mandatory (order confirmations, refunds, policy changes)")
    default_on = models.BooleanField(default=True, help_text="For people who never changed their notification settings")
    staff_permission = models.CharField(max_length=60, blank=True, help_text="Staff: who receives it (a staff permission code); empty = the catalogue default")
    min_amount_cents = models.PositiveBigIntegerField(null=True, blank=True, help_text="Only when the amount is at least this (e.g. high-value orders), in the market's currency")

    class Meta(TimeStampedModel.Meta):
        ordering = ["event", "audience"]
        constraints = [
            models.UniqueConstraint(fields=["event", "audience", "market"], name="uniq_rule_per_market"),
            models.UniqueConstraint(fields=["event", "audience"], condition=models.Q(market__isnull=True), name="uniq_rule_every_market"),
        ]

    def __str__(self):
        return f"{self.event} -> {self.audience} @ {self.market_id or 'every market'}"


class RuleChange(TimeStampedModel):
    """Who changed a rule, when, and from what to what: "why did sellers stop getting X?" must be answerable."""

    rule = models.ForeignKey(NotificationRule, on_delete=models.SET_NULL, null=True, blank=True, related_name="changes")
    rule_label = models.CharField(max_length=140)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    changes = models.JSONField(default=dict, help_text='{"email": [false, true]}; a new or deleted rule records every field')

    class Meta(TimeStampedModel.Meta):
        ordering = ["-created_at"]


class NotificationPreference(TimeStampedModel):
    """A person's own switch for one category on one channel, within what the rules allow."""

    class Channel(models.TextChoices):
        IN_APP = "in_app", "In the app"
        EMAIL = "email", "E-mail"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notification_preferences")
    category = models.CharField(max_length=12)
    channel = models.CharField(max_length=10, choices=Channel.choices)
    enabled = models.BooleanField()

    class Meta(TimeStampedModel.Meta):
        constraints = [models.UniqueConstraint(fields=["user", "category", "channel"], name="uniq_preference")]


class Delivery(TimeStampedModel):
    """The outbox: one message to one recipient on one channel. Written in the same transaction as the event and sent after it
    commits (at once, then retried by `deliver_notifications`), so a rolled-back change never sends and a crash never loses one."""

    class Status(models.TextChoices):
        PENDING = "pending", "Waiting to send"
        SENDING = "sending", "Sending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed (gave up)"
        SUPPRESSED = "suppressed", "Not sent (address suppressed)"

    key = models.CharField(max_length=200, unique=True, help_text="event + channel + recipient: the same message is never sent twice")
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="deliveries")
    channel = models.CharField(max_length=10, default="email")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    to = models.CharField(max_length=254, help_text="Where it goes (a guest has no account, only an address)")
    subject = models.CharField(max_length=255)
    body = models.TextField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=500, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta(TimeStampedModel.Meta):
        ordering = ["-created_at"]
        verbose_name_plural = "deliveries"
        indexes = [models.Index(fields=["status", "next_attempt_at"])]

    def __str__(self):
        return f"{self.channel} to {self.to}: {self.status}"
