"""Seller-defined shipping (sprint 6).

Each seller sets their own delivery options, so a cart spanning several sellers has
several shipping lines. That is the consequence of the chosen model and the thing
most worth pinning down.
"""

from core.testing import ApiTestCase, make_category, make_product, make_store, make_user
from orders.models import Order, OrderDelivery
from sellers.models import StoreDeliveryOption

CART = "/api/v1/cart"
CHECKOUT = "/api/v1/checkout"
SELLER = "/api/v1/seller/delivery-options"
CONTACT = {"first_name": "John", "last_name": "Ali", "email": "john.ali@gmail.com", "phone": "+92 300 1234567"}
ADDRESS = {"address": "123 Main Street", "city": "Cape Town", "postal_code": "6001"}


class DeliveryTestCase(ApiTestCase):
    @classmethod
    def setUpTestData(cls):
        cls.beauty = make_category("Beauty")
        cls.owner_a = make_user(is_seller=True, full_name="Sirosh Bashir")
        cls.store_a = make_store(owner=cls.owner_a, name="Huda Beauty")
        cls.store_b = make_store(name="Other Co")
        cls.prod_a = make_product(cls.store_a, cls.beauty, "BB Cream", 2599, None, stock=5)
        cls.prod_b = make_product(cls.store_b, cls.beauty, "Lip Gloss", 1299, None, stock=5)

    def setUp(self):
        self.buyer = make_user(full_name="John Ali")
        self.token = self.token_for(self.buyer)
        self.seller_token = self.token_for(self.owner_a)

    def add(self, product, qty=1):
        res = self.post(f"{CART}/items", {"variant_id": str(product.variants.first().id), "qty": qty}, self.token)
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()

    def option(self, store, name):
        return store.delivery_options.get(name=name)


class StoreDefaultsTests(DeliveryTestCase):
    def test_every_store_starts_with_the_designs_three_options(self):
        names = list(self.store_a.delivery_options.values_list("name", flat=True))
        self.assertEqual(sorted(names), ["Customer Pickup", "Express Delivery", "Seller Delivery"])
        self.assertEqual(self.option(self.store_a, "Customer Pickup").kind, "pickup")

    def test_seller_crud(self):
        rows = self.get(SELLER, self.seller_token).json()
        self.assertEqual(len(rows), 3)
        res = self.post(SELLER, {"name": "Overnight", "eta_label": "next day", "price_cents": 4999}, self.seller_token)
        self.assertEqual(res.status_code, 201, res.content)
        option_id = res.json()["id"]
        self.assertEqual(self.post(SELLER, {"name": "overnight", "price_cents": 1}, self.seller_token).status_code, 409)
        patched = self.patch(f"{SELLER}/{option_id}", {"price_cents": 3999}, self.seller_token).json()
        self.assertEqual(patched["price_cents"], 3999)
        self.assertEqual(self.delete(f"{SELLER}/{option_id}", self.seller_token).status_code, 204)
        self.assertEqual(len(self.get(SELLER, self.seller_token).json()), 3)

    def test_a_store_cannot_end_up_with_nothing_to_ship_with(self):
        for name in ("Express Delivery", "Seller Delivery"):
            self.assertEqual(self.delete(f"{SELLER}/{self.option(self.store_a, name).id}", self.seller_token).status_code, 204)
        last = self.option(self.store_a, "Customer Pickup")
        res = self.delete(f"{SELLER}/{last.id}", self.seller_token)
        self.assertEqual(res.status_code, 409)
        self.assertIn("at least one", res.json()["detail"])

    def test_an_option_in_use_is_deactivated_not_deleted(self):
        express = self.option(self.store_a, "Express Delivery")
        self.prod_a.delivery_options.set([express])
        self.assertEqual(self.delete(f"{SELLER}/{express.id}", self.seller_token).status_code, 204)
        express.refresh_from_db()
        self.assertFalse(express.is_active)   # kept, so the product's history still resolves


