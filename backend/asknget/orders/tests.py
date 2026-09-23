"""End-to-end tests for cart, checkout, orders and tracking."""

from django.conf import settings
from django.core import mail
from django.utils import timezone

from catalog.models import ProductVariant
from core.testing import ApiTestCase, make_category, make_product, make_store, make_user

from .models import Order, OrderDelivery, Voucher

CART = "/api/v1/cart"
CHECKOUT = "/api/v1/checkout"
ORDERS = "/api/v1/me/orders"


class OrdersTestCase(ApiTestCase):
    def setUp(self):
        self.store = make_store()
        self.beauty = make_category("Beauty")
        self.bb = make_product(self.store, self.beauty, "NARS BB Cream", 2599, 4099, options={"Size": ["30 ml"], "Shade": ["Natural"]}, stock=5)
        self.buds = make_product(self.store, self.beauty, "Black Earbuds", 1299, 2099, options={"Color": ["Black"]}, stock=2)
        self.bb_variant = self.bb.variants.first()
        self.buds_variant = self.buds.variants.first()
        # Shipping is the seller's own (sprint 6): make_store() gives every store the
        # design's three options. Cheapest delivery wins by default; pickup never does.
        self.express = self.store.delivery_options.get(name="Express Delivery")      # 1999
        self.standard = self.store.delivery_options.get(name="Seller Delivery")      # 1099, the default
        self.pickup = self.store.delivery_options.get(name="Customer Pickup")        # 0
        self.user = make_user(email="john.ali@gmail.com", full_name="John Ali")
        self.token = self.token_for(self.user)

    CONTACT = {"first_name": "John", "last_name": "Ali", "email": "john.ali@gmail.com", "phone": "+92 300 1234567"}
    ADDRESS = {"address": "123 Main Street", "city": "Lahore", "postal_code": "45009"}

    def checkout_payload(self, **over):
        return {"contact": self.CONTACT, "address": self.ADDRESS, "payment_method_type": "card", **over}

    def delivery(self, option):
        return [{"store_id": str(self.store.id), "option_id": str(option.id)}]

    def cart_with(self, token=None, guest_token=None, variant=None, qty=1):
        headers = {"X-Cart-Token": guest_token} if guest_token else {}
        res = self.client.post(f"{CART}/items", {"variant_id": str((variant or self.bb_variant).id), "qty": qty}, content_type="application/json",
                               headers={**self._headers(token), **headers})
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()


