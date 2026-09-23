"""A country is a market: what a shopper in one region can see, buy and ship to."""

from django.conf import settings

from catalog.models import Category
from catalog.services import refresh_category_counts
from core.testing import ApiTestCase, make_category, make_country, make_product, make_store, make_user
from marketing.models import Banner
from orders.models import Order, Voucher

CART = "/api/v1/cart"
CHECKOUT = "/api/v1/checkout"
DEFAULT = settings.DEFAULT_COUNTRY


class MarketScopeTests(ApiTestCase):
    @classmethod
    def setUpTestData(cls):
        cls.pk = make_country("PK")
        cls.beauty = make_category("Beauty")
        cls.za_store = make_store(name="Huda Beauty")  # default market
        cls.pk_store = make_store(name="Karachi Cosmetics", country=cls.pk)
        cls.za_product = make_product(cls.za_store, cls.beauty, "ZA Cream", 2599, 4099, stock=5)
        cls.pk_product = make_product(cls.pk_store, cls.beauty, "PK Cream", 1599, None, stock=5, currency="PKR")
        # Shipping belongs to the seller (sprint 6); each store got the defaults.
        cls.za_method = cls.za_store.delivery_options.get(name="Seller Delivery")
        cls.pk_method = cls.pk_store.delivery_options.get(name="Seller Delivery")
        refresh_category_counts()

    def setUp(self):
        self.user = make_user(full_name="John Ali")
        self.token = self.token_for(self.user)

    # Storefront ----------------------------------------------------------

    def test_storefront_is_scoped_to_the_request_market(self):
        names = lambda region: [p["name"] for p in self.get("/api/v1/products", region=region).json()["items"]]
        self.assertEqual(names(None), ["ZA Cream"])
        self.assertEqual(names("PK"), ["PK Cream"])
        self.assertEqual(self.get(f"/api/v1/products/{self.pk_product.slug}").status_code, 404)
        self.assertEqual(self.get(f"/api/v1/products/{self.pk_product.slug}", region="PK").status_code, 200)
        filters = self.get("/api/v1/products/filters", region="PK").json()
        self.assertEqual(filters["currency"], "PKR")

    def test_category_counts_are_per_market(self):
        beauty = Category.objects.get(slug="beauty")
        self.assertEqual(beauty.product_counts, {DEFAULT: 1, "PK": 1})
        self.assertEqual(self.get("/api/v1/categories").json()[0]["product_count"], 1)
        self.assertEqual(self.get("/api/v1/categories", region="PK").json()[0]["product_count"], 1)
        self.assertEqual(beauty.product_count_for(None), 2)

    def test_search_and_recommendations_stay_in_market(self):
        self.assertEqual([p["name"] for p in self.get("/api/v1/products", q="cream", region="PK").json()["items"]], ["PK Cream"])
        self.assertEqual([p["name"] for p in self.get("/api/v1/recommendations", region="PK").json()["items"]], ["PK Cream"])
        similar = self.get(f"/api/v1/products/{self.za_product.slug}/similar").json()
        self.assertNotIn("PK Cream", [p["name"] for p in similar])

    def test_delivery_options_belong_to_the_store_and_banners_follow_the_market(self):
        Banner.objects.create(placement="hero", title="Everywhere", image="banners/x.png")
        Banner.objects.create(placement="hero", title="Pakistan only", image="banners/y.png", country=self.pk)
        # A cart is single-market, so its delivery options are whichever store's it holds.
        self._add(self.za_product, token=self.token)
        body = self.get(CART, token=self.token).json()
        self.assertEqual([d["store_name"] for d in body["deliveries"]], ["Huda Beauty"])
        self.assertTrue({"Express Delivery", "Seller Delivery", "Customer Pickup"} <= {o["name"] for o in body["deliveries"][0]["options"]})
        self.assertEqual([b["title"] for b in self.get("/api/v1/banners").json()], ["Everywhere"])
        self.assertEqual(sorted(b["title"] for b in self.get("/api/v1/banners", region="PK").json()), ["Everywhere", "Pakistan only"])

    # Cart ----------------------------------------------------------------

    def _add(self, product, region=None, token=None, guest=None):
        headers = {"X-Cart-Token": guest} if guest else {}
        return self.client.post(f"{CART}/items", {"variant_id": str(product.variants.first().id), "qty": 1}, content_type="application/json",
                                headers={**self._headers(token, region), **headers})

    def test_one_market_per_cart(self):
        body = self._add(self.za_product, token=self.token).json()
        self.assertEqual(body["country"], DEFAULT)
        self.assertEqual(body["summary"]["currency"], "USD")  # product currency as created by the fixture
        self.assertEqual(body["summary"]["shipping_cents"], self.za_method.price_cents)
        res = self._add(self.pk_product, region="PK", token=self.token)
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()["detail"], "cart_region_mismatch")
        # another store's option cannot be chosen for this cart
        res = self.patch(f"{CART}/delivery", {"store_id": str(self.pk_store.id), "option_id": str(self.pk_method.id)}, self.token)
        self.assertEqual(res.status_code, 404)
        # clear → switch
        body = self.delete(f"{CART}/items", self.token).json()
        self.assertEqual((body["items"], body["country"]), ([], DEFAULT))  # empty → the request's market
        body = self._add(self.pk_product, region="PK", token=self.token).json()
        self.assertEqual((body["country"], body["summary"]["shipping_cents"]), ("PK", self.pk_method.price_cents))

    def test_guest_lines_from_another_market_do_not_merge(self):
        guest = self.client.get(CART, headers={"X-Region": "PK"}).json()["cart_token"]
        self._add(self.pk_product, region="PK", guest=guest)
        self._add(self.za_product, token=self.token)
        body = self.client.get(CART, headers={**self._headers(self.token), "X-Cart-Token": guest}).json()
        self.assertEqual([l["product_name"] for l in body["items"]], ["ZA Cream"])

    def test_voucher_can_be_market_bound(self):
        Voucher.objects.create(code="PKONLY", type="percent", value=10, country=self.pk)
        self._add(self.za_product, token=self.token)
        res = self.post(f"{CART}/voucher", {"code": "PKONLY"}, self.token)
        self.assertEqual(res.status_code, 422)
        self.assertIn("region", res.json()["detail"])

    # Checkout ------------------------------------------------------------

    CONTACT = {"first_name": "John", "last_name": "Ali", "email": "john.ali@gmail.com", "phone": "+92 300 1234567"}

    def _payload(self, method, **over):
        return {"contact": self.CONTACT, "address": {"address": "1 Main St", "city": "Cape Town", "postal_code": "6001"},
                "deliveries": [{"store_id": str(method.store_id), "option_id": str(method.id)}],
                "payment_method_type": "card", **over}

    def test_checkout_snapshots_market_and_return_window(self):
        self._add(self.za_product, token=self.token)
        res = self.post(CHECKOUT, self._payload(self.za_method), self.token)
        self.assertEqual(res.status_code, 201, res.content)
        order = Order.objects.get(order_number=res.json()["order"]["order_number"])
        self.assertEqual((order.country_id, order.return_window_days), (DEFAULT, 30))
        self.assertEqual(order.shipping_address["country"], DEFAULT)  # blank address country = the market
        detail = self.get(f"/api/v1/me/orders/{order.id}", token=self.token).json()
        self.assertEqual((detail["country"], detail["return_window_days"], detail["return_eligible_until"]), (DEFAULT, 30, None))

    def test_checkout_refuses_other_market_delivery_and_address(self):
        self._add(self.za_product, token=self.token)
        # the PK store has nothing in this cart, so its option cannot be applied
        self.assertEqual(self.post(CHECKOUT, self._payload(self.pk_method), self.token).status_code, 404)
        res = self.post(CHECKOUT, self._payload(self.za_method, address={"address": "1 Mall Rd", "city": "Lahore", "postal_code": "54000", "country": "PK"}), self.token)
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()["detail"], "ships_outside_region")
        # the cart's market wins even if the request comes from another region's domain
        res = self.post(CHECKOUT, self._payload(self.za_method), self.token, region="PK")
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(Order.objects.get(order_number=res.json()["order"]["order_number"]).country_id, DEFAULT)

    def test_address_popup_defaults_to_the_market(self):
        res = self.post("/api/v1/me/addresses", {"full_name": "John Ali", "phone": "+923001234567", "street_address": "12 Mall Rd",
                                                  "city": "Lahore", "postal_code": "54000"}, self.token, region="PK")
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()["country"], "PK")


