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
    """How a seller's identity fields are labelled and validated in this country, and the text above the uploads. Which
    documents are needed is `DocumentRequirement` (no rows = the defaults in `sellers.rules`)."""

    class Mode(models.TextChoices):
        MANUAL = "manual", "Manual review by staff"
        # PROVIDER = "provider"  — reserved for Smile ID / Sumsub etc.

    country = models.OneToOneField(Country, on_delete=models.CASCADE, primary_key=True, related_name="kyc_policy")
    mode = models.CharField(max_length=20, choices=Mode.choices, default=Mode.MANUAL)
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


# --------------------------------------------------------------------------------------
# Seller documents: a catalogue admins extend, and what each market requires (sprint 9)
# --------------------------------------------------------------------------------------

FILE_TYPES = ("application/pdf", "image/jpeg", "image/png")      # what the upload code can check safely (PDF signature, re-encoded images)
ACCOUNT_TYPES = [("individual", "Individual"), ("sole_proprietor", "Sole Proprietor"), ("registered_company", "Registered Company")]


def _default_file_types():
    return list(FILE_TYPES)


class DocumentType(models.Model):
    """A kind of document a seller can be asked for ("Tax Clearance Certificate"). Admins add new ones without a release; which
    market needs which is `DocumentRequirement`. The code is stored on each upload, so it cannot change once used."""

    code = models.SlugField(max_length=30, primary_key=True, help_text='Short and permanent, e.g. "tax_clearance"')
    name = models.JSONField(help_text='Per language: {"en": "Tax Clearance Certificate"}')
    help = models.JSONField(default=dict, blank=True, help_text="Per language: what a good copy looks like")
    accepted_types = models.JSONField(default=_default_file_types, help_text=f"A subset of {list(FILE_TYPES)}")
    max_mb = models.PositiveSmallIntegerField(default=10, help_text="At most the platform limit (SELLER_DOCUMENT_MAX_BYTES)")
    default_step = models.PositiveSmallIntegerField(default=4, choices=[(1, "1 Identity"), (2, "2 Business"), (3, "3 Company"), (4, "4 Bank & address")],
                                                    help_text="The wizard step that asks for it, unless a requirement says otherwise")
    is_active = models.BooleanField(default=True, help_text="Off = no longer asked for or accepted (existing uploads stay)")
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "code"]

    def __str__(self):
        return (self.name or {}).get("en") or self.code

    def clean(self):
        validate_i18n(self.name, field="name")
        if not self.name:
            raise ValidationError({"name": "Give it a name (at least in English)."})
        validate_i18n(self.help, field="help")
        if not isinstance(self.accepted_types, list) or not self.accepted_types or set(self.accepted_types) - set(FILE_TYPES):
            raise ValidationError({"accepted_types": f"A non-empty list from {list(FILE_TYPES)}."})
        if not 1 <= self.max_mb <= settings.SELLER_DOCUMENT_MAX_BYTES // (1024 * 1024):
            raise ValidationError({"max_mb": f"Between 1 and {settings.SELLER_DOCUMENT_MAX_BYTES // (1024 * 1024)}."})


class DocumentRequirement(models.Model):
    """One document one kind of seller in one market uploads. A market (or an account type in it) with no rows uses the platform
    defaults in sellers/rules.py. Changing these applies to applications not yet approved; approved sellers are not asked again."""

    market = models.ForeignKey(Country, on_delete=models.CASCADE, related_name="document_requirements")
    account_type = models.CharField(max_length=20, choices=ACCOUNT_TYPES)
    document_type = models.ForeignKey(DocumentType, on_delete=models.PROTECT, related_name="requirements")
    required = models.BooleanField(default=True, help_text="Off = offered, but the application can be submitted without it")
    step = models.PositiveSmallIntegerField(null=True, blank=True, choices=[(1, "1"), (2, "2"), (3, "3"), (4, "4")], help_text="Empty = the type's default step")
    label = models.JSONField(default=dict, blank=True, help_text='This market\'s name for it, per language: {"en": "CNIC / Passport"}. Empty = the type\'s name')
    instructions = models.JSONField(default=dict, blank=True, help_text="Per language, shown next to the upload")
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["market", "account_type", "sort_order", "document_type"]
        constraints = [models.UniqueConstraint(fields=["market", "account_type", "document_type"], name="uniq_document_requirement")]

    def __str__(self):
        return f"{self.market_id} {self.account_type}: {self.document_type_id}{'' if self.required else ' (optional)'}"

    def clean(self):
        validate_i18n(self.label, field="label")
        validate_i18n(self.instructions, field="instructions")
