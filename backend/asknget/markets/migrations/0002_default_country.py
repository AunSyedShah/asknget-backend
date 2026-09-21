"""Make sure one country exists so later migrations can attach existing stores,
products and orders to it. `seed_lookups` fills in the full ZA / PK profiles;
this only guarantees settings.DEFAULT_COUNTRY resolves."""

from django.conf import settings
from django.db import migrations

PROFILES = {
    "ZA": dict(name="South Africa", currency="ZAR", timezone="Africa/Johannesburg", default_language="en",
               languages=["en", "af", "zu"], phone_prefix="+27", region_label="Province", postal_code_regex=r"^\d{4}$"),
    "PK": dict(name="Pakistan", currency="PKR", timezone="Asia/Karachi", default_language="en",
               languages=["en", "ur"], phone_prefix="+92", region_label="Province", postal_code_regex=r"^\d{5}$"),
}


def create_default(apps, schema_editor):
    Country = apps.get_model("markets", "Country")
    ReturnPolicy = apps.get_model("markets", "ReturnPolicy")
    KycPolicy = apps.get_model("markets", "KycPolicy")
    code = (getattr(settings, "DEFAULT_COUNTRY", "ZA") or "ZA").upper()
    if Country.objects.filter(is_default=True).exists():
        return
    profile = PROFILES.get(code, dict(name=code, currency="USD", timezone="UTC", default_language="en", languages=["en"]))
    country, _ = Country.objects.update_or_create(code=code, defaults={**profile, "subdomain": code.lower(), "is_active": True, "is_default": True})
    ReturnPolicy.objects.get_or_create(country=country, defaults={"window_days": 30, "summary": {"en": "Return eligible items within 30 days"}})
    KycPolicy.objects.get_or_create(country=country)


class Migration(migrations.Migration):
    dependencies = [("markets", "0001_initial")]

    operations = [migrations.RunPython(create_default, migrations.RunPython.noop)]