class SellerMarketTests(ApiTestCase):
    def test_application_and_store_carry_the_market(self):
        pk = make_country("PK")
        res = self.post("/api/v1/auth/seller/register", {"email": "s@example.com", "password": "Str0ngPassw0rd!", "confirm_password": "Str0ngPassw0rd!",
                                                          "phone": "+92 300 1234567", "account_type": "individual",
                                                          "accept_terms": True}, region="PK")
        self.assertEqual(res.status_code, 201, res.content)
        from sellers.models import SellerApplication

        app = SellerApplication.objects.get(user__email="s@example.com")
        self.assertEqual((app.country_id, app.user.country), ("PK", "PK"))

    def test_portal_products_are_priced_in_the_store_market(self):
        pk = make_country("PK")
        owner = make_user(is_seller=True)
        store = make_store(owner=owner, name="Karachi Cosmetics", country=pk)
        beauty = make_category("Beauty")
        token = self.token_for(owner)
        res = self.post("/api/v1/seller/products", {"name": "Kajal", "category_id": str(beauty.id), "short_description": "x"}, token)
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()["currency"], "PKR")
        # the dashboard reports in the store's market even from another region's domain
        body = self.get("/api/v1/seller/dashboard", token=token, region=DEFAULT).json()
        self.assertEqual(body["store"]["currency"], "PKR")
