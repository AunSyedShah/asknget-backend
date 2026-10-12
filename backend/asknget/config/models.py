"""Admin-managed configuration: scalar settings, and commission rules.

Two shapes because they behave differently. A setting is one current value that
applies from the moment it is saved. A commission rate changes over time and money
is posted against it, so it is a dated rule and each fee records the rule it used.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator
from django.db import models
from django.utils import timezone

from core.models import TimeStampedModel

from . import registry


class Setting(TimeStampedModel):
    """One override of a registered setting at one scope. The code default applies
    when no row matches, so deleting a row restores the default."""

    class Scope(models.TextChoices):
        GLOBAL = "global", "Whole platform"
        COUNTRY = "country", "One market"
        STORE = "store", "One store"

    key = models.CharField(max_length=100, help_text="One of the registered settings (see config/definitions.py)")
    scope = models.CharField(max_length=10, choices=Scope.choices, default=Scope.GLOBAL)
    country = models.ForeignKey("markets.Country", on_delete=models.CASCADE, null=True, blank=True, related_name="settings")
    store = models.ForeignKey("sellers.Store", on_delete=models.CASCADE, null=True, blank=True, related_name="settings")
    value = models.JSONField(help_text="Must satisfy the setting's type and bounds, e.g. 14, true, \"fixed_days\"")
    note = models.CharField(max_length=255, blank=True, help_text="Why it was changed; shown to the next admin")
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta(TimeStampedModel.Meta):
        ordering = ["key", "scope"]
        constraints = [
            models.UniqueConstraint(fields=["key"], condition=models.Q(scope="global"), name="uniq_setting_global"),
            models.UniqueConstraint(fields=["key", "country"], condition=models.Q(scope="country"), name="uniq_setting_country"),
            models.UniqueConstraint(fields=["key", "store"], condition=models.Q(scope="store"), name="uniq_setting_store"),
            models.CheckConstraint(
                name="setting_scope_matches_target",
                condition=(
                    models.Q(scope="global", country__isnull=True, store__isnull=True)
                    | models.Q(scope="country", country__isnull=False, store__isnull=True)
                    | models.Q(scope="store", store__isnull=False, country__isnull=True)
                ),
            ),
        ]

    def __str__(self):
        return f"{self.key} = {self.value!r} ({self.target})"

    @property
    def target(self) -> str:
        return {"global": "everywhere", "country": f"market {self.country_id}", "store": f"store {self.store_id}"}.get(self.scope, self.scope)

    def clean(self):
        try:
            definition = registry.definition(self.key)
        except registry.UnknownSetting:
            raise ValidationError({"key": f"{self.key!r} is not a registered setting."}) from None
        if self.scope not in definition.scopes:
            raise ValidationError({"scope": f"{definition.label} can only be set for: {', '.join(definition.scopes)}."})
        if self.scope == self.Scope.COUNTRY and not self.country_id:
            raise ValidationError({"country": "Choose the market this applies to."})
        if self.scope == self.Scope.STORE and not self.store_id:
            raise ValidationError({"store": "Choose the store this applies to."})
        if self.scope != self.Scope.COUNTRY and self.country_id:
            raise ValidationError({"country": "Leave empty unless the scope is one market."})
        if self.scope != self.Scope.STORE and self.store_id:
            raise ValidationError({"store": "Leave empty unless the scope is one store."})
        try:
            definition.validate(self.value)
        except ValueError as exc:
            raise ValidationError({"value": f"{definition.label} {exc}."}) from None

    def save(self, *args, **kwargs):
        self.clean()   # an invalid value must not get into the table by any route, not only through admin
        super().save(*args, **kwargs)


class CommissionRule(TimeStampedModel):
    """The platform's cut on a sale. Resolved per order line, most specific first:
    store, then the line's category (or its nearest parent that has a rule), then
    the market, then global. Among rules of the same specificity the latest
    `effective_from` that has already begun wins, so a rate can be scheduled.

    Changing a rate never rewrites history: a posted fee keeps the rate it used.
    """

    class Scope(models.TextChoices):
        GLOBAL = "global", "Whole platform"
        COUNTRY = "country", "One market"
        CATEGORY = "category", "One category (and its subcategories)"
        STORE = "store", "One store"

    scope = models.CharField(max_length=10, choices=Scope.choices, default=Scope.GLOBAL)
    country = models.ForeignKey("markets.Country", on_delete=models.CASCADE, null=True, blank=True, related_name="commission_rules")
    category = models.ForeignKey("catalog.Category", on_delete=models.CASCADE, null=True, blank=True, related_name="commission_rules")
    store = models.ForeignKey("sellers.Store", on_delete=models.CASCADE, null=True, blank=True, related_name="commission_rules")
    rate_bps = models.PositiveSmallIntegerField(
        validators=[MaxValueValidator(10000)],
        help_text="Basis points: 500 = 5.00 %, 1250 = 12.5 %. 0 is allowed (no commission).",
    )
    effective_from = models.DateTimeField(default=timezone.now, help_text="Takes effect from this moment; future dates schedule a change")
    is_active = models.BooleanField(default=True, help_text="Untick to switch a rule off without deleting its history")
    note = models.CharField(max_length=255, blank=True)

    class Meta(TimeStampedModel.Meta):
        ordering = ["scope", "-effective_from"]
        indexes = [models.Index(fields=["scope", "is_active", "-effective_from"])]
        constraints = [
            models.CheckConstraint(
                name="commission_scope_matches_target",
                condition=(
                    models.Q(scope="global", country__isnull=True, category__isnull=True, store__isnull=True)
                    | models.Q(scope="country", country__isnull=False, category__isnull=True, store__isnull=True)
                    | models.Q(scope="category", category__isnull=False, country__isnull=True, store__isnull=True)
                    | models.Q(scope="store", store__isnull=False, country__isnull=True, category__isnull=True)
                ),
            ),
        ]

    def __str__(self):
        return f"{self.rate_bps / 100:g} % — {self.target}"

    @property
    def target(self) -> str:
        return {
            "global": "everywhere", "country": f"market {self.country_id}",
            "category": f"category {self.category_id}", "store": f"store {self.store_id}",
        }.get(self.scope, self.scope)

    def clean(self):
        wanted = {"global": None, "country": "country", "category": "category", "store": "store"}[self.scope]
        for field in ("country", "category", "store"):
            has = bool(getattr(self, f"{field}_id"))
            if field == wanted and not has:
                raise ValidationError({field: "Choose what this rule applies to."})
            if field != wanted and has:
                raise ValidationError({field: f"Leave empty when the scope is {self.get_scope_display().lower()}."})