class CartDeliveryTests(DeliveryTestCase):
    def test_one_shipping_line_per_seller(self):
        self.add(self.prod_a)
        body = self.add(self.prod_b)
        self.assertEqual(sorted(d["store_name"] for d in body["deliveries"]), ["Huda Beauty", "Other Co"])
        # each seller's own cheapest delivery, summed
        self.assertEqual(body["summary"]["shipping_cents"], 1099 * 2)

    def test_default_is_the_cheapest_delivery_never_pickup(self):
        body = self.add(self.prod_a)
        line = body["deliveries"][0]
        chosen = next(o for o in line["options"] if o["id"] == line["selected_option_id"])
        self.assertEqual(chosen["name"], "Seller Delivery")
        self.assertEqual(chosen["kind"], "delivery")
        self.assertLess(chosen["price_cents"], 1999)

    def test_choosing_per_store_changes_only_that_store(self):
        self.add(self.prod_a)
        self.add(self.prod_b)
        express_a = self.option(self.store_a, "Express Delivery")
        body = self.patch(f"{CART}/delivery", {"store_id": str(self.store_a.id), "option_id": str(express_a.id)}, self.token).json()
        by_store = {d["store_name"]: d for d in body["deliveries"]}
        self.assertEqual(by_store["Huda Beauty"]["price_cents"], 1999)
        self.assertEqual(by_store["Other Co"]["price_cents"], 1099)
        self.assertEqual(body["summary"]["shipping_cents"], 1999 + 1099)

    def test_a_product_narrows_the_options_for_its_store(self):
        pickup_only = self.option(self.store_a, "Customer Pickup")
        self.prod_a.delivery_options.set([pickup_only])
        body = self.add(self.prod_a)
        line = body["deliveries"][0]
        self.assertEqual([o["name"] for o in line["options"]], ["Customer Pickup"])
        self.assertEqual(body["summary"]["shipping_cents"], 0)

    def test_two_products_from_one_store_intersect_their_options(self):
        express = self.option(self.store_a, "Express Delivery")
        seller = self.option(self.store_a, "Seller Delivery")
        other = make_product(self.store_a, self.beauty, "Serum", 999, None, stock=5)
        self.prod_a.delivery_options.set([express, seller])
        other.delivery_options.set([express])
        self.add(self.prod_a)
        body = self.add(other)
        self.assertEqual([o["name"] for o in body["deliveries"][0]["options"]], ["Express Delivery"])

    def test_no_common_option_blocks_checkout_with_a_clear_reason(self):
        other = make_product(self.store_a, self.beauty, "Serum", 999, None, stock=5)
        self.prod_a.delivery_options.set([self.option(self.store_a, "Express Delivery")])
        other.delivery_options.set([self.option(self.store_a, "Customer Pickup")])
        self.add(self.prod_a)
        body = self.add(other)
        self.assertEqual(body["deliveries"][0]["options"], [])
        self.assertFalse(body["can_checkout"])
        self.assertIn("no delivery option covers these items", " ".join(body["problems"]))
        res = self.post(CHECKOUT, {"contact": CONTACT, "address": ADDRESS, "payment_method_type": "card"}, self.token)
        self.assertEqual(res.status_code, 409)

    def test_another_stores_option_is_rejected(self):
        self.add(self.prod_a)
        foreign = self.option(self.store_b, "Express Delivery")
        res = self.patch(f"{CART}/delivery", {"store_id": str(self.store_b.id), "option_id": str(foreign.id)}, self.token)
        self.assertEqual(res.status_code, 404)


class CheckoutDeliveryTests(DeliveryTestCase):
    def test_multi_store_checkout_snapshots_one_delivery_per_store(self):
        self.add(self.prod_a)
        self.add(self.prod_b)
        payload = {
            "contact": CONTACT, "address": ADDRESS, "payment_method_type": "card",
            "deliveries": [{"store_id": str(self.store_a.id), "option_id": str(self.option(self.store_a, "Express Delivery").id)}],
        }
        res = self.post(CHECKOUT, payload, self.token)
        self.assertEqual(res.status_code, 201, res.content)
        order = Order.objects.get(order_number=res.json()["order"]["order_number"])
        rows = {d.store_id: d for d in OrderDelivery.objects.filter(order=order)}
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[self.store_a.id].name, "Express Delivery")
        self.assertEqual(rows[self.store_a.id].price_cents, 1999)
        self.assertEqual(rows[self.store_b.id].name, "Seller Delivery")
        self.assertEqual(order.shipping_cents, 1999 + 1099)
        self.assertEqual(order.total_cents, 2599 + 1299 + 1999 + 1099)
        # the buyer-facing snapshot lists every seller's choice
        detail = self.get(f"/api/v1/me/orders/{order.id}", token=self.token).json()
        self.assertEqual(sorted(d["store"] for d in detail["delivery_method"]["per_store"]), ["Huda Beauty", "Other Co"])

    def test_deactivating_an_option_later_does_not_rewrite_history(self):
        self.add(self.prod_a)
        res = self.post(CHECKOUT, {"contact": CONTACT, "address": ADDRESS, "payment_method_type": "card"}, self.token)
        self.assertEqual(res.status_code, 201, res.content)
        StoreDeliveryOption.objects.filter(store=self.store_a).update(is_active=False)
        order = Order.objects.get(order_number=res.json()["order"]["order_number"])
        self.assertEqual(OrderDelivery.objects.get(order=order, store=self.store_a).name, "Seller Delivery")
