"""Reusable field types for Ninja/pydantic schemas.

Import these instead of re-declaring regexes in every schema, so a rule change
(e.g. allowing local phone formats) happens in exactly one place.
"""

import re
from datetime import date
from typing import Annotated

from pydantic import AfterValidator, Field, StringConstraints

_PHONE_RE = re.compile(r"^\+[1-9]\d{6,14}$")  # E.164
_OTP_RE = re.compile(r"^\d{6}$")  # design: "6-digit verification code"


def _normalize_phone(value: str) -> str:
    """Strip spaces/dashes/parens so '+ (92) 300 - 1234567' → '+923001234567'."""
    cleaned = re.sub(r"[\s\-().]", "", value)
    if not _PHONE_RE.match(cleaned):
        raise ValueError("Phone must be in international format, e.g. +923001234567")
    return cleaned


def _normalize_email(value: str) -> str:
    return value.strip().lower()


def _past_date(value: date) -> date:
    if value >= date.today():
        raise ValueError("Date must be in the past")
    return value


def _password_strength(value: str) -> str:
    # Django's AUTH_PASSWORD_VALIDATORS run in the service layer (they need the user);
    # this is the cheap, user-independent subset so Swagger documents it.
    if len(value) < 8:
        raise ValueError("Password must be at least 8 characters")
    if value.isdigit():
        raise ValueError("Password cannot be entirely numeric")
    return value


def _country_code(value: str) -> str:
    value = value.strip().upper()
    if not re.fullmatch(r"[A-Z]{2}", value):
        raise ValueError("Country must be an ISO 3166-1 alpha-2 code, e.g. PK")
    return value


PhoneStr = Annotated[str, AfterValidator(_normalize_phone)]
EmailStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=254), AfterValidator(_normalize_email)]
PasswordStr = Annotated[str, Field(min_length=8, max_length=128), AfterValidator(_password_strength)]
OtpCode = Annotated[str, StringConstraints(strip_whitespace=True, pattern=_OTP_RE.pattern)]
PastDate = Annotated[date, AfterValidator(_past_date)]
NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
CountryCode = Annotated[str, AfterValidator(_country_code)]
PostalCode = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=20)]
Last4 = Annotated[str, StringConstraints(pattern=r"^\d{4}$")]
