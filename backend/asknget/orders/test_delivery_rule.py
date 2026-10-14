"""The delivery rule: free from a threshold, otherwise a fee, set by the admin and by the seller.

An option with no price follows the rule; one with a price is fixed. The rule is judged per seller, on that seller's
items before any voucher, and an order keeps what it was charged.
"""

from config.models import Setting
from orders.models import Order, OrderDelivery
from sellers.models import StoreDeliveryOption

from .test_delivery import ADDRESS, CART, CHECKOUT, CONTACT, SELLER, DeliveryTestCase

RULE = "/api/v1/seller/delivery-options/rule"


class RuleTestCase(DeliveryTestCase):
    """Store A's "Seller Delivery" follows the rule (no price); "Express Delivery" stays a fixed 1999."""

    def setUp(self):
        super().setUp()
        StoreDeliveryOption.objects.filter(store__in=[self.store_a, self.store_b], name="Seller Delivery").update(price_cents=None)

    def set(self, key, value, scope="global", **target):
        Setting.objects.update_or_create(key=f"shipping.{key}", scope=scope, **target, defaults={"value": value})

    def line(self, body, store):
        return next(d for d in body["deliveries"] if d["store_name"] == store.display_name)


class RuleMathTests(RuleTestCase):
    def test_with_no_rule_set_an_option_that_follows_it_costs_nothing(self):
        body = self.add(self.prod_a)
        self.assertEqual(self.line(body, self.store_a)["price_cents"], 0)       # nothing changes until someone sets a fee

    def test_a_fee_below_the_threshold(self):
        self.set("fee_cents", 800)
        self.set("free_over_cents", 5000)
        body = self.add(self.prod_a)                                            # 2599 < 5000
        line = self.line(body, self.store_a)
        self.assertEqual(line["price_cents"], 800)
        self.assertEqual(line["rule"], {"free_over_cents": 5000, "fee_cents": 800, "store_subtotal_cents": 2599,
                                        "remaining_cents": 2401, "free": False})
        self.assertEqual(body["summary"]["shipping_cents"], 800)

    def test_free_exactly_at_the_threshold_and_not_one_cent_below(self):
        self.set("fee_cents", 800)
        self.set("free_over_cents", 2599)
        line = self.line(self.add(self.prod_a), self.store_a)
        self.assertEqual((line["price_cents"], line["rule"]["free"], line["rule"]["remaining_cents"]), (0, True, 0))
        self.set("free_over_cents", 2600)
        line = self.line(self.get(CART, self.token).json(), self.store_a)
        self.assertEqual((line["price_cents"], line["rule"]["free"], line["rule"]["remaining_cents"]), (800, False, 1))

    def test_zero_threshold_means_never_free(self):
        self.set("fee_cents", 800)
        self.set("free_over_cents", 0)
        body = self.add(self.prod_a, qty=5)
        self.assertEqual(self.line(body, self.store_a)["price_cents"], 800)

    def test_each_seller_is_judged_on_their_own_items(self):
        self.set("fee_cents", 800)
        self.set("free_over_cents", 2000)
        self.add(self.prod_a)                                                   # 2599: free
        body = self.add(self.prod_b)                                            # 1299: pays
        self.assertEqual(self.line(body, self.store_a)["price_cents"], 0)
        self.assertEqual(self.line(body, self.store_b)["price_cents"], 800)
        self.assertEqual(body["summary"]["shipping_cents"], 800)

    def test_quantity_counts_toward_the_threshold(self):
        self.set("fee_cents", 800)
        self.set("free_over_cents", 2000)
        self.assertEqual(self.line(self.add(self.prod_b, qty=2), self.store_b)["price_cents"], 0)    # 2 x 1299

    def test_a_fixed_price_option_ignores_the_rule(self):
        self.set("fee_cents", 800)
        self.set("free_over_cents", 100)                                        # everything qualifies
        self.add(self.prod_a)
        express = self.option(self.store_a, "Express Delivery")
        body = self.patch(f"{CART}/delivery", {"store_id": str(self.store_a.id), "option_id": str(express.id)}, self.token).json()
        line = self.line(body, self.store_a)
        self.assertEqual(line["price_cents"], 1999)
        chosen = next(o for o in line["options"] if o["name"] == "Express Delivery")
        self.assertEqual((chosen["follows_rule"], chosen["waived"]), (False, False))

    def test_the_cheapest_delivery_is_chosen_with_the_rule_applied(self):
        self.set("fee_cents", 800)
        self.set("free_over_cents", 100)
        line = self.line(self.add(self.prod_a), self.store_a)
        chosen = next(o for o in line["options"] if o["id"] == line["selected_option_id"])
        self.assertEqual((chosen["name"], chosen["price_cents"], chosen["waived"]), ("Seller Delivery", 0, True))


