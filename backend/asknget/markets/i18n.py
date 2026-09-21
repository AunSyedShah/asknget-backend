"""Resolve per-language JSON ({"en": "...", "ur": "..."}) for the active language."""

from django.utils import translation

from .context import get_country


def pick(mapping, language: str | None = None, country=None, default: str = "") -> str:
    """language → country default → "en" → first value → `default`."""
    if not mapping:
        return default
    if not isinstance(mapping, dict):
        return str(mapping)
    language = language or translation.get_language() or ""
    country = country or get_country()
    candidates = [language, language.split("-")[0], country.default_language if country else "", "en"]
    for code in candidates:
        if code and mapping.get(code):
            return mapping[code]
    return next((v for v in mapping.values() if v), default)
