"""Shared fixtures + HTTP helpers for app test suites."""

import itertools

from django.test import TestCase
from django.utils import timezone

from django.conf import settings

from accounts.models import User
from catalog.models import Brand, Category, Product, ProductOptionType, ProductOptionValue, ProductVariant
from markets.models import Country, KycPolicy, ReturnPolicy
from sellers.models import SellerApplication, Store

_seq = itertools.count(1)

PASSWORD = "Str0ngPassw0rd!"


def make_user(email=None, **extra) -> User:
    n = next(_seq)
    return User.objects.create_user(email=email or f"user{n}@example.com", password=PASSWORD, **extra)


def make_country(code="PK", **extra) -> Country:
    """A second market for cross-region tests (the default one comes from the migration)."""
    profiles = {"PK": dict(name="Pakistan", currency="PKR", timezone="Asia/Karachi", languages=["en", "ur"], phone_prefix="+92", sort_order=2)}
    country, created = Country.objects.get_or_create(code=code, defaults={**profiles.get(code, dict(name=code, currency="USD", timezone="UTC")), **extra})
    if created:
        ReturnPolicy.objects.create(country=country, window_days=7, summary={"en": "Return eligible items within 7 days"})
        KycPolicy.objects.create(country=country)
    return country


def make_store(owner=None, name="Huda Beauty", status=Store.StoreStatus.ACTIVE, country=None) -> Store:
    """`country`: a Country or ISO code; defaults to settings.DEFAULT_COUNTRY (created by the markets migration)."""
    owner = owner or make_user(is_seller=True)
    country_id = getattr(country, "code", country) or settings.DEFAULT_COUNTRY
    app = SellerApplication.objects.create(user=owner, account_type="registered_company", status="active", store_display_name=name, country_id=country_id)
    n = next(_seq)
    return Store.objects.create(owner=owner, application=app, country_id=country_id, display_name=name, slug=f"{name.lower().replace(' ', '-')}-{n}", status=status)


def make_category(name, parent=None) -> Category:
    return Category.objects.create(name=name, slug=name.lower().replace(" & ", "-").replace(" ", "-"), parent=parent)


def make_product(store, category, name="NARS BB Cream", price=2599, compare_at=4099, brand=None, *, options=None,
                 status=Product.ProductStatus.ACTIVE, stock=10, **extra) -> Product:
    """options: {"Size": ["15 ml", "30 ml"], "Shade": ["Ivory", "Natural"]} → one variant per combination."""
    n = next(_seq)
    product = Product.objects.create(
        store=store, country_id=store.country_id, category=category, brand=brand, name=name, slug=f"{name.lower().replace(' ', '-')}-{n}",
        price_cents=price, compare_at_price_cents=compare_at, status=status,
        published_at=timezone.now() if status == Product.ProductStatus.ACTIVE else None, **extra,
    )
    combos = [[]]
    for i, (opt_name, values) in enumerate((options or {}).items()):
        ot = ProductOptionType.objects.create(product=product, name=opt_name, sort_order=i)
        vals = [ProductOptionValue.objects.create(option_type=ot, value=v, sort_order=j) for j, v in enumerate(values)]
        combos = [c + [v] for c in combos for v in vals]
    for i, combo in enumerate(combos):
        variant = ProductVariant.objects.create(product=product, sku=f"SKU-{n}-{i}", stock_qty=stock)
        variant.option_values.set(combo)
    return product


class ApiTestCase(TestCase):
    """Thin JSON client helpers. Pass `token` to send a Bearer header and `region`
    to send X-Region (otherwise the request resolves to settings.DEFAULT_COUNTRY)."""

    def _headers(self, token, region=None):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        if region:
            headers["X-Region"] = region
        return headers

    def get(self, url, token=None, region=None, **params):
        return self.client.get(url, params, headers=self._headers(token, region))

    def post(self, url, data=None, token=None, region=None):
        return self.client.post(url, data=data or {}, content_type="application/json", headers=self._headers(token, region))

    def put(self, url, data=None, token=None, region=None):
        return self.client.put(url, data=data or {}, content_type="application/json", headers=self._headers(token, region))

    def patch(self, url, data=None, token=None, region=None):
        return self.client.patch(url, data=data or {}, content_type="application/json", headers=self._headers(token, region))

    def delete(self, url, token=None, region=None):
        return self.client.delete(url, headers=self._headers(token, region))

    def login(self, email, password=PASSWORD) -> str:
        res = self.post("/api/v1/auth/login", {"email": email, "password": password})
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()["tokens"]["access_token"]

    def token_for(self, user) -> str:
        return self.login(user.email)
