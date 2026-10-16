"""Countries as markets.

A country is a separate market: it has its own currency, timezone, languages,
KYC policy and return policy, and every store (hence every product, cart and
order) belongs to exactly one. Staff maintain all of this in Django admin —
nothing here is code-configured.

Localised text is a per-language JSON mapping, e.g. {"en": "...", "ur": "..."};
`markets.i18n.pick()` resolves it for the active language with sensible fallbacks.
"""

import re
import zoneinfo

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

_TIMEZONES: set[str] | None = None


def available_timezones() -> set[str]:
    global _TIMEZONES
    if _TIMEZONES is None:
        _TIMEZONES = zoneinfo.available_timezones()
    return _TIMEZONES


def language_codes() -> list[str]:
    return [code for code, _ in settings.LANGUAGES]


def validate_i18n(value, *, field="value"):
    """i18n JSON must be {language_code: str}; keys limited to settings.LANGUAGES."""
    if value in (None, {}):
        return
    if not isinstance(value, dict):
        raise ValidationError({field: "Must be a JSON object keyed by language code."})
    bad = sorted(set(value) - set(language_codes()))
    if bad:
        raise ValidationError({field: f"Unknown language code(s): {', '.join(bad)}. Allowed: {', '.join(language_codes())}"})
    if any(not isinstance(v, str) for v in value.values()):
        raise ValidationError({field: "Every translation must be a string."})


class Country(models.Model):
    code = models.CharField(max_length=2, primary_key=True, help_text="ISO 3166-1 alpha-2, e.g. ZA")
    name = models.CharField(max_length=80)
    subdomain = models.SlugField(max_length=10, unique=True, blank=True, help_text="za → za.asknget.com (blank = the code, lower-case)")
    currency = models.CharField(max_length=3, help_text="ISO 4217, e.g. ZAR")
    timezone = models.CharField(max_length=64, default="UTC", help_text="IANA name, e.g. Africa/Johannesburg")
    default_language = models.CharField(max_length=10, default="en")
    languages = models.JSONField(default=list, blank=True, help_text='Offered languages in order, e.g. ["en", "af", "zu"]')
    phone_prefix = models.CharField(max_length=6, blank=True, help_text="+27")
    region_label = models.CharField(max_length=30, default="Province", help_text="What the address form calls the first-level region")
    postal_code_regex = models.CharField(max_length=100, blank=True, help_text="Blank = any")
    is_active = models.BooleanField(default=True, help_text="Inactive countries are not resolvable from the API")
    is_default = models.BooleanField(default=False, help_text="Used when a request carries no region (bare domain, tests)")
    sort_order = models.PositiveSmallIntegerField(default=0)
    site_info = models.JSONField(
        default=dict, blank=True,
        help_text='This market\'s footer / Contact Us details, over the platform-wide ones: {"contact_phone": "+27 11 555 0123", "contact_email": "...", '
                  '"address": "...", "social": {"facebook": "https://..."}}. Leave a key out to use the platform-wide value.')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name_plural = "countries"

    def __str__(self):
        return f"{self.name} ({self.code})"

    def clean(self):
        self.code = (self.code or "").upper()
        self.currency = (self.currency or "").upper()
        if not self.subdomain:
            self.subdomain = self.code.lower()
        if not re.fullmatch(r"[A-Z]{2}", self.code):
            raise ValidationError({"code": "Two-letter ISO code."})
        if not re.fullmatch(r"[A-Z]{3}", self.currency):
            raise ValidationError({"currency": "Three-letter ISO 4217 code."})
        if self.timezone not in available_timezones():
            raise ValidationError({"timezone": f"Unknown IANA timezone '{self.timezone}'."})
        if not self.languages:
            self.languages = [self.default_language]
        bad = sorted(set(self.languages) - set(language_codes()))
        if bad:
            raise ValidationError({"languages": f"Unknown language code(s): {', '.join(bad)}. Allowed: {', '.join(language_codes())}"})
        if self.default_language not in self.languages:
            raise ValidationError({"default_language": "Must be one of `languages`."})
        allowed = {"contact_phone", "contact_email", "address", "social"}
        if not isinstance(self.site_info, dict) or set(self.site_info) - allowed:
            raise ValidationError({"site_info": f"An object with only these keys: {', '.join(sorted(allowed))}."})
        if not isinstance(self.site_info.get("social", {}), dict):
            raise ValidationError({"site_info": "social must be an object of name: link."})
        if self.postal_code_regex:
            try:
                re.compile(self.postal_code_regex)
            except re.error as exc:
                raise ValidationError({"postal_code_regex": f"Invalid regex: {exc}"})

    def save(self, *args, **kwargs):
        self.code = self.code.upper()
        self.currency = self.currency.upper()
        if not self.subdomain:
            self.subdomain = self.code.lower()
        if not self.languages:
            self.languages = [self.default_language]
        super().save(*args, **kwargs)
        if self.is_default:
            Country.objects.filter(is_default=True).exclude(pk=self.pk).update(is_default=False)

    # Convenience accessors that tolerate a missing policy row.
    @property
    def kyc(self):
        try:
            return self.kyc_policy
        except KycPolicy.DoesNotExist:
            return None

    @property
    def returns(self):
        try:
            return self.return_policy
        except ReturnPolicy.DoesNotExist:
            return None

    @property
    def return_window_days(self) -> int:
        policy = self.returns
        return policy.window_days if policy and policy.is_active else 0


