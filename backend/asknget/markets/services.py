"""Country lookups shared by the middleware, seeders and other apps."""

from django.conf import settings
from django.db.models import Q

from .models import Country


def countries_qs():
    return Country.objects.select_related("kyc_policy", "return_policy")


def active_countries():
    return list(countries_qs().filter(is_active=True))


def current_or_default() -> Country | None:
    from .context import get_country

    return get_country() or default_country()


def current_currency() -> str:
    """Currency of the active market (default market outside a request)."""
    country = current_or_default()
    return country.currency if country else "USD"


def default_country() -> Country | None:
    """The flagged default, else settings.DEFAULT_COUNTRY, else the first active country."""
    return (
        countries_qs().filter(is_active=True).filter(Q(is_default=True) | Q(pk=settings.DEFAULT_COUNTRY))
        .order_by("-is_default").first()
        or countries_qs().filter(is_active=True).first()
    )


def resolve(*, explicit: str | None = None, host_label: str | None = None) -> tuple[Country | None, bool]:
    """(country, explicit_unknown). Explicit code wins; unknown/inactive explicit
    → (None, True). Then the host's subdomain, then the default. One query."""
    qs = countries_qs().filter(is_active=True)
    if explicit:
        country = qs.filter(pk=explicit.strip().upper()).first()
        return (country, country is None)
    label = (host_label or "").lower()
    rows = list(qs.filter(Q(subdomain=label) | Q(is_default=True) | Q(pk=settings.DEFAULT_COUNTRY)))
    by_sub = next((c for c in rows if label and c.subdomain == label), None)
    if by_sub:
        return by_sub, False
    flagged = next((c for c in rows if c.is_default), None)
    if flagged:
        return flagged, False
    return next(iter(rows), None) or default_country(), False
