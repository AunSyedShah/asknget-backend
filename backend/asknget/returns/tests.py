"""Returns: the rules, the request workflow, the money, and the courier seam.

These drive the real flow - a buyer checks out, staff confirm payment, a seller delivers - and then a customer
asks for a return, so a wrong hook shows up as a wrong number or a wrong status.
"""

from datetime import timedelta
from io import BytesIO

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.utils import timezone
from PIL import Image

from catalog.models import StockMovement
from config.models import Setting
from core.testing import ApiTestCase, make_category, make_country, make_product, make_store, make_user
from ledger import services as ledger
from ledger.models import LedgerEntry
from markets.models import Country, ReturnPolicy
from notifications.models import NotificationTemplate, StoreNotification
from orders.models import Order, OrderDelivery, OrderItem, OrderStatusEvent, Voucher
from team.models import StoreMember

from . import couriers, policy, services
from .models import ReturnReason, ReturnRequest, ReturnRule, ReturnShipment

CART = "/api/v1/cart"
CHECKOUT = "/api/v1/checkout"
ME = "/api/v1/me"
SELLER = "/api/v1/seller/returns"
ADMIN = "/api/v1/admin/returns"
CONTACT = {"first_name": "Olivia", "last_name": "Martin", "email": "olivia@example.com", "phone": "+27 82 123 4567"}
ADDRESS = {"address": "123 Main Street", "city": "Cape Town", "postal_code": "6001"}
T = LedgerEntry.Type


