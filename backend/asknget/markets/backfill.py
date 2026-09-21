"""Helpers for the per-app migrations that attach existing rows to a market.

Pattern (one migration per app): AddField(null=True) -> RunPython(backfill) ->
AlterField(null=False). Rows created before markets existed go to the default
country (the row flagged is_default, else settings.DEFAULT_COUNTRY).
"""

from django.conf import settings


def default_country(apps):
    Country = apps.get_model("markets", "Country")
    return (
        Country.objects.filter(is_default=True).first()
        or Country.objects.filter(pk=settings.DEFAULT_COUNTRY).first()
        or Country.objects.order_by("sort_order", "code").first()
    )


def backfill(app_label, *model_names, field="country"):
    """RunPython callable: set `field` to the default country where it is NULL."""

    def run(apps, schema_editor):
        default = default_country(apps)
        for name in model_names:
            model = apps.get_model(app_label, name)
            qs = model.objects.filter(**{f"{field}__isnull": True})
            if default is None:
                if qs.exists():
                    raise RuntimeError(f"{app_label}.{name} has rows but no markets.Country exists — run migrations for markets first")
                continue
            qs.update(**{field: default})

    return run