class CartTests(OrdersTestCase):
    def test_member_cart_totals(self):
        body = self.cart_with(self.token, qty=2)
        self.assertIsNone(body["cart_token"])
        line = body["items"][0]
        self.assertEqual((line["product_name"], line["variant_summary"], line["qty"]), ("NARS BB Cream", "Size: 30 ml | Shade: Natural", 2))
        self.assertEqual(line["line_total_cents"], 5198)
        self.assertEqual([d["store_name"] for d in body["deliveries"]], ["Huda Beauty"])  # one line per seller
        chosen = next(o for o in body["deliveries"][0]["options"] if o["id"] == body["deliveries"][0]["selected_option_id"])
        self.assertEqual((chosen["name"], chosen["price_cents"]), ("Seller Delivery", 1099))  # cheapest delivery, not pickup
        self.assertEqual(body["summary"], {"subtotal_cents": 5198, "shipping_cents": 1099, "discount_cents": 0, "total_cents": 6297, "currency": "USD", "item_count": 2})
        self.assertTrue(body["can_checkout"])
        # adding again merges qty
        body = self.cart_with(self.token, qty=1)
        self.assertEqual(body["items"][0]["qty"], 3)
        self.assertEqual(self.get(CART, self.token).json()["items"][0]["qty"], 3)

    def test_guest_cart_token_round_trip(self):
        body = self.client.get(CART).json()
        token = body["cart_token"]
        self.assertTrue(token)
        self.assertEqual(body["items"], [])
        self.assertFalse(body["can_checkout"])
        body = self.cart_with(guest_token=token)
        self.assertEqual(body["cart_token"], token)
        self.assertEqual(len(self.client.get(CART, headers={"X-Cart-Token": token}).json()["items"]), 1)
        # unknown token → fresh cart, new token
        fresh = self.client.get(CART, headers={"X-Cart-Token": "nope"}).json()
        self.assertNotEqual(fresh["cart_token"], token)

    def test_guest_cart_merges_into_member_cart_on_sign_in(self):
        token = self.client.get(CART).json()["cart_token"]
        self.cart_with(guest_token=token, variant=self.buds_variant)
        self.cart_with(self.token, variant=self.bb_variant)
        body = self.client.get(CART, headers={"Authorization": f"Bearer {self.token}", "X-Cart-Token": token}).json()
        self.assertEqual({l["product_name"] for l in body["items"]}, {"NARS BB Cream", "Black Earbuds"})
        # guest cart is gone
        self.assertEqual(self.client.get(CART, headers={"X-Cart-Token": token}).json()["items"], [])

    def test_stock_limits(self):
        res = self.post(f"{CART}/items", {"variant_id": str(self.buds_variant.id), "qty": 5}, self.token)
        self.assertEqual(res.json()["items"][0]["qty"], 2)  # clamped to stock
        item_id = res.json()["items"][0]["id"]
        self.assertEqual(self.patch(f"{CART}/items/{item_id}", {"qty": 3}, self.token).status_code, 409)
        self.assertEqual(self.patch(f"{CART}/items/{item_id}", {"qty": 1}, self.token).json()["items"][0]["qty"], 1)
        self.assertEqual(self.delete(f"{CART}/items/{item_id}", self.token).json()["items"], [])
        ProductVariant.objects.filter(pk=self.buds_variant.pk).update(stock_qty=0)
        self.assertEqual(self.post(f"{CART}/items", {"variant_id": str(self.buds_variant.id)}, self.token).status_code, 409)
        self.assertEqual(self.post(f"{CART}/items", {"variant_id": "00000000-0000-0000-0000-000000000000"}, self.token).status_code, 404)

    def test_note_and_delivery_type(self):
        self.cart_with(self.token)
        body = self.patch(CART, {"order_note": "Leave at door"}, self.token).json()
        self.assertEqual(body["order_note"], "Leave at door")
        body = self.patch(f"{CART}/delivery", {"store_id": str(self.store.id), "option_id": str(self.express.id)}, self.token).json()
        self.assertEqual(body["summary"]["shipping_cents"], 1999)
        self.assertEqual(body["deliveries"][0]["selected_option_id"], str(self.express.id))
        # pickup is choosable, just never the default
        self.assertEqual(self.patch(f"{CART}/delivery", {"store_id": str(self.store.id), "option_id": str(self.pickup.id)},
                                    self.token).json()["summary"]["shipping_cents"], 0)
        bogus = self.patch(f"{CART}/delivery", {"store_id": str(self.store.id), "option_id": "00000000-0000-0000-0000-000000000000"}, self.token)
        self.assertEqual(bogus.status_code, 422)

    def test_vouchers(self):
        Voucher.objects.create(code="SAVE20", type="percent", value=20, min_subtotal_cents=5000)
        Voucher.objects.create(code="OLD", type="fixed", value=500, ends_at=timezone.now() - timezone.timedelta(days=1))
        self.cart_with(self.token, qty=1)
        self.assertEqual(self.post(f"{CART}/voucher", {"code": "nope"}, self.token).status_code, 404)
        res = self.post(f"{CART}/voucher", {"code": "save20"}, self.token)
        self.assertEqual(res.status_code, 422)
        self.assertIn("more to use", res.json()["detail"])
        self.assertIn("expired", self.post(f"{CART}/voucher", {"code": "OLD"}, self.token).json()["detail"])
        self.cart_with(self.token, qty=1)  # subtotal 5198
        body = self.post(f"{CART}/voucher", {"code": "SAVE20"}, self.token).json()
        self.assertEqual(body["voucher"]["label"], "Discount (20%)")
        self.assertEqual(body["summary"]["discount_cents"], 1039)
        self.assertEqual(body["summary"]["total_cents"], 5198 + 1099 - 1039)
        self.assertIsNone(self.delete(f"{CART}/voucher", self.token).json()["voucher"])