def png(name="p.png"):
    buf = BytesIO()
    Image.new("RGB", (4, 4), "red").save(buf, format="PNG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


class ReturnsTestCase(ApiTestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner_a, cls.owner_b = make_user(is_seller=True), make_user(is_seller=True)
        cls.store_a = make_store(owner=cls.owner_a, name="Huda Beauty")
        cls.store_b = make_store(owner=cls.owner_b, name="Tech Hub")
        cls.beauty, cls.tech = make_category("Beauty"), make_category("Tech")
        cls.bb = make_product(cls.store_a, cls.beauty, "BB Cream", 10000, None, stock=50)
        cls.lip = make_product(cls.store_a, cls.beauty, "Lip Gloss", 5000, None, stock=50)
        cls.cable = make_product(cls.store_a, cls.tech, "Cable", 2000, None, stock=50)
        cls.watch = make_product(cls.store_b, cls.tech, "Smart Watch", 20000, None, stock=50)
        cls.buyer = make_user(email="olivia@example.com", full_name="Olivia Martin")
        cls.other_buyer = make_user(email="someone@example.com")
        cls.staff = make_user(is_staff=True)
        # The seeded reasons come from the migration; fetch the ones the tests use.
        cls.r_mind = ReturnReason.objects.get(code="changed_mind")
        cls.r_damaged = ReturnReason.objects.get(code="damaged")
        cls.r_wrong = ReturnReason.objects.get(code="wrong_item")
        cls.r_notdesc = ReturnReason.objects.get(code="not_as_described")

    def setUp(self):
        self.buyer_token = self.token_for(self.buyer)
        self.other_token = self.token_for(self.other_buyer)
        self.staff_token = self.token_for(self.staff)
        self.token_a, self.token_b = self.token_for(self.owner_a), self.token_for(self.owner_b)

    # -- the real flow ---------------------------------------------------------------------
    def order(self, *lines, voucher=None) -> Order:
        for product, qty in lines:
            res = self.post(f"{CART}/items", {"variant_id": str(product.variants.first().id), "qty": qty}, self.buyer_token)
            self.assertEqual(res.status_code, 200, res.content)
        if voucher:
            self.assertEqual(self.post(f"{CART}/voucher", {"code": voucher}, self.buyer_token).status_code, 200)
        res = self.post(CHECKOUT, {"contact": CONTACT, "address": ADDRESS, "payment_method_type": "card"}, self.buyer_token)
        self.assertEqual(res.status_code, 201, res.content)
        return Order.objects.get(order_number=res.json()["order"]["order_number"])

    def delivered(self, *lines, voucher=None) -> Order:
        order = self.order(*lines, voucher=voucher)
        self.assertEqual(self.post(f"/api/v1/admin/orders/{order.pk}/status", {"status": "confirmed"}, self.staff_token).status_code, 200)
        for store_token in {self.token_a if p.store_id == self.store_a.pk else self.token_b for p, _ in lines}:
            for status in ("processing", "shipped", "delivered"):
                res = self.post(f"/api/v1/seller/orders/{order.pk}/status", {"status": status}, store_token)
                self.assertEqual(res.status_code, 200, res.content)
        return Order.objects.get(pk=order.pk)

    def item(self, order, product) -> OrderItem:
        return order.items.get(product=product)

    def ask(self, order, items, reason="changed_mind", note="", photos=None, token=None, region=None):
        data = {"reason": reason, "note": note, "item_ids": [str(i.pk) for i in items]}
        if photos:
            data["photos"] = photos
        headers = {"Authorization": f"Bearer {token or self.buyer_token}"}
        if region:
            headers["X-Region"] = region
        return self.client.post(f"{ME}/orders/{order.pk}/returns", data, headers=headers)

    def request(self, order, items, reason="changed_mind", **kw) -> ReturnRequest:
        res = self.ask(order, items, reason, **kw)
        self.assertEqual(res.status_code, 201, res.content)
        return ReturnRequest.objects.get(pk=res.json()["id"])

    def seller_post(self, req, action, data=None, token=None):
        return self.post(f"{SELLER}/{req.pk}/{action}", data or {}, token or self.token_a)

    def staff_post(self, req, action, data=None):
        return self.post(f"{ADMIN}/{req.pk}/{action}", data or {}, self.staff_token)

    def returned_to_seller(self, req, condition="good"):
        self.assertEqual(self.seller_post(req, "approve").status_code, 200)
        res = self.seller_post(req, "received", {"condition": condition})
        self.assertEqual(res.status_code, 200, res.content)
        return ReturnRequest.objects.get(pk=req.pk)

    def backdate_delivery(self, order, days):
        OrderStatusEvent.objects.filter(order=order, status="delivered").update(created_at=timezone.now() - timedelta(days=days))

    def amount(self, store, kind, **filters):
        return sum(LedgerEntry.objects.filter(store=store, type=kind, **filters).values_list("amount_cents", flat=True))


# ======================================================================================
# The rules
# ======================================================================================


class TermsTests(ReturnsTestCase):
    def test_the_market_policy_is_the_default(self):
        terms = policy.terms_for(Country.objects.get(pk="ZA"), [self.beauty.pk])[self.beauty.pk]
        self.assertEqual((terms.change_of_mind_days, terms.defect_days, terms.restocking_fee_bps), (30, 30, 0))

    def test_an_inactive_or_missing_policy_means_no_returns(self):
        ReturnPolicy.objects.filter(country_id="ZA").update(is_active=False)
        self.assertEqual(policy.terms_for(Country.objects.get(pk="ZA"), [self.beauty.pk])[self.beauty.pk], policy.NO_RETURNS)
        ReturnPolicy.objects.filter(country_id="ZA").delete()
        self.assertEqual(policy.terms_for(Country.objects.get(pk="ZA"), [self.beauty.pk])[self.beauty.pk], policy.NO_RETURNS)

    def test_a_category_rule_overrides_only_what_it_sets(self):
        ReturnRule.objects.create(category=self.beauty, change_of_mind_days=0)
        terms = policy.terms_for(Country.objects.get(pk="ZA"), [self.beauty.pk, self.tech.pk])
        self.assertEqual((terms[self.beauty.pk].change_of_mind_days, terms[self.beauty.pk].defect_days), (0, 30))   # defects stay returnable
        self.assertEqual(terms[self.tech.pk].change_of_mind_days, 30)

    def test_a_subcategory_follows_its_parents_rule_and_its_own_wins(self):
        makeup = make_category("Makeup", parent=self.beauty)
        lips = make_category("Lips", parent=makeup)
        ReturnRule.objects.create(category=self.beauty, change_of_mind_days=0)
        za = Country.objects.get(pk="ZA")
        self.assertEqual(policy.terms_for(za, [lips.pk])[lips.pk].change_of_mind_days, 0)
        ReturnRule.objects.create(category=makeup, change_of_mind_days=14)
        self.assertEqual(policy.terms_for(za, [lips.pk])[lips.pk].change_of_mind_days, 14)

    def test_a_market_rule_beats_an_all_markets_rule_and_ignores_other_markets(self):
        za, pk = Country.objects.get(pk="ZA"), make_country("PK")
        ReturnRule.objects.create(category=self.beauty, change_of_mind_days=10)
        ReturnRule.objects.create(category=self.beauty, country=za, change_of_mind_days=3)
        ReturnRule.objects.create(category=self.tech, country=pk, change_of_mind_days=1)
        self.assertEqual(policy.terms_for(za, [self.beauty.pk])[self.beauty.pk].change_of_mind_days, 3)
        self.assertEqual(policy.terms_for(pk, [self.beauty.pk])[self.beauty.pk].change_of_mind_days, 10)
        self.assertEqual(policy.terms_for(za, [self.tech.pk])[self.tech.pk].change_of_mind_days, 30)      # PK's rule does not leak

    def test_an_inactive_rule_is_ignored(self):
        ReturnRule.objects.create(category=self.beauty, change_of_mind_days=0, is_active=False)
        self.assertEqual(policy.terms_for(Country.objects.get(pk="ZA"), [self.beauty.pk])[self.beauty.pk].change_of_mind_days, 30)

    def test_checkout_snapshots_the_terms_so_later_edits_do_not_rewrite_the_order(self):
        ReturnPolicy.objects.filter(country_id="ZA").update(window_days=30, defect_days=180, restocking_fee_bps=500)
        order = self.delivered((self.bb, 1))
        self.assertEqual(self.item(order, self.bb).return_terms, {"change_of_mind_days": 30, "defect_days": 180, "restocking_fee_bps": 500})
        ReturnPolicy.objects.filter(country_id="ZA").update(window_days=1, defect_days=1, restocking_fee_bps=0)
        ReturnRule.objects.create(category=self.beauty, change_of_mind_days=0)
        res = self.ask(order, [self.item(order, self.bb)])
        self.assertEqual(res.status_code, 201, res.content)                                              # still the old terms
        self.assertEqual(res.json()["restocking_fee_cents"], 500)

    def test_an_order_from_before_returns_existed_falls_back_to_its_own_window(self):
        order = self.delivered((self.bb, 1))
        OrderItem.objects.filter(order=order).update(return_terms={})
        Order.objects.filter(pk=order.pk).update(return_window_days=12)
        item = OrderItem.objects.select_related("order").get(order=order)
        terms = policy.line_terms(item)
        self.assertEqual((terms.change_of_mind_days, terms.defect_days, terms.restocking_fee_bps), (12, 12, 0))


class EligibilityTests(ReturnsTestCase):
    def returnable(self, order, reason=None, token=None):
        params = {"reason": reason} if reason else {}
        res = self.get(f"{ME}/orders/{order.pk}/returnable", token or self.buyer_token, **params)
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()

    def test_a_delivered_line_is_open_with_both_deadlines(self):
        body = self.returnable(self.delivered((self.bb, 1)))
        line = body["lines"][0]
        self.assertTrue(body["can_request"])
        self.assertEqual((line["eligible"], line["why_not"]), (True, ""))
        self.assertTrue(line["change_of_mind_until"] and line["defect_until"])
        self.assertEqual([r["code"] for r in body["reasons"]][:2], ["changed_mind", "wrong_size"])

    def test_nothing_is_returnable_before_delivery(self):
        order = self.order((self.bb, 1))
        line = self.returnable(order)["lines"][0]
        self.assertEqual((line["eligible"], line["why_not"]), (False, "not_delivered"))
        self.assertEqual(self.ask(order, [self.item(order, self.bb)]).status_code, 409)

    def test_a_closed_change_of_mind_window_still_allows_a_defect(self):
        ReturnPolicy.objects.filter(country_id="ZA").update(window_days=7, defect_days=180)
        order = self.delivered((self.bb, 1))
        self.backdate_delivery(order, 20)
        self.assertEqual(self.returnable(order, "changed_mind")["lines"][0]["why_not"], "window_closed")
        self.assertEqual(self.returnable(order, "damaged")["lines"][0]["eligible"], True)
        self.assertEqual(self.ask(order, [self.item(order, self.bb)], "changed_mind").status_code, 409)
        self.assertEqual(self.ask(order, [self.item(order, self.bb)], "damaged", photos=[png()]).status_code, 201)

    def test_zero_days_means_not_accepted(self):
        ReturnRule.objects.create(category=self.beauty, change_of_mind_days=0)
        order = self.delivered((self.bb, 1), (self.cable, 1))
        self.assertEqual(self.get(f"{ME}/orders/{order.pk}/returnable", self.buyer_token, reason="changed_mind").json()["lines"][0]["why_not"] or "x", "not_returnable")
        res = self.ask(order, [self.item(order, self.bb)], "changed_mind")
        self.assertEqual((res.status_code, res.json()["detail"]), (409, "not_returnable"))
        self.assertEqual(self.ask(order, [self.item(order, self.cable)], "changed_mind").status_code, 201)

    def test_a_line_already_in_a_request_is_blocked_until_it_is_withdrawn(self):
        order = self.delivered((self.bb, 1))
        req = self.request(order, [self.item(order, self.bb)])
        self.assertEqual(self.returnable(order)["lines"][0]["why_not"], "already_requested")
        self.assertEqual(self.ask(order, [self.item(order, self.bb)]).status_code, 409)
        self.assertEqual(self.post(f"{ME}/returns/{req.pk}/cancel", None, self.buyer_token).status_code, 200)
        self.assertEqual(self.returnable(order)["lines"][0]["eligible"], True)

    def test_a_goodwill_returned_line_reads_already_returned(self):
        order = self.delivered((self.bb, 1))
        self.assertEqual(self.post(f"/api/v1/seller/orders/{order.pk}/status", {"status": "returned"}, self.token_a).status_code, 200)
        self.assertEqual(self.returnable(order)["lines"][0]["why_not"], "already_returned")

    def test_someone_elses_order_is_not_found(self):
        order = self.delivered((self.bb, 1))
        self.assertEqual(self.get(f"{ME}/orders/{order.pk}/returnable", self.other_token).status_code, 404)
        self.assertEqual(self.ask(order, [self.item(order, self.bb)], token=self.other_token).status_code, 404)

    def test_a_policy_switched_off_closes_returns(self):
        order = self.delivered((self.bb, 1))
        OrderItem.objects.filter(order=order).update(return_terms={"change_of_mind_days": 0, "defect_days": 0, "restocking_fee_bps": 0})
        self.assertFalse(self.returnable(order)["can_request"])


# ======================================================================================
# Asking for a return
# ======================================================================================


class RequestTests(ReturnsTestCase):
    def test_the_happy_path_creates_a_request_with_a_reference_and_a_response_deadline(self):
        order = self.delivered((self.bb, 1), (self.lip, 1))
        res = self.ask(order, [self.item(order, self.bb)], note="Not for me")
        self.assertEqual(res.status_code, 201, res.content)
        body = res.json()
        self.assertEqual(body["status"], "requested")
        self.assertRegex(body["reference"], r"^RET-\d+-1$")
        self.assertEqual((body["items_count"], body["note"], body["can_cancel"]), (1, "Not for me", True))
        self.assertEqual([i["product_name"] for i in body["items"]], ["BB Cream"])
        self.assertTrue(body["response_due_at"])
        self.assertEqual([e["actor"] for e in body["events"]], ["buyer"])

    def test_a_second_request_on_the_same_order_gets_the_next_number(self):
        order = self.delivered((self.bb, 1), (self.lip, 1))
        first = self.request(order, [self.item(order, self.bb)])
        second = self.request(order, [self.item(order, self.lip)])
        self.assertEqual((first.reference.rsplit("-", 1)[1], second.reference.rsplit("-", 1)[1]), ("1", "2"))

    def test_unknown_reason_or_nothing_chosen_or_two_stores_is_refused(self):
        order = self.delivered((self.bb, 1), (self.watch, 1))
        bb, watch = self.item(order, self.bb), self.item(order, self.watch)
        self.assertEqual(self.ask(order, [bb], "nope").json()["detail"], "unknown_reason")
        self.assertEqual(self.ask(order, []).status_code, 422)
        res = self.ask(order, [bb, watch])
        self.assertEqual((res.status_code, res.json()["detail"]), (422, "one_store_per_request"))

    def test_a_reason_that_is_not_offered_in_the_market_is_refused(self):
        only_pk = ReturnReason.objects.create(code="pk_only", label={"en": "PK only"}, kind="other")
        only_pk.countries.set([make_country("PK")])
        order = self.delivered((self.bb, 1))
        self.assertEqual(self.ask(order, [self.item(order, self.bb)], "pk_only").json()["detail"], "unknown_reason")

    def test_an_inactive_reason_is_refused(self):
        ReturnReason.objects.filter(code="changed_mind").update(is_active=False)
        order = self.delivered((self.bb, 1))
        self.assertEqual(self.ask(order, [self.item(order, self.bb)]).status_code, 422)

    def test_a_damage_claim_needs_a_photo_and_photos_are_checked(self):
        order = self.delivered((self.bb, 1))
        bb = self.item(order, self.bb)
        self.assertEqual(self.ask(order, [bb], "damaged").json()["detail"], "photo_required")
        bad = SimpleUploadedFile("x.png", b"not an image", content_type="image/png")
        self.assertEqual(self.ask(order, [bb], "damaged", photos=[bad]).status_code, 422)
        too_many = [png(f"{i}.png") for i in range(6)]
        self.assertEqual(self.ask(order, [bb], "damaged", photos=too_many).status_code, 422)
        res = self.ask(order, [bb], "damaged", photos=[png()])
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(len(res.json()["photos"]), 1)

    def test_the_photo_limit_is_an_admin_setting(self):
        Setting.objects.create(scope="global", key="returns.max_photos", value=1)
        order = self.delivered((self.bb, 1))
        self.assertEqual(self.ask(order, [self.item(order, self.bb)], "damaged", photos=[png("a.png"), png("b.png")]).status_code, 422)

    def test_change_of_mind_refund_is_the_items_less_the_restocking_fee(self):
        ReturnPolicy.objects.filter(country_id="ZA").update(restocking_fee_bps=1000)          # 10 %
        order = self.delivered((self.bb, 1), (self.lip, 1))
        body = self.ask(order, [self.item(order, self.bb)]).json()
        self.assertEqual((body["items_cents"], body["restocking_fee_cents"], body["delivery_refund_cents"], body["refund_cents"]), (10000, 1000, 0, 9000))

    def test_a_defect_has_no_restocking_fee_and_a_full_return_refunds_delivery(self):
        ReturnPolicy.objects.filter(country_id="ZA").update(restocking_fee_bps=1000)
        order = self.delivered((self.bb, 1))
        delivery = OrderDelivery.objects.get(order=order, store=self.store_a).price_cents
        body = self.ask(order, [self.item(order, self.bb)], "damaged", photos=[png()]).json()
        self.assertEqual((body["restocking_fee_cents"], body["delivery_refund_cents"], body["refund_cents"]), (0, delivery, 10000 + delivery))

    def test_delivery_is_not_refunded_for_part_of_the_store_order_or_for_change_of_mind(self):
        order = self.delivered((self.bb, 1), (self.lip, 1))
        part = self.ask(order, [self.item(order, self.bb)], "damaged", photos=[png()]).json()
        self.assertEqual(part["delivery_refund_cents"], 0)
        order2 = self.delivered((self.cable, 1))
        mind = self.ask(order2, [self.item(order2, self.cable)], "changed_mind").json()
        self.assertEqual(mind["delivery_refund_cents"], 0)

    def test_delivery_is_refunded_only_once_across_requests(self):
        order = self.delivered((self.bb, 1), (self.lip, 1))
        first = self.request(order, [self.item(order, self.bb)], "damaged", photos=[png()])
        self.assertEqual(first.delivery_refund_cents, 0)
        second = self.request(order, [self.item(order, self.lip)], "damaged", photos=[png()])        # the rest of the store's order
        self.assertGreater(second.delivery_refund_cents, 0)

    def test_the_vouchers_share_comes_off_the_refund(self):
        Voucher.objects.create(code="SAVE20", type="percent", value=20)
        order = self.delivered((self.bb, 1), (self.lip, 1), voucher="SAVE20")        # 15000 - 3000
        body = self.ask(order, [self.item(order, self.bb)]).json()
        self.assertEqual((body["items_cents"], body["discount_cents"], body["refund_cents"]), (10000, 2000, 8000))

    def test_the_terms_in_force_are_kept_on_the_request(self):
        order = self.delivered((self.bb, 1))
        req = self.request(order, [self.item(order, self.bb)])
        self.assertEqual(req.terms["reason_kind"], "change_of_mind")
        self.assertIn(str(self.item(order, self.bb).pk), req.terms["deadlines"])

    def test_auto_approve_is_an_admin_setting(self):
        Setting.objects.create(scope="global", key="returns.auto_approve", value=True)
        order = self.delivered((self.bb, 1))
        req = self.request(order, [self.item(order, self.bb)])
        self.assertEqual(req.status, "approved")
        self.assertEqual(list(req.events.values_list("actor", flat=True)), ["buyer", "system"])

    def test_the_response_time_is_an_admin_setting(self):
        Setting.objects.create(scope="global", key="returns.seller_response_days", value=10)
        order = self.delivered((self.bb, 1))
        req = self.request(order, [self.item(order, self.bb)])
        self.assertAlmostEqual((req.response_due_at - req.created_at).total_seconds(), 10 * 86400, delta=5)

    def test_the_buyer_gets_a_confirmation_email(self):
        mail.outbox.clear()
        order = self.delivered((self.bb, 1))
        mail.outbox.clear()
        req = self.request(order, [self.item(order, self.bb)])
        self.assertTrue(any(req.reference in m.subject and m.to == ["olivia@example.com"] for m in mail.outbox))

    def test_the_list_only_shows_my_returns(self):
        order = self.delivered((self.bb, 1))
        self.request(order, [self.item(order, self.bb)])
        self.assertEqual(self.get(f"{ME}/returns", self.buyer_token).json()["total"], 1)
        self.assertEqual(self.get(f"{ME}/returns", self.other_token).json()["total"], 0)
        self.assertEqual(self.get(f"{ME}/returns", self.buyer_token, tab="refunded").json()["total"], 0)

    def test_someone_elses_return_is_not_found(self):
        order = self.delivered((self.bb, 1))
        req = self.request(order, [self.item(order, self.bb)])
        self.assertEqual(self.get(f"{ME}/returns/{req.pk}", self.other_token).status_code, 404)
        self.assertEqual(self.post(f"{ME}/returns/{req.pk}/cancel", None, self.other_token).status_code, 404)

    def test_guests_cannot_return(self):
        order = self.delivered((self.bb, 1))
        self.assertEqual(self.get(f"{ME}/orders/{order.pk}/returnable").status_code, 401)


# ======================================================================================
# The workflow and the money
# ======================================================================================


class WorkflowTests(ReturnsTestCase):
    def test_approve_receive_refund_walks_the_whole_path(self):
        order = self.delivered((self.bb, 1), (self.lip, 1))
        bb = self.item(order, self.bb)
        stock = bb.variant.stock_qty
        req = self.request(order, [bb])
        res = self.seller_post(req, "approve")
        self.assertEqual((res.status_code, res.json()["status"]), (200, "approved"))
        self.assertEqual(self.get(f"{ME}/returns/{req.pk}", self.buyer_token).json()["shipment"]["courier"], "manual")
        res = self.seller_post(req, "received", {"condition": "good"})
        body = res.json()
        self.assertEqual((res.status_code, body["status"], body["refund_status"]), (200, "refunded", "pending"))
        bb.refresh_from_db()
        bb.variant.refresh_from_db()
        self.assertEqual(bb.status, "returned")
        self.assertEqual(bb.variant.stock_qty, stock + 1)
        self.assertTrue(StockMovement.objects.filter(variant=bb.variant, reason="return", order=order).exists())
        self.assertEqual([e["actor"] for e in body["events"]], ["buyer", "seller", "seller", "seller"])

    def test_a_partial_return_leaves_the_order_delivered_and_part_refunded(self):
        order = self.delivered((self.bb, 1), (self.lip, 1))
        self.returned_to_seller(self.request(order, [self.item(order, self.bb)]))
        order.refresh_from_db()
        self.assertEqual((order.status, order.payment_status), ("delivered", "partially_refunded"))
        self.returned_to_seller(self.request(order, [self.item(order, self.lip)]))
        order.refresh_from_db()
        self.assertEqual((order.status, order.payment_status), ("returned", "refunded"))

    def test_the_sellers_ledger_gets_the_refund_and_the_commission_share_back(self):
        order = self.delivered((self.bb, 1), (self.lip, 1))          # 15000 of items; 5 % commission = 750
        self.assertEqual(self.amount(self.store_a, T.FEE, order=order), -750)
        req = self.returned_to_seller(self.request(order, [self.item(order, self.bb)]))       # 10000 of the 15000
        self.assertEqual(self.amount(self.store_a, T.REFUND, order=order), -10000)
        self.assertEqual(self.amount(self.store_a, T.FEE_REFUND, order=order), 500)           # two thirds of the commission
        self.assertEqual(req.ledger_entries.count(), 2)

    def test_a_voucher_is_respected_in_both_the_refund_and_the_commission(self):
        Voucher.objects.create(code="SAVE20", type="percent", value=20)
        order = self.delivered((self.bb, 1), (self.lip, 1), voucher="SAVE20")                 # net 12000, commission 600
        self.returned_to_seller(self.request(order, [self.item(order, self.bb)]))
        self.assertEqual(self.amount(self.store_a, T.REFUND, order=order), -8000)
        self.assertEqual(self.amount(self.store_a, T.FEE_REFUND, order=order), 400)

    def test_returning_everything_gives_back_the_whole_sale_and_commission(self):
        order = self.delivered((self.bb, 1), (self.lip, 1))
        sale = self.amount(self.store_a, T.SALE, order=order)
        self.returned_to_seller(self.request(order, [self.item(order, self.bb)], "damaged", photos=[png()]))
        self.returned_to_seller(self.request(order, [self.item(order, self.lip)], "damaged", photos=[png()]))
        self.assertEqual(self.amount(self.store_a, T.REFUND, order=order), -sale)             # items plus the delivery, once
        self.assertEqual(self.amount(self.store_a, T.FEE_REFUND, order=order), 750)

    def test_a_later_whole_refund_only_pays_what_is_left(self):
        order = self.delivered((self.bb, 1), (self.lip, 1))
        self.returned_to_seller(self.request(order, [self.item(order, self.bb)]))
        sale = self.amount(self.store_a, T.SALE, order=order)
        ledger.refund(order)                                                                 # e.g. staff refund the rest by hand
        self.assertEqual(self.amount(self.store_a, T.REFUND, order=order), -sale)
        self.assertEqual(self.amount(self.store_a, T.FEE_REFUND, order=order), 750)
        before = LedgerEntry.objects.count()
        ledger.refund(order)
        self.assertEqual(LedgerEntry.objects.count(), before)                                # and never twice

    def test_one_refund_per_request_even_if_asked_twice(self):
        order = self.delivered((self.bb, 1))
        req = self.returned_to_seller(self.request(order, [self.item(order, self.bb)]))
        before = LedgerEntry.objects.count()
        self.assertEqual(ledger.refund_items(req), [])
        self.assertEqual(LedgerEntry.objects.count(), before)
        with self.assertRaises(IntegrityError), transaction.atomic():
            LedgerEntry.objects.create(store=self.store_a, type=T.REFUND, amount_cents=-1, currency="ZAR", order=order, return_request=req, reference="DUP-1")

    def test_a_partly_refunded_sale_still_gets_released_when_delivered_later(self):
        order = self.delivered((self.bb, 1), (self.lip, 1))
        sale = LedgerEntry.objects.get(order=order, store=self.store_a, type=T.SALE)
        self.assertIsNotNone(sale.available_at)                    # delivered -> hold started
        self.returned_to_seller(self.request(order, [self.item(order, self.bb)]))
        self.assertEqual(ledger._fully_refunded_stores(order), set())

    def test_the_restock_choice_is_respected(self):
        order = self.delivered((self.bb, 1))
        bb = self.item(order, self.bb)
        stock = bb.variant.stock_qty
        req = self.request(order, [bb])
        self.seller_post(req, "approve")
        self.seller_post(req, "received", {"condition": "good", "restock": False})
        bb.variant.refresh_from_db()
        self.assertEqual(bb.variant.stock_qty, stock)

    def test_money_owed_to_the_buyer_is_marked_paid_by_staff(self):
        order = self.delivered((self.bb, 1))
        req = self.returned_to_seller(self.request(order, [self.item(order, self.bb)]))
        res = self.staff_post(req, "refund-paid", {"reference": "EFT-991"})
        self.assertEqual((res.status_code, res.json()["refund_status"]), (200, "completed"))
        self.assertEqual(self.staff_post(req, "refund-paid").status_code, 409)               # nothing is owed any more

    def test_the_buyer_is_emailed_at_each_step(self):
        order = self.delivered((self.bb, 1))
        req = self.request(order, [self.item(order, self.bb)])
        mail.outbox.clear()
        self.returned_to_seller(req)
        subjects = [m.subject for m in mail.outbox if req.reference in m.subject]
        self.assertTrue(any("approved" in s for s in subjects) and any("refund" in s for s in subjects), subjects)

    # -- saying no ---------------------------------------------------------------------------
    def test_a_rejection_needs_a_reason_the_buyer_can_see(self):
        order = self.delivered((self.bb, 1))
        req = self.request(order, [self.item(order, self.bb)])
        self.assertEqual(self.seller_post(req, "reject", {"reason": ""}).status_code, 422)
        res = self.seller_post(req, "reject", {"reason": "Opened and used"})
        self.assertEqual((res.status_code, res.json()["status"]), (200, "rejected"))
        body = self.get(f"{ME}/returns/{req.pk}", self.buyer_token).json()
        self.assertEqual((body["seller_note"], body["can_escalate"], body["can_cancel"]), ("Opened and used", True, False))
        self.assertEqual(self.amount(self.store_a, T.REFUND, order=order), 0)

    def test_the_buyer_disputes_and_staff_overrule_the_seller(self):
        order = self.delivered((self.bb, 1))
        req = self.request(order, [self.item(order, self.bb)])
        self.seller_post(req, "reject", {"reason": "Not eligible"})
        res = self.post(f"{ME}/returns/{req.pk}/escalate", {"note": "It was sealed"}, self.buyer_token)
        self.assertEqual((res.status_code, res.json()["status"]), (200, "escalated"))
        self.assertTrue(StoreNotification.objects.filter(store=self.store_a, kind="return_escalated").exists())
        res = self.staff_post(req, "decide", {"outcome": "approve", "note": "Sealed, agreed"})
        self.assertEqual((res.status_code, res.json()["status"]), (200, "approved"))
        self.assertTrue(StoreNotification.objects.filter(store=self.store_a, kind="return_decided").exists())
        self.assertEqual(self.seller_post(req, "received", {"condition": "good"}).json()["status"], "refunded")

    def test_staff_can_uphold_the_rejection_and_that_is_final(self):
        order = self.delivered((self.bb, 1))
        req = self.request(order, [self.item(order, self.bb)])
        self.seller_post(req, "reject", {"reason": "Not eligible"})
        self.post(f"{ME}/returns/{req.pk}/escalate", None, self.buyer_token)
        self.assertEqual(self.staff_post(req, "decide", {"outcome": "reject"}).status_code, 422)          # a reason is needed
        res = self.staff_post(req, "decide", {"outcome": "reject", "note": "Used item"})
        body = res.json()
        self.assertEqual((body["status"], body["is_final"], body["staff_note"]), ("rejected", True, "Used item"))
        self.assertEqual(self.post(f"{ME}/returns/{req.pk}/escalate", None, self.buyer_token).status_code, 409)

    def test_disputes_can_be_switched_off(self):
        Setting.objects.create(scope="global", key="returns.allow_escalation", value=False)
        order = self.delivered((self.bb, 1))
        req = self.request(order, [self.item(order, self.bb)])
        body = self.seller_post(req, "reject", {"reason": "Not eligible"}).json()
        self.assertTrue(body["is_final"])
        self.assertEqual(self.post(f"{ME}/returns/{req.pk}/escalate", None, self.buyer_token).status_code, 409)

    def test_only_a_rejected_return_can_be_disputed(self):
        order = self.delivered((self.bb, 1))
        req = self.request(order, [self.item(order, self.bb)])
        self.assertEqual(self.post(f"{ME}/returns/{req.pk}/escalate", None, self.buyer_token).status_code, 409)

    # -- damaged on arrival ------------------------------------------------------------------
    def test_a_damaged_parcel_waits_for_staff_and_no_money_moves_yet(self):
        order = self.delivered((self.bb, 1))
        req = self.returned_to_seller(self.request(order, [self.item(order, self.bb)]), "damaged")
        self.assertEqual((req.status, req.refund_status), ("received", "none"))
        self.assertEqual(self.amount(self.store_a, T.REFUND, order=order), 0)
        res = self.staff_post(req, "decide", {"outcome": "approve"})
        self.assertEqual((res.json()["status"], res.json()["refund_status"]), ("refunded", "pending"))
        self.assertEqual(self.amount(self.store_a, T.REFUND, order=order), -10000)
        self.assertFalse(ReturnRequest.objects.get(pk=req.pk).restocked)              # damaged goods do not go back on the shelf

    def test_staff_can_refuse_a_refund_for_a_damaged_return(self):
        order = self.delivered((self.bb, 1))
        req = self.returned_to_seller(self.request(order, [self.item(order, self.bb)]), "damaged")
        res = self.staff_post(req, "decide", {"outcome": "reject", "note": "Damaged by the buyer"})
        self.assertEqual((res.json()["status"], res.json()["is_final"]), ("rejected", True))
        self.assertEqual(self.amount(self.store_a, T.REFUND, order=order), 0)

    def test_staff_cannot_decide_a_request_that_needs_no_decision(self):
        order = self.delivered((self.bb, 1))
        req = self.request(order, [self.item(order, self.bb)])
        self.assertEqual(self.staff_post(req, "decide", {"outcome": "approve"}).status_code, 409)

    # -- withdrawing ---------------------------------------------------------------------------
    def test_the_buyer_can_withdraw_before_it_is_sent_and_the_seller_is_told(self):
        order = self.delivered((self.bb, 1))
        req = self.request(order, [self.item(order, self.bb)])
        self.seller_post(req, "approve")
        res = self.post(f"{ME}/returns/{req.pk}/cancel", None, self.buyer_token)
        self.assertEqual((res.status_code, res.json()["status"]), (200, "cancelled"))
        self.assertTrue(StoreNotification.objects.filter(store=self.store_a, kind="return_cancelled").exists())
        self.assertEqual(ReturnRequest.objects.get(pk=req.pk).shipments.get().status, "cancelled")

    def test_it_cannot_be_withdrawn_once_it_is_on_its_way(self):
        order = self.delivered((self.bb, 1))
        req = self.request(order, [self.item(order, self.bb)])
        self.seller_post(req, "approve")
        self.assertEqual(self.post(f"{ME}/returns/{req.pk}/tracking", {"tracking_number": "TCS123456"}, self.buyer_token).status_code, 200)
        self.assertEqual(self.post(f"{ME}/returns/{req.pk}/cancel", None, self.buyer_token).status_code, 409)

    def test_steps_out_of_order_are_refused(self):
        order = self.delivered((self.bb, 1))
        req = self.request(order, [self.item(order, self.bb)])
        self.assertEqual(self.seller_post(req, "received", {"condition": "good"}).status_code, 409)       # not approved yet
        self.assertEqual(self.post(f"{ME}/returns/{req.pk}/tracking", {"tracking_number": "TCS123"}, self.buyer_token).status_code, 409)
        self.seller_post(req, "approve")
        self.assertEqual(self.seller_post(req, "approve").status_code, 409)
        self.assertEqual(self.seller_post(req, "reject", {"reason": "late"}).status_code, 409)

    def test_the_buyers_tracking_number_is_stored_and_shown(self):
        order = self.delivered((self.bb, 1))
        req = self.request(order, [self.item(order, self.bb)])
        self.seller_post(req, "approve")
        self.post(f"{ME}/returns/{req.pk}/tracking", {"tracking_number": "TCS123456", "carrier": "TCS"}, self.buyer_token)
        ship = self.get(f"{ME}/returns/{req.pk}", self.buyer_token).json()["shipment"]
        self.assertEqual((ship["tracking_number"], ship["status"]), ("TCS123456", "in_transit"))
        self.assertEqual(ship["dropoff_address"]["name"], "Huda Beauty")

    # -- no double payment between the two ways of returning ---------------------------------
    def test_a_goodwill_return_is_blocked_while_a_customer_return_is_open(self):
        order = self.delivered((self.bb, 1))
        req = self.request(order, [self.item(order, self.bb)])
        res = self.post(f"/api/v1/seller/orders/{order.pk}/status", {"status": "returned"}, self.token_a)
        self.assertEqual((res.status_code, res.json()["detail"]), (409, "return_in_progress"))
        res = self.post(f"/api/v1/admin/orders/{order.pk}/status", {"status": "refunded"}, self.staff_token)
        self.assertEqual((res.status_code, res.json()["detail"]), (409, "return_in_progress"))
        self.post(f"{ME}/returns/{req.pk}/cancel", None, self.buyer_token)
        self.assertEqual(self.post(f"/api/v1/seller/orders/{order.pk}/status", {"status": "returned"}, self.token_a).status_code, 200)

    def test_another_stores_open_return_does_not_block_this_store(self):
        order = self.delivered((self.bb, 1), (self.watch, 1))
        self.request(order, [self.item(order, self.watch)])
        self.assertEqual(self.post(f"/api/v1/seller/orders/{order.pk}/status", {"status": "returned"}, self.token_a).status_code, 200)


# ======================================================================================
# Who can see and do what
# ======================================================================================


class AccessTests(ReturnsTestCase):
    def test_a_seller_sees_only_their_stores_returns_with_the_customer(self):
        order = self.delivered((self.bb, 1), (self.watch, 1))
        mine = self.request(order, [self.item(order, self.bb)])
        self.request(order, [self.item(order, self.watch)])
        rows = self.get(SELLER, self.token_a).json()
        self.assertEqual((rows["total"], rows["items"][0]["reference"], rows["items"][0]["customer_name"]), (1, mine.reference, "Olivia Martin"))
        self.assertEqual(self.get(f"{SELLER}/{mine.pk}", self.token_b).status_code, 404)
        self.assertEqual(self.seller_post(mine, "approve", token=self.token_b).status_code, 404)

    def test_the_tab_counts_and_overdue(self):
        order = self.delivered((self.bb, 1), (self.lip, 1))
        a = self.request(order, [self.item(order, self.bb)])
        self.request(order, [self.item(order, self.lip)])
        self.seller_post(a, "approve")
        ReturnRequest.objects.exclude(pk=a.pk).update(response_due_at=timezone.now() - timedelta(hours=1))
        tabs = self.get(f"{SELLER}/summary", self.token_a).json()
        self.assertEqual((tabs["all"], tabs["open"], tabs["requested"], tabs["approved"], tabs["overdue"]), (2, 2, 1, 1, 1))
        self.assertEqual(self.get(SELLER, self.token_a, tab="overdue").json()["total"], 1)
        self.assertTrue(self.get(SELLER, self.token_a, tab="overdue").json()["items"][0]["is_overdue"])

    def test_a_team_member_needs_the_orders_section(self):
        order = self.delivered((self.bb, 1))
        req = self.request(order, [self.item(order, self.bb)])
        user = make_user(full_name="Clerk")
        StoreMember.objects.create(store=self.store_a, user=user, email=user.email, permissions={"orders": "view"}, status="active", joined_at=timezone.now())
        token = self.token_for(user)
        self.assertEqual(self.get(SELLER, token).status_code, 200)
        self.assertEqual(self.seller_post(req, "approve", token=token).status_code, 403)
        StoreMember.objects.filter(user=user).update(permissions={"orders": "manage"})
        self.assertEqual(self.seller_post(req, "approve", token=token).status_code, 200)
        StoreMember.objects.filter(user=user).update(permissions={"products": "manage"})
        self.assertEqual(self.get(SELLER, token).status_code, 403)

    def test_only_staff_use_the_admin_queue(self):
        order = self.delivered((self.bb, 1))
        req = self.request(order, [self.item(order, self.bb)])
        self.assertEqual(self.get(ADMIN, self.buyer_token).status_code, 403)
        self.assertEqual(self.get(ADMIN, self.token_a).status_code, 403)
        self.assertEqual(self.get(f"{ADMIN}/{req.pk}", self.staff_token).status_code, 200)

    def test_the_staff_queue_puts_decisions_first_and_filters(self):
        order = self.delivered((self.bb, 1), (self.lip, 1), (self.watch, 1))
        plain = self.request(order, [self.item(order, self.lip)])
        disputed = self.request(order, [self.item(order, self.bb)])
        self.seller_post(disputed, "reject", {"reason": "Not eligible"})
        self.post(f"{ME}/returns/{disputed.pk}/escalate", None, self.buyer_token)
        other_store = self.request(order, [self.item(order, self.watch)])
        rows = self.get(ADMIN, self.staff_token).json()["items"]
        self.assertEqual(rows[0]["reference"], disputed.reference)
        self.assertTrue(rows[0]["can_decide"])
        self.assertEqual(self.get(ADMIN, self.staff_token, tab="escalated").json()["total"], 1)
        self.assertEqual(self.get(ADMIN, self.staff_token, store_id=str(self.store_b.pk)).json()["items"][0]["reference"], other_store.reference)
        self.assertEqual(self.get(ADMIN, self.staff_token, q=plain.reference).json()["total"], 1)
        self.assertEqual(self.get(ADMIN, self.staff_token, country="PK").json()["total"], 0)
        self.assertEqual(self.get(f"{ADMIN}/summary", self.staff_token).json()["all"], 3)

    def test_the_seller_is_notified_and_a_noisy_kind_can_be_switched_off(self):
        order = self.delivered((self.bb, 1), (self.lip, 1))
        self.request(order, [self.item(order, self.bb)])
        note = StoreNotification.objects.get(store=self.store_a, kind="return_requested")
        self.assertEqual((note.category, note.params["order"], note.params["items"]), ("returns", order.order_number, 1))
        feed = self.get("/api/v1/seller/notifications", self.token_a, tab="returns").json()["items"]
        self.assertEqual(feed[0]["title"], "Return Requested")
        self.assertIn("Olivia Martin", feed[0]["body"])
        NotificationTemplate.objects.filter(kind="return_requested").update(is_active=False)
        before = StoreNotification.objects.count()
        self.request(order, [self.item(order, self.lip)])
        self.assertEqual(StoreNotification.objects.count(), before)


# ======================================================================================
# The rules, edited by staff
# ======================================================================================


class AdminRulesTests(ReturnsTestCase):
    def test_the_public_policy_follows_the_market_and_the_language(self):
        ReturnPolicy.objects.filter(country_id="ZA").update(window_days=30, defect_days=180, restocking_fee_bps=250,
                                                           terms={"en": "Items must be unused."}, url="https://asknget.com/returns")
        body = self.get("/api/v1/returns/policy").json()
        self.assertEqual((body["country"], body["accepts_returns"], body["change_of_mind_days"], body["defect_days"]), ("ZA", True, 30, 180))
        self.assertEqual((body["restocking_fee_bps"], body["terms"], body["courier"], body["max_photos"]), (250, "Items must be unused.", "manual", 5))
        self.assertIn("damaged", [r["code"] for r in body["reasons"]])
        make_country("PK")
        pk = self.get("/api/v1/returns/policy", region="PK").json()
        self.assertEqual((pk["country"], pk["change_of_mind_days"], pk["defect_days"]), ("PK", 7, 30))

    def test_a_market_with_no_returns_says_so(self):
        ReturnPolicy.objects.filter(country_id="ZA").update(is_active=False)
        body = self.get("/api/v1/returns/policy").json()
        self.assertEqual((body["accepts_returns"], body["reasons"], body["change_of_mind_days"]), (False, [], 0))

    def test_staff_set_a_policy_for_any_market(self):
        make_country("PK")
        res = self.put(f"{ADMIN}/policies/pk", {"is_active": True, "window_days": 14, "defect_days": 45, "restocking_fee_bps": 500,
                                                 "summary": {"en": "14 days", "ur": "۱۴ دن"}, "terms": {"en": "Unused only"}, "url": ""}, self.staff_token)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual((res.json()["country"], res.json()["window_days"]), ("PK", 14))
        self.assertEqual(self.get("/api/v1/returns/policy", region="PK").json()["defect_days"], 45)
        self.assertEqual({r["country"] for r in self.get(f"{ADMIN}/policies", self.staff_token).json()}, {"ZA", "PK"})

    def test_a_new_market_gets_a_policy_row_created_for_it(self):
        make_country("PK")
        ReturnPolicy.objects.filter(country_id="PK").delete()
        res = self.put(f"{ADMIN}/policies/PK", {"is_active": True, "window_days": 5, "defect_days": 10, "restocking_fee_bps": 0}, self.staff_token)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(ReturnPolicy.objects.get(country_id="PK").window_days, 5)

    def test_policy_validation(self):
        base = {"is_active": True, "window_days": 5, "defect_days": 10, "restocking_fee_bps": 0}
        self.assertEqual(self.put(f"{ADMIN}/policies/ZZ", base, self.staff_token).status_code, 404)
        self.assertEqual(self.put(f"{ADMIN}/policies/ZA", {**base, "restocking_fee_bps": 20000}, self.staff_token).status_code, 422)
        self.assertEqual(self.put(f"{ADMIN}/policies/ZA", {**base, "summary": {"xx": "bad language"}}, self.staff_token).status_code, 422)
        self.assertEqual(self.put(f"{ADMIN}/policies/ZA", base, self.token_a).status_code, 403)

    def test_staff_manage_reasons(self):
        res = self.post(f"{ADMIN}/reasons", {"code": "arrived_late", "label": {"en": "It arrived too late"}, "kind": "other", "countries": ["za"]}, self.staff_token)
        self.assertEqual((res.status_code, res.json()["countries"]), (201, ["ZA"]))
        rid = res.json()["id"]
        self.assertEqual(self.post(f"{ADMIN}/reasons", {"code": "arrived_late", "label": {"en": "x"}, "kind": "other"}, self.staff_token).status_code, 409)
        self.assertEqual(self.post(f"{ADMIN}/reasons", {"code": "bad_one", "label": {"xx": "x"}, "kind": "other"}, self.staff_token).status_code, 422)
        self.assertEqual(self.post(f"{ADMIN}/reasons", {"code": "bad_two", "label": {"en": "x"}, "kind": "other", "countries": ["QQ"]}, self.staff_token).status_code, 422)
        self.assertIn("arrived_late", [r["code"] for r in self.get("/api/v1/returns/policy").json()["reasons"]])
        res = self.patch(f"{ADMIN}/reasons/{rid}", {"is_active": False, "requires_photo": True}, self.staff_token)
        self.assertEqual((res.status_code, res.json()["is_active"], res.json()["requires_photo"]), (200, False, True))
        self.assertNotIn("arrived_late", [r["code"] for r in self.get("/api/v1/returns/policy").json()["reasons"]])
        self.assertEqual(self.patch(f"{ADMIN}/reasons/{rid}", {"countries": []}, self.staff_token).json()["countries"], [])

    def test_staff_manage_category_rules_and_new_orders_follow_them(self):
        res = self.post(f"{ADMIN}/rules", {"category_id": str(self.beauty.pk), "country": "ZA", "change_of_mind_days": 0, "note": "Opened cosmetics"}, self.staff_token)
        self.assertEqual((res.status_code, res.json()["category_name"], res.json()["change_of_mind_days"]), (201, "Beauty", 0))
        rule_id = res.json()["id"]
        self.assertEqual(self.post(f"{ADMIN}/rules", {"category_id": str(self.beauty.pk), "country": "ZA"}, self.staff_token).status_code, 409)
        self.assertEqual(self.post(f"{ADMIN}/rules", {"category_id": "00000000-0000-0000-0000-000000000000"}, self.staff_token).status_code, 422)
        order = self.delivered((self.bb, 1))
        self.assertEqual(self.ask(order, [self.item(order, self.bb)]).json()["detail"], "not_returnable")
        self.assertEqual(len(self.get(f"{ADMIN}/rules", self.staff_token, country="ZA").json()), 1)
        res = self.patch(f"{ADMIN}/rules/{rule_id}", {"clear_change_of_mind": True, "defect_days": 90}, self.staff_token)
        self.assertEqual((res.json()["change_of_mind_days"], res.json()["defect_days"]), (None, 90))
        self.assertEqual(self.delete(f"{ADMIN}/rules/{rule_id}", self.staff_token).status_code, 204)
        self.assertEqual(self.delete(f"{ADMIN}/rules/{rule_id}", self.staff_token).status_code, 404)

    def test_the_rules_need_staff(self):
        for path in ("policies", "reasons", "rules"):
            self.assertEqual(self.get(f"{ADMIN}/{path}", self.buyer_token).status_code, 403, path)

    def test_other_markets_orders_use_their_own_policy(self):
        pk = make_country("PK")
        store = make_store(name="Karachi Crafts", country=pk)
        product = make_product(store, self.beauty, "Kurta", 5000, None, stock=5)
        res = self.post(f"{CART}/items", {"variant_id": str(product.variants.first().id), "qty": 1}, self.buyer_token, region="PK")
        self.assertEqual(res.status_code, 200, res.content)
        res = self.client.post(CHECKOUT, {"contact": CONTACT, "address": {**ADDRESS, "country": "PK"}, "payment_method_type": "card"}, content_type="application/json",
                               headers={"Authorization": f"Bearer {self.buyer_token}", "X-Region": "PK"})
        self.assertEqual(res.status_code, 201, res.content)
        item = OrderItem.objects.get(order__order_number=res.json()["order"]["order_number"])
        self.assertEqual(item.return_terms["change_of_mind_days"], 7)


# ======================================================================================
# The courier seam
# ======================================================================================


class FakeCourier(couriers.Courier):
    slug = "fastship"

    def __init__(self):
        self.cancelled = []
        self.fail = False

    def book_return(self, request, pickup, dropoff):
        if self.fail:
            raise RuntimeError("courier is down")
        return couriers.Booking(tracking_number="FS-1001", tracking_url="https://fastship.example/FS-1001", label_url="https://fastship.example/label.pdf",
                                external_id="BK-77", status="booked", raw={"pickup": pickup["city"], "to": dropoff["name"]})

    def cancel(self, shipment):
        self.cancelled.append(shipment.external_id)

    def track(self, shipment):
        return couriers.TrackingUpdate(status="delivered", external_id=shipment.external_id, note="Left at the seller's door")

    def parse_webhook(self, body, headers):
        if headers.get("X-Signature") != "ok":
            raise NotImplementedError
        return [couriers.TrackingUpdate(status="in_transit", external_id="BK-77", note="Picked up")]


class CourierSeamTests(ReturnsTestCase):
    def setUp(self):
        super().setUp()
        self.fake = FakeCourier()
        couriers.load()
        couriers.register(self.fake)
        self.addCleanup(couriers._REGISTRY.pop, "fastship", None)

    def use_fake(self):
        Setting.objects.create(scope="country", country_id="ZA", key="returns.courier", value="fastship")

    def approved(self):
        order = self.delivered((self.bb, 1))
        req = self.request(order, [self.item(order, self.bb)])
        self.assertEqual(self.seller_post(req, "approve").status_code, 200)
        return ReturnRequest.objects.get(pk=req.pk)

    def test_manual_is_the_default_courier(self):
        req = self.approved()
        ship = req.shipments.get()
        self.assertEqual((ship.courier, ship.status, ship.tracking_number), ("manual", "pending", ""))

    def test_approval_books_a_pickup_with_the_markets_courier(self):
        self.use_fake()
        ship = self.approved().shipments.get()
        self.assertEqual((ship.courier, ship.status, ship.tracking_number, ship.external_id), ("fastship", "booked", "FS-1001", "BK-77"))
        self.assertEqual((ship.label_url, ship.tracking_url), ("https://fastship.example/label.pdf", "https://fastship.example/FS-1001"))
        self.assertEqual((ship.raw["pickup"], ship.raw["to"]), ("Cape Town", "Huda Beauty"))
        self.assertEqual(ship.dropoff_address["name"], "Huda Beauty")
        self.assertIsNotNone(ship.booked_at)
        shown = self.get(f"{ME}/returns/{ship.request_id}", self.buyer_token).json()["shipment"]
        self.assertEqual((shown["label_url"], shown["tracking_number"]), ("https://fastship.example/label.pdf", "FS-1001"))

    def test_the_courier_is_chosen_per_market(self):
        self.use_fake()
        pk = make_country("PK")
        self.assertEqual(couriers.for_country(Country.objects.get(pk="ZA")).slug, "fastship")
        self.assertEqual(couriers.for_country(pk).slug, "manual")

    def test_an_unknown_courier_name_falls_back_to_manual(self):
        Setting.objects.create(scope="global", key="returns.courier", value="typo")
        self.assertEqual(couriers.for_country(Country.objects.get(pk="ZA")).slug, "manual")

    def test_a_courier_that_fails_never_blocks_the_approval(self):
        self.use_fake()
        self.fake.fail = True
        with self.assertLogs("returns.services", level="ERROR"):
            req = self.approved()
        ship = req.shipments.get()
        self.assertEqual((req.status, ship.courier, ship.status), ("approved", "manual", "pending"))
        self.assertIn("courier is down", ship.raw["error"])

    def test_withdrawing_cancels_the_booking_with_the_courier(self):
        self.use_fake()
        req = self.approved()
        self.assertEqual(self.post(f"{ME}/returns/{req.pk}/cancel", None, self.buyer_token).status_code, 200)
        self.assertEqual(self.fake.cancelled, ["BK-77"])
        self.assertEqual(req.shipments.get().status, "cancelled")

    def test_a_webhook_moves_the_shipment_and_writes_the_timeline(self):
        self.use_fake()
        req = self.approved()
        res = self.client.post("/api/v1/couriers/fastship/webhook", data="{}", content_type="application/json", headers={"X-Signature": "ok"})
        self.assertEqual((res.status_code, res.json()["applied"]), (200, 1))
        self.assertEqual(req.shipments.get().status, "in_transit")
        self.assertIn("Picked up", list(req.events.values_list("note", flat=True)))

    def test_a_webhook_without_a_valid_signature_or_for_an_unknown_courier_is_a_404(self):
        self.use_fake()
        self.approved()
        post = lambda slug, **h: self.client.post(f"/api/v1/couriers/{slug}/webhook", data="{}", content_type="application/json", headers=h)       # noqa: E731
        self.assertEqual(post("fastship").status_code, 404)
        self.assertEqual(post("nobody").status_code, 404)
        self.assertEqual(post("manual").status_code, 404)

    def test_polling_updates_shipments_still_on_their_way(self):
        self.use_fake()
        req = self.approved()
        ReturnShipment.objects.filter(request=req).update(status="in_transit")
        self.assertEqual(services.sync_tracking(), 1)
        self.assertEqual(req.shipments.get().status, "delivered")
        self.assertEqual(services.sync_tracking(), 0)            # delivered ones are no longer polled

    def test_a_delivered_parcel_does_not_refund_by_itself(self):
        self.use_fake()
        req = self.approved()
        services.apply_update(req.shipments.get(), couriers.TrackingUpdate(status="delivered", external_id="BK-77"))
        self.assertEqual(ReturnRequest.objects.get(pk=req.pk).status, "approved")          # the seller still inspects it
        self.assertEqual(self.amount(self.store_a, T.REFUND), 0)

    def test_a_late_courier_message_cannot_reopen_a_finished_shipment(self):
        self.use_fake()
        req = self.approved()
        ship = req.shipments.get()
        services.apply_update(ship, couriers.TrackingUpdate(status="delivered", external_id="BK-77"))
        services.apply_update(ship, couriers.TrackingUpdate(status="in_transit", external_id="BK-77"))
        self.assertEqual(req.shipments.get().status, "delivered")

    def test_a_courier_needs_a_slug(self):
        with self.assertRaises(ValueError):
            couriers.register(couriers.Courier())


