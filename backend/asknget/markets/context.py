"""The active market for the current request.

Mirrors Django's `translation.activate()` / `timezone.activate()`: middleware
activates the resolved `Country`, everything downstream asks `get_country()`,
and a `finally` resets it. Outside a request (management commands, signals,
`setUpTestData`) nothing is active and `get_country()` returns None, which the
catalogue treats as "all markets" — no code path silently sees one market.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from zoneinfo import ZoneInfo

from django.utils import timezone, translation

_current: ContextVar = ContextVar("asknget_country", default=None)


def activate(country):
    """Make `country` current; returns a token for `deactivate()`."""
    return _current.set(country)


def deactivate(token):
    _current.reset(token)


def get_country():
    return _current.get()


def require_country():
    country = get_country()
    if country is None:
        raise RuntimeError("No market is active — call markets.context.activate() or send X-Region")
    return country


@contextmanager
def region(country, *, language=None):
    """Activate a market (and its timezone / language) for a block of code —
    used by tests, seeders and jobs that act on behalf of one country.
    Accepts a `Country` or an ISO code."""
    from .models import Country

    if not hasattr(country, "code"):
        country = Country.objects.get(pk=str(country).upper())
    token = activate(country)
    timezone.activate(ZoneInfo(country.timezone))
    translation.activate(language or country.default_language)
    try:
        yield country
    finally:
        translation.deactivate()
        timezone.deactivate()
        deactivate(token)
