"""Public shape of a market: what the storefront needs to render a country."""

from typing import Optional

from ninja import Field, Schema


class CountryOut(Schema):
    code: str
    name: str
    subdomain: str
    currency: str
    timezone: str
    default_language: str
    languages: list[str]
    phone_prefix: str
    region_label: str


class ReturnPolicyOut(Schema):
    window_days: int = Field(description="Change of mind: days after delivery (0 = not accepted)")
    defect_days: int = Field(description="Defective / wrong / not as described: days after delivery (0 = not accepted)")
    restocking_fee_bps: int
    summary: str
    terms: str
    url: str


class RegionOut(Schema):
    """`GET /regions/current` — the market, language and timezone this request resolved to."""

    country: CountryOut
    language: str
    timezone: str
    currency: str
    postal_code_regex: str
    return_policy: Optional[ReturnPolicyOut] = None
