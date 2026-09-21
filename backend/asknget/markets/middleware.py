"""Resolve market, language and timezone for every API request.

Order (first hit wins):
  country   X-Region header / ?region=   →  host subdomain (za.asknget.com)  →  default country
  language  X-Language / ?lang=          →  Accept-Language ∩ country.languages →  country default
  timezone  X-Timezone (valid IANA name) →  country.timezone

An explicit region that is unknown or inactive is a 400 `unknown_region`; an
unknown subdomain (localhost, container names, IPs) just falls through to the
default. Only `/api/` paths are resolved — Django admin and static files are
untouched. Everything activated here is reset in `finally`, because gunicorn's
thread pool and the test client reuse threads between requests.
"""

from zoneinfo import ZoneInfo

from django.http import JsonResponse
from django.utils import timezone, translation
from django.utils.cache import patch_vary_headers
from django.utils.translation.trans_real import parse_accept_lang_header

from . import context, services
from .models import available_timezones

REGION_HEADER = "X-Region"
LANGUAGE_HEADER = "X-Language"
TIMEZONE_HEADER = "X-Timezone"
API_PREFIX = "/api/"


def _match_language(candidates, offered: list[str]) -> str | None:
    """First candidate (exact or base language) that the country offers."""
    offered_l = [o.lower() for o in offered]
    for code in candidates:
        code = (code or "").strip().lower()
        if not code:
            continue
        if code in offered_l:
            return offered[offered_l.index(code)]
        base = code.split("-")[0]
        if base in offered_l:
            return offered[offered_l.index(base)]
    return None


def pick_language(request, country) -> str:
    offered = list(country.languages or [country.default_language])
    explicit = request.headers.get(LANGUAGE_HEADER) or request.GET.get("lang")
    if explicit:
        hit = _match_language([explicit], offered)
        if hit:
            return hit
    accept = request.META.get("HTTP_ACCEPT_LANGUAGE", "")
    hit = _match_language([code for code, _q in parse_accept_lang_header(accept)], offered) if accept else None
    return hit or country.default_language


def pick_timezone(request, country) -> str:
    explicit = (request.headers.get(TIMEZONE_HEADER) or "").strip()
    if explicit and explicit in available_timezones():
        return explicit
    return country.timezone


class RegionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.path.startswith(API_PREFIX):
            return self.get_response(request)

        explicit = request.headers.get(REGION_HEADER) or request.GET.get("region")
        host_label = request.get_host().split(":")[0].split(".")[0]
        country, unknown = services.resolve(explicit=explicit, host_label=host_label)
        if unknown:
            return JsonResponse({"detail": "unknown_region"}, status=400)

        request.country = country
        if country is None:  # no country configured at all — run unscoped
            request.language = translation.get_language()
            request.timezone = timezone.get_current_timezone_name()
            return self.get_response(request)

        language = pick_language(request, country)
        tz_name = pick_timezone(request, country)
        request.language = language
        request.timezone = tz_name

        token = context.activate(country)
        translation.activate(language)
        timezone.activate(ZoneInfo(tz_name))
        try:
            response = self.get_response(request)
        finally:
            timezone.deactivate()
            translation.deactivate()
            context.deactivate(token)

        response.headers.setdefault("Content-Language", language)
        response.headers[REGION_HEADER] = country.code
        patch_vary_headers(response, (REGION_HEADER, LANGUAGE_HEADER, TIMEZONE_HEADER, "Accept-Language"))
        return response