class RuleScopeTests(RuleTestCase):
    def test_store_beats_country_beats_global(self):
        self.set("fee_cents", 1000)
        self.set("fee_cents", 900, scope="country", country=self.store_a.country)
        self.set("fee_cents", 700, scope="store", store=self.store_a)
        self.add(self.prod_a)
        body = self.add(self.prod_b)
        self.assertEqual(self.line(body, self.store_a)["price_cents"], 700)
        self.assertEqual(self.line(body, self.store_b)["price_cents"], 900)

    def test_a_locked_market_ignores_the_stores_own_values(self):
        self.set("fee_cents", 500, scope="country", country=self.store_a.country)
        self.set("rule_locked", True, scope="country", country=self.store_a.country)
        self.set("fee_cents", 100, scope="store", store=self.store_a)
        self.assertEqual(self.line(self.add(self.prod_a), self.store_a)["price_cents"], 500)

    def test_unlocking_gives_the_store_its_values_back(self):
        self.set("fee_cents", 500)
        self.set("fee_cents", 100, scope="store", store=self.store_a)
        self.set("rule_locked", True)
        self.assertEqual(self.line(self.add(self.prod_a), self.store_a)["price_cents"], 500)
        self.set("rule_locked", False)
        self.assertEqual(self.line(self.get(CART, self.token).json(), self.store_a)["price_cents"], 100)


class RuleCheckoutTests(RuleTestCase):
    def place(self):
        res = self.post(CHECKOUT, {"contact": CONTACT, "address": ADDRESS, "payment_method_type": "card"}, self.token)
        self.assertEqual(res.status_code, 201, res.content)
        return Order.objects.get(order_number=res.json()["order"]["order_number"])

    def test_checkout_charges_and_records_the_rule(self):
        self.set("fee_cents", 800)
        self.set("free_over_cents", 2000)
        self.add(self.prod_a)
        self.add(self.prod_b)
        order = self.place()
        rows = {d.store_id: d for d in OrderDelivery.objects.filter(order=order)}
        self.assertEqual((rows[self.store_a.id].price_cents, rows[self.store_a.id].waived, rows[self.store_a.id].free_over_cents), (0, True, 2000))
        self.assertEqual((rows[self.store_b.id].price_cents, rows[self.store_b.id].waived), (800, False))
        self.assertEqual(order.shipping_cents, 800)
        self.assertEqual(order.total_cents, 2599 + 1299 + 800)
        per_store = {d["store"]: d for d in order.delivery_method_snapshot["per_store"]}
        self.assertTrue(per_store["Huda Beauty"]["waived"])
        self.assertFalse(per_store["Other Co"]["waived"])

    def test_a_fixed_price_option_records_no_threshold(self):
        self.set("free_over_cents", 100)
        self.add(self.prod_a)
        express = self.option(self.store_a, "Express Delivery")
        self.patch(f"{CART}/delivery", {"store_id": str(self.store_a.id), "option_id": str(express.id)}, self.token)
        row = OrderDelivery.objects.get(order=self.place())
        self.assertEqual((row.price_cents, row.waived, row.free_over_cents), (1999, False, None))

    def test_changing_the_rule_later_does_not_touch_an_old_order(self):
        self.set("fee_cents", 800)
        self.add(self.prod_a)
        order = self.place()
        self.set("fee_cents", 5000)
        self.set("free_over_cents", 100)
        order.refresh_from_db()
        self.assertEqual(order.shipping_cents, 800)
        detail = self.get(f"/api/v1/me/orders/{order.id}", token=self.token).json()
        self.assertEqual(detail["shipping_cents"], 800)