class KycPolicy(models.Model):
    """Which documents a seller in this country must upload and how the identity
    fields are labelled / validated. Empty JSON = use the defaults in
    `sellers.rules` (so a new country works before anyone customises it)."""

    class Mode(models.TextChoices):
        MANUAL = "manual", "Manual review by staff"
        # PROVIDER = "provider"  — reserved for Smile ID / Sumsub etc.

    country = models.OneToOneField(Country, on_delete=models.CASCADE, primary_key=True, related_name="kyc_policy")
    mode = models.CharField(max_length=20, choices=Mode.choices, default=Mode.MANUAL)
    required_documents = models.JSONField(
        default=dict, blank=True,
        help_text='Per account type, e.g. {"individual": ["id_passport", "proof_of_address"], "registered_company": [...]}. '
                  "Empty = platform defaults.",
    )
    document_labels = models.JSONField(
        default=dict, blank=True,
        help_text='Per document type, i18n: {"id_passport": {"en": "SA ID / Passport"}}',
    )
    id_number_label = models.CharField(max_length=60, blank=True, help_text='e.g. "SA ID / Passport number", "CNIC / Passport number"')
    id_number_regex = models.CharField(max_length=100, blank=True, help_text="Blank = not validated")
    tax_id_label = models.CharField(max_length=60, blank=True, help_text='e.g. "VAT number", "NTN"')
    company_registration_label = models.CharField(max_length=60, blank=True, help_text='e.g. "CIPC registration number", "SECP registration number"')
    instructions = models.JSONField(default=dict, blank=True, help_text="i18n text shown above the document uploads")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "KYC policies"

    def __str__(self):
        return f"KYC policy – {self.country_id}"

    def clean(self):
        if self.required_documents and not isinstance(self.required_documents, dict):
            raise ValidationError({"required_documents": "Must be a JSON object keyed by account type."})
        for key, docs in (self.required_documents or {}).items():
            if not isinstance(docs, list) or any(not isinstance(d, str) for d in docs):
                raise ValidationError({"required_documents": f"'{key}' must map to a list of document types."})
        if self.document_labels and not isinstance(self.document_labels, dict):
            raise ValidationError({"document_labels": "Must be a JSON object keyed by document type."})
        for doc_type, mapping in (self.document_labels or {}).items():
            validate_i18n(mapping, field="document_labels")
        validate_i18n(self.instructions, field="instructions")
        if self.id_number_regex:
            try:
                re.compile(self.id_number_regex)
            except re.error as exc:
                raise ValidationError({"id_number_regex": f"Invalid regex: {exc}"})


class ReturnPolicy(models.Model):
    """The buyer-facing return rule for a country. The window is snapshotted on
    every order at checkout, so changing it here never rewrites history."""

    country = models.OneToOneField(Country, on_delete=models.CASCADE, primary_key=True, related_name="return_policy")
    window_days = models.PositiveSmallIntegerField(
        default=30, help_text="Change of mind: days after delivery the buyer may ask to return an item they simply don't want. 0 = not accepted. "
                              "Design: 'Return eligible items within 30 days'")
    defect_days = models.PositiveSmallIntegerField(
        default=30, help_text="Defective, wrong or not-as-described items: days after delivery. Often longer than the change-of-mind window "
                              "(consumer law may require it). 0 = not accepted.")
    restocking_fee_bps = models.PositiveSmallIntegerField(
        default=0, help_text="Kept from a change-of-mind refund, in basis points of the item value (100 = 1 %). Never charged for a defect or wrong item.")
    summary = models.JSONField(default=dict, blank=True, help_text='i18n one-liner, e.g. {"en": "Return eligible items within 30 days"}')
    terms = models.JSONField(default=dict, blank=True, help_text="i18n full terms shown on the return form")
    url = models.URLField(blank=True, help_text="Full policy page on the storefront")
    is_active = models.BooleanField(default=True, help_text="Off = no returns of any kind in this market")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "return policies"

    def __str__(self):
        return f"Return policy – {self.country_id} ({self.window_days} d)"

    def clean(self):
        validate_i18n(self.summary, field="summary")
        validate_i18n(self.terms, field="terms")
        if self.restocking_fee_bps > 10000:
            raise ValidationError({"restocking_fee_bps": "At most 10000 (100 %)."})
