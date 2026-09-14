"""Shared fixtures + HTTP helpers for app test suites."""

import itertools

from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from catalog.models import Brand, Category, Product, ProductOptionType, ProductOptionValue, ProductVariant
from sellers.models import SellerApplication, Store

_seq = itertools.count(1)

PASSWORD = "Str0ngPassw0rd!"


def make_user(email=None, **extra) -> User:
    n = next(_seq)
    return User.objects.create_user(email=email or f"user{n}@example.com", password=PASSWORD, **extra)


def make_store(owner=None, name="Huda Beauty", status=Store.StoreStatus.ACTIVE) -> Store:
    owner = owner or make_user(is_seller=True)
    app = SellerApplication.objects.create(user=owner, account_type="registered_company", status="active", store_display_name=name)
    n = next(_seq)
    return Store.objects.create(owner=owner, application=app, display_name=name, slug=f"{name.lower().replace(' ', '-')}-{n}", status=status)


def make_category(name, parent=None) -> Category:
    return Category.objects.create(name=name, slug=name.lower().replace(" & ", "-").replace(" ", "-"), parent=parent)


def make_product(store, category, name="NARS BB Cream", price=2599, compare_at=4099, brand=None, *, options=None,
                 status=Product.ProductStatus.ACTIVE, stock=10, **extra) -> Product:
    """options: {"Size": ["15 ml", "30 ml"], "Shade": ["Ivory", "Natural"]} → one variant per combination."""
    n = next(_seq)
    product = Product.objects.create(
        store=store, category=category, brand=brand, name=name, slug=f"{name.lower().replace(' ', '-')}-{n}",
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
    """Thin JSON client helpers. Pass `token` to send a Bearer header."""

    def _headers(self, token):
        return {"Authorization": f"Bearer {token}"} if token else {}

    def get(self, url, token=None, **params):
        return self.client.get(url, params, headers=self._headers(token))

    def post(self, url, data=None, token=None):
        return self.client.post(url, data=data or {}, content_type="application/json", headers=self._headers(token))

    def put(self, url, data=None, token=None):
        return self.client.put(url, data=data or {}, content_type="application/json", headers=self._headers(token))

    def patch(self, url, data=None, token=None):
        return self.client.patch(url, data=data or {}, content_type="application/json", headers=self._headers(token))

    def delete(self, url, token=None):
        return self.client.delete(url, headers=self._headers(token))

    def login(self, email, password=PASSWORD) -> str:
        res = self.post("/api/v1/auth/login", {"email": email, "password": password})
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()["tokens"]["access_token"]

    def token_for(self, user) -> str:
        return self.login(user.email)