class CheckoutTests(OrdersTestCase):
    def test_empty_cart_refused(self):
        self.get(CART, self.token)
        res = self.post(CHECKOUT, self.checkout_payload(), self.token)
        self.assertEqual(res.status_code, 409)

    def test_payload_validation(self):
        self.cart_with(self.token)
        res = self.post(CHECKOUT, {"contact": self.CONTACT, "payment_method_type": "card"}, self.token)
        self.assertEqual(res.status_code, 422)
        res = self.post(CHECKOUT, self.checkout_payload(ship_to_different_address=True), self.token)
        self.assertEqual(res.status_code, 422)
        res = self.post(CHECKOUT, self.checkout_payload(payment_method_type="cash"), self.token)
        self.assertEqual(res.status_code, 422)

    def test_member_checkout_snapshots_and_reserves_stock(self):
        Voucher.objects.create(code="SAVE20", type="percent", value=20)
        self.cart_with(self.token, qty=2)
        self.cart_with(self.token, variant=self.buds_variant)
        self.post(f"{CART}/voucher", {"code": "SAVE20"}, self.token)
        self.patch(CART, {"order_note": "Ring twice"}, self.token)
        res = self.post(CHECKOUT, self.checkout_payload(payment_method_type="bank_transfer"), self.token)
        self.assertEqual(res.status_code, 201, res.content)
        body = res.json()
        self.assertIn("Thank You, John Ali", body["detail"])
        order = body["order"]
        self.assertRegex(order["order_number"], r"^ORD-\d{6}$")
        self.assertEqual((order["status"], order["payment_status"], order["payment_method_type"]), ("pending_payment", "unpaid", "bank_transfer"))
        subtotal = 2 * 2599 + 1299
        self.assertEqual((order["subtotal_cents"], order["shipping_cents"], order["discount_cents"]), (subtotal, 1099, subtotal * 20 // 100))
        self.assertEqual(order["total_cents"], subtotal + 1099 - subtotal * 20 // 100)
        self.assertEqual(order["voucher_code"], "SAVE20")
        self.assertEqual(order["special_note"], "Ring twice")
        self.assertEqual(order["shipping_address"]["city"], "Lahore")
        self.assertIsNone(order["billing_address"])
        self.assertEqual([d["name"] for d in order["delivery_method"]["per_store"]], ["Seller Delivery"])
        self.assertEqual({i["product_name"]: i["qty"] for i in order["items"]}, {"NARS BB Cream": 2, "Black Earbuds": 1})
        self.assertEqual(order["items"][0]["variant_summary"], "Size: 30 ml | Shade: Natural")
        self.assertEqual([e["status"] for e in order["events"]], ["pending_payment"])
        self.assertTrue(order["can_cancel"])
        # stock reserved, cart cleared, voucher counted, mail sent
        self.bb_variant.refresh_from_db(); self.buds_variant.refresh_from_db()
        self.assertEqual((self.bb_variant.stock_qty, self.buds_variant.stock_qty), (3, 1))
        cart = self.get(CART, self.token).json()
        self.assertEqual((cart["items"], cart["voucher"], cart["order_note"]), ([], None, ""))
        self.assertEqual(Voucher.objects.get(code="SAVE20").used_count, 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(order["order_number"], mail.outbox[0].subject)

    def test_saved_address_and_different_shipping(self):
        addr = self.post("/api/v1/me/addresses", {"full_name": "John Ali", "phone": "+923001234567", "street_address": "12 Mall Rd",
                                                  "city": "Lahore", "postal_code": "54000", "country": "PK"}, self.token)
        self.assertEqual(addr.status_code, 201, addr.content)
        self.cart_with(self.token)
        res = self.post(CHECKOUT, self.checkout_payload(address=None, address_id=addr.json()["id"], ship_to_different_address=True,
                                                        shipping_address={"address": "1 Other St", "city": "Karachi", "postal_code": "75500"}), self.token)
        self.assertEqual(res.status_code, 201, res.content)
        order = res.json()["order"]
        self.assertEqual(order["shipping_address"]["city"], "Karachi")
        self.assertEqual(order["billing_address"]["address"], "12 Mall Rd")

    def test_guest_checkout(self):
        token = self.client.get(CART).json()["cart_token"]
        self.cart_with(guest_token=token)
        res = self.client.post(CHECKOUT, self.checkout_payload(), content_type="application/json", headers={"X-Cart-Token": token})
        self.assertEqual(res.status_code, 201, res.content)
        number = res.json()["order"]["order_number"]
        self.assertIsNone(Order.objects.get(order_number=number).user)
        # guest cannot use a saved address
        self.cart_with(guest_token=token)
        res = self.client.post(CHECKOUT, self.checkout_payload(address=None, address_id="00000000-0000-0000-0000-000000000000"),
                               content_type="application/json", headers={"X-Cart-Token": token})
        self.assertEqual(res.status_code, 422)
        # no cart at all
        self.assertEqual(self.client.post(CHECKOUT, self.checkout_payload(), content_type="application/json").status_code, 404)

    def test_stock_race_refused(self):
        self.cart_with(self.token, variant=self.buds_variant, qty=2)
        ProductVariant.objects.filter(pk=self.buds_variant.pk).update(stock_qty=1)
        res = self.post(CHECKOUT, self.checkout_payload(), self.token)
        self.assertEqual(res.status_code, 409)
        self.assertIn("only 1 left", res.json()["detail"])


class MyOrdersTests(OrdersTestCase):
    def place(self, variant=None, qty=1, token=None):
        token = token or self.token
        self.cart_with(token, variant=variant, qty=qty)
        res = self.post(CHECKOUT, self.checkout_payload(), token)
        self.assertEqual(res.status_code, 201, res.content)
        return res.json()["order"]

    def test_tabs_and_detail(self):
        o1 = self.place()
        o2 = self.place(variant=self.buds_variant)
        self.post(f"{ORDERS}/{o2['id']}/cancel", {"reason": "Changed my mind"}, self.token)
        Order.objects.filter(pk=o1["id"]).update(status="delivered")
        self.assertEqual(self.get(ORDERS, self.token).json()["total"], 2)
        self.assertEqual([o["order_number"] for o in self.get(ORDERS, self.token, tab="completed").json()["items"]], [o1["order_number"]])
        self.assertEqual([o["order_number"] for o in self.get(ORDERS, self.token, tab="cancelled").json()["items"]], [o2["order_number"]])
        self.assertEqual(self.get(ORDERS, self.token, tab="ongoing").json()["items"], [])
        completed = self.get(ORDERS, self.token, tab="completed").json()["items"][0]
        self.assertEqual((completed["group"], completed["can_cancel"], completed["can_reorder"]), ("completed", False, True))
        self.assertTrue(completed["items"][0]["can_review"])  # 'Leave Review'
        detail = self.get(f"{ORDERS}/{o2['id']}", self.token).json()
        self.assertEqual(detail["cancel_reason"], "Changed my mind")
        self.assertEqual([e["status"] for e in detail["events"]], ["pending_payment", "cancelled"])
        # other users can't see it
        other = self.token_for(make_user())
        self.assertEqual(self.get(f"{ORDERS}/{o2['id']}", other).status_code, 404)

    def test_cancel_restores_stock_and_respects_window(self):
        order = self.place(qty=2)
        self.bb_variant.refresh_from_db(); self.assertEqual(self.bb_variant.stock_qty, 3)
        res = self.post(f"{ORDERS}/{order['id']}/cancel", {}, self.token)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()["status"], "cancelled")
        self.bb_variant.refresh_from_db(); self.assertEqual(self.bb_variant.stock_qty, 5)
        self.assertEqual(self.post(f"{ORDERS}/{order['id']}/cancel", {}, self.token).status_code, 409)
        shipped = self.place()
        Order.objects.filter(pk=shipped["id"]).update(status="shipped")
        self.assertEqual(self.post(f"{ORDERS}/{shipped['id']}/cancel", {}, self.token).status_code, 409)

    def test_reorder(self):
        order = self.place(variant=self.buds_variant, qty=2)
        ProductVariant.objects.filter(pk=self.buds_variant.pk).update(stock_qty=0)  # sold out since
        Order.objects.filter(pk=order["id"]).update(status="delivered")
        body = self.post(f"{ORDERS}/{order['id']}/reorder", None, self.token).json()
        self.assertEqual(body["items"], [])
        self.assertIn("Black Earbuds is no longer available", body["problems"])
        ProductVariant.objects.filter(pk=self.buds_variant.pk).update(stock_qty=1)
        body = self.post(f"{ORDERS}/{order['id']}/reorder", None, self.token).json()
        self.assertEqual((body["items"][0]["product_name"], body["items"][0]["qty"]), ("Black Earbuds", 1))  # clamped

    def test_public_tracking(self):
        order = self.place()
        res = self.client.post("/api/v1/orders/track", {"order_number": order["order_number"].lower(), "email": "JOHN.ALI@gmail.com"}, content_type="application/json")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()["status_label"], "Pending Payment")
        self.assertEqual(len(res.json()["events"]), 1)
        res = self.client.post("/api/v1/orders/track", {"order_number": order["order_number"], "email": "someone@else.com"}, content_type="application/json")
        self.assertEqual(res.status_code, 404)


class AdminOrderTests(OrdersTestCase):
    def test_status_progression_writes_timeline(self):
        staff = make_user(is_staff=True)
        staff_token = self.token_for(staff)
        self.cart_with(self.token)
        order = self.post(CHECKOUT, self.checkout_payload(payment_method_type="bank_transfer"), self.token).json()["order"]
        self.assertEqual(self.post(f"/api/v1/admin/orders/{order['id']}/status", {"status": "shipped"}, self.token).status_code, 403)
        self.assertEqual(self.post(f"/api/v1/admin/orders/{order['id']}/status", {"status": "shipped"}, staff_token).status_code, 409)
        for status in ("confirmed", "processing", "shipped", "out_for_delivery", "delivered"):
            res = self.post(f"/api/v1/admin/orders/{order['id']}/status", {"status": status, "location": "Lahore hub"}, staff_token)
            self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertEqual(body["payment_status"], "paid")  # confirmed ⇒ manually paid until the PSP lands
        self.assertEqual([e["status"] for e in body["events"]], ["pending_payment", "confirmed", "processing", "shipped", "out_for_delivery", "delivered"])
        self.assertEqual(body["events"][3]["location"], "Lahore hub")
        self.assertEqual(self.get("/api/v1/admin/orders", staff_token, status="delivered").json()["total"], 1)
        self.assertEqual(self.get(f"{ORDERS}/{order['id']}", self.token).json()["group"], "completed")
