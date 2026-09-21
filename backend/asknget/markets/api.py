"""Public region endpoints — no auth; the middleware has already resolved the market."""

from ninja import Router
from ninja.errors import HttpError

from . import services
from .i18n import pick
from .schemas import CountryOut, RegionOut

regions_router = Router(tags=["regions"])


def region_payload(request) -> dict:
    country = request.country
    policy = country.returns
    return {
        "country": country,
        "language": request.language,
        "timezone": request.timezone,
        "currency": country.currency,
        "postal_code_regex": country.postal_code_regex,
        "return_policy": (
            {"window_days": policy.window_days, "summary": pick(policy.summary, request.language, country), "url": policy.url}
            if policy and policy.is_active else None
        ),
    }


@regions_router.get("", response=list[CountryOut], summary="Active countries (country switcher)")
def regions(request):
    return services.active_countries()


@regions_router.get("/current", response=RegionOut, summary="Market, language and timezone resolved for this request")
def current(request):
    if getattr(request, "country", None) is None:
        raise HttpError(503, "No market configured — run `manage.py seed_lookups`")
    return region_payload(request)