class SellerRuleTests(RuleTestCase):
    def test_the_seller_sees_what_applies_and_where_it_comes_from(self):
        self.set("fee_cents", 900, scope="country", country=self.store_a.country)
        body = self.get(RULE, self.seller_token).json()
        self.assertEqual((body["fee_cents"], body["free_over_cents"], body["locked"]), (900, 0, False))
        self.assertEqual(body["source"], {"free_over": "default", "fee": "country"})
        self.assertEqual(body["own"], {"free_over_cents": None, "fee_cents": None})
        self.assertEqual(body["currency"], self.store_a.country.currency)

    def test_the_seller_sets_their_own_values_and_the_cart_follows(self):
        res = self.client.put(RULE, data={"free_over_cents": 2000, "fee_cents": 650}, content_type="application/json",
                              headers={"Authorization": f"Bearer {self.seller_token}"})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()["own"], {"free_over_cents": 2000, "fee_cents": 650})
        self.assertEqual(res.json()["source"], {"free_over": "store", "fee": "store"})
        self.assertEqual(self.line(self.add(self.prod_b), self.store_b)["price_cents"], 0)       # store B has no rule of its own: free by default
        self.assertEqual(self.line(self.get(CART, self.token).json(), self.store_b)["rule"]["free_over_cents"], 0)
        body = self.add(self.prod_a)
        self.assertEqual(self.line(body, self.store_a)["price_cents"], 0)                       # 2599 >= 2000
        self.assertEqual(self.line(body, self.store_a)["rule"]["fee_cents"], 650)

    def test_one_value_at_a_time_and_nothing_at_all_is_refused(self):
        headers = {"Authorization": f"Bearer {self.seller_token}"}
        ok = self.client.put(RULE, data={"fee_cents": 400}, content_type="application/json", headers=headers)
        self.assertEqual(ok.json()["own"], {"free_over_cents": None, "fee_cents": 400})
        self.assertEqual(self.client.put(RULE, data={}, content_type="application/json", headers=headers).status_code, 422)
        self.assertEqual(self.client.put(RULE, data={"fee_cents": -1}, content_type="application/json", headers=headers).status_code, 422)

    def test_the_seller_can_go_back_to_the_platforms_numbers(self):
        self.set("fee_cents", 900)
        headers = {"Authorization": f"Bearer {self.seller_token}"}
        self.client.put(RULE, data={"fee_cents": 100}, content_type="application/json", headers=headers)
        res = self.client.delete(RULE, headers=headers)
        self.assertEqual(res.status_code, 200)
        self.assertEqual((res.json()["fee_cents"], res.json()["own"]["fee_cents"]), (900, None))

    def test_a_locked_market_refuses_the_seller_with_rule_locked(self):
        self.set("rule_locked", True)
        res = self.client.put(RULE, data={"fee_cents": 100}, content_type="application/json",
                              headers={"Authorization": f"Bearer {self.seller_token}"})
        self.assertEqual((res.status_code, res.json()["detail"]), (409, "rule_locked"))
        self.assertTrue(self.get(RULE, self.seller_token).json()["locked"])

    def test_only_the_stores_own_rule_is_touched(self):
        self.client.put(RULE, data={"fee_cents": 100}, content_type="application/json", headers={"Authorization": f"Bearer {self.seller_token}"})
        self.assertFalse(Setting.objects.filter(key="shipping.fee_cents", store=self.store_b).exists())

    def test_a_buyer_cannot_use_the_seller_rule(self):
        self.assertEqual(self.get(RULE, self.token).status_code, 403)


class OptionPriceTests(RuleTestCase):
    def test_a_delivery_option_can_follow_the_rule_and_says_so(self):
        res = self.post(SELLER, {"name": "Standard", "price_cents": None}, self.seller_token)
        self.assertEqual(res.status_code, 201, res.content)
        self.assertIsNone(res.json()["price_cents"])
        rows = {r["name"]: r for r in self.get(SELLER, self.seller_token).json()}
        self.assertIsNone(rows["Standard"]["price_cents"])
        self.assertEqual(rows["Express Delivery"]["price_cents"], 1999)

    def test_a_fixed_option_can_be_switched_to_follow_the_rule_and_back(self):
        express = self.option(self.store_a, "Express Delivery")
        self.assertIsNone(self.patch(f"{SELLER}/{express.id}", {"price_cents": None}, self.seller_token).json()["price_cents"])
        self.assertEqual(self.patch(f"{SELLER}/{express.id}", {"price_cents": 2500}, self.seller_token).json()["price_cents"], 2500)

    def test_pickup_cannot_follow_the_rule(self):
        pickup = self.option(self.store_a, "Customer Pickup")
        self.assertEqual(self.patch(f"{SELLER}/{pickup.id}", {"price_cents": None}, self.seller_token).status_code, 422)
        self.assertEqual(self.post(SELLER, {"name": "Collect", "kind": "pickup", "price_cents": None}, self.seller_token).status_code, 422)
        self.assertEqual(self.option(self.store_a, "Customer Pickup").price_cents, 0)
