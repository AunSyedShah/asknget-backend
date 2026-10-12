"""Money: what gets posted when, what each balance means, and that nothing is ever counted twice.

These drive the real flow - a buyer checks out, staff confirm the payment, sellers deliver, orders
get cancelled or returned - and then read the ledger, so a wrong hook shows up as a wrong number.
"""

import re
from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import override_settings
from django.utils import timezone

from config.models import CommissionRule, Setting
from core.testing import ApiTestCase, make_category, make_product, make_store, make_user
from ledger import services
from ledger.models import LedgerEntry, Payout
from orders.models import Order, OrderItem, Voucher
from team.models import StoreMember

CART = "/api/v1/cart"
CHECKOUT = "/api/v1/checkout"
TX = "/api/v1/seller/transactions"
ADMIN_STATUS = "/api/v1/admin/orders/{}/status"
CONTACT = {"first_name": "Olivia", "last_name": "Martin", "email": "olivia@example.com", "phone": "+27 82 123 4567"}
ADDRESS = {"address": "123 Main Street", "city": "Cape Town", "postal_code": "6001"}
T = LedgerEntry.Type


class LedgerTestCase(ApiTestCase):
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
        cls.staff = make_user(is_staff=True)

    def setUp(self):
        self.buyer_token = self.token_for(self.buyer)
        self.staff_token = self.token_for(self.staff)
        self.token_a, self.token_b = self.token_for(self.owner_a), self.token_for(self.owner_b)

    # -- the real flow ------------------------------------------------------------------
    def order(self, *lines, voucher=None) -> Order:
        for product, qty in lines:
            res = self.post(f"{CART}/items", {"variant_id": str(product.variants.first().id), "qty": qty}, self.buyer_token)
            self.assertEqual(res.status_code, 200, res.content)
        if voucher:
            self.assertEqual(self.post(f"{CART}/voucher", {"code": voucher}, self.buyer_token).status_code, 200)
        res = self.post(CHECKOUT, {"contact": CONTACT, "address": ADDRESS, "payment_method_type": "card"}, self.buyer_token)
        self.assertEqual(res.status_code, 201, res.content)
        return Order.objects.get(order_number=res.json()["order"]["order_number"])

    def paid(self, *lines, voucher=None) -> Order:
        order = self.order(*lines, voucher=voucher)
        self.staff_status(order, "confirmed")
        return order

    def staff_status(self, order, status):
        res = self.post(ADMIN_STATUS.format(order.pk), {"status": status}, self.staff_token)
        self.assertEqual(res.status_code, 200, res.content)

    def seller_moves(self, order, token, *statuses):
        for status in statuses:
            res = self.post(f"/api/v1/seller/orders/{order.pk}/status", {"status": status}, token)
            self.assertEqual(res.status_code, 200, res.content)

    def deliver(self, order, token):
        self.seller_moves(order, token, "processing", "shipped", "delivered")

    def entries(self, store, **filters):
        return LedgerEntry.objects.filter(store=store, **filters)

    def amount(self, store, type_, order=None):
        qs = self.entries(store, type=type_)
        return qs.filter(order=order).get().amount_cents if order else sum(e.amount_cents for e in qs)

    def pass_time(self, days=0):
        """Let the hold elapse: pretend every held amount became available `days` ago."""
        LedgerEntry.objects.filter(available_at__isnull=False).update(available_at=timezone.now() - timedelta(days=days, seconds=1))

    def balances(self, store):
        return services.balances(store)


# --------------------------------------------------------------------------- #
# Posting
# --------------------------------------------------------------------------- #


class PostingTests(LedgerTestCase):
    def test_nothing_is_posted_until_the_payment_is_confirmed(self):
        order = self.order((self.bb, 1))
        self.assertEqual(LedgerEntry.objects.count(), 0)
        self.staff_status(order, "confirmed")
        self.assertEqual(sorted(self.entries(self.store_a).values_list("type", flat=True)), ["fee", "sale"])

    def test_a_sale_is_the_items_plus_the_sellers_delivery_and_the_fee_is_the_commission(self):
        order = self.paid((self.bb, 1), (self.lip, 2))            # 10000 + 2 x 5000 = 20000, delivery 1099, 5 % commission
        self.assertEqual(self.amount(self.store_a, T.SALE, order), 20000 + 1099)
        self.assertEqual(self.amount(self.store_a, T.FEE, order), -1000)
        sale = self.entries(self.store_a, type=T.SALE).get()
        self.assertEqual(sale.meta, {"lines_cents": 20000, "discount_cents": 0, "delivery_cents": 1099})
        self.assertEqual(sale.currency, order.currency)
        self.assertEqual(sale.description, f"Order {order.order_number}")

    def test_what_the_stores_earn_adds_up_exactly_to_what_the_buyer_paid(self):
        Voucher.objects.create(code="SAVE20", type="percent", value=20)
        order = self.paid((self.bb, 1), (self.watch, 1), voucher="SAVE20")
        a, b = self.amount(self.store_a, T.SALE, order), self.amount(self.store_b, T.SALE, order)
        self.assertEqual(a + b, order.total_cents)                # items - voucher + both deliveries, to the cent
        self.assertEqual(order.discount_cents, 6000)
        self.assertEqual(self.entries(self.store_a, type=T.SALE).get().meta["discount_cents"], 2000)   # pro-rata on subtotal

    def test_the_voucher_reduces_the_commission_proportionally(self):
        Voucher.objects.create(code="SAVE20", type="percent", value=20)
        order = self.paid((self.bb, 1), voucher="SAVE20")         # 10000 - 2000 = 8000 net; 5 % = 400
        self.assertEqual(self.amount(self.store_a, T.FEE, order), -400)

    def test_references_are_dated_unique_and_sequential(self):
        self.paid((self.bb, 1), (self.watch, 1))
        refs = list(LedgerEntry.objects.order_by("created_at", "reference").values_list("reference", flat=True))
        self.assertEqual(len(refs), 4)
        self.assertTrue(all(re.fullmatch(rf"TXN-{timezone.localdate():%Y%m%d}-\d+", r) for r in refs), refs)
        numbers = sorted(int(r.rsplit("-", 1)[1]) for r in refs)
        self.assertEqual(numbers, list(range(numbers[0], numbers[0] + 4)))        # no gaps

    def test_confirming_twice_cannot_post_twice(self):
        order = self.paid((self.bb, 1))
        before = LedgerEntry.objects.count()
        services.post_sales(order)
        services.post_sales(order)
        self.assertEqual(LedgerEntry.objects.count(), before)

    def test_the_database_refuses_a_second_posting_of_the_same_kind(self):
        order = self.paid((self.bb, 1))
        with self.assertRaises(IntegrityError), transaction.atomic():
            LedgerEntry.objects.create(store=self.store_a, order=order, type=T.SALE, amount_cents=1, currency="ZAR", reference="TXN-X-1")

    def test_signs_are_enforced_by_type(self):
        for type_, amount in ((T.SALE, -5), (T.REFUND, 5), (T.PAYOUT, 5), (T.FEE, 5), (T.ADJUSTMENT, 0)):
            with self.assertRaises(IntegrityError, msg=f"{type_} {amount}"), transaction.atomic():
                LedgerEntry.objects.create(store=self.store_a, type=type_, amount_cents=amount, currency="ZAR", reference=f"TXN-S-{type_}-{amount}")

    def test_an_order_never_confirmed_leaves_no_trace_even_when_cancelled(self):
        order = self.order((self.bb, 1))
        self.assertEqual(self.post(f"/api/v1/me/orders/{order.pk}/cancel", {"reason": "changed my mind"}, self.buyer_token).status_code, 200)
        self.assertEqual(LedgerEntry.objects.count(), 0)


class CommissionPostingTests(LedgerTestCase):
    def test_the_rate_in_force_is_recorded_on_the_fee(self):
        rule = CommissionRule.objects.get(scope="global")
        fee = self.entries(self.store_a, type=T.FEE, order=self.paid((self.bb, 1))).get()
        self.assertEqual(fee.meta["effective_bps"], 500)
        self.assertEqual(fee.meta["lines"], [{"category_id": str(self.beauty.pk), "line_cents": 10000, "bps": 500, "rule_id": str(rule.pk)}])

    def test_changing_the_rate_later_never_rewrites_a_posted_fee(self):
        order = self.paid((self.bb, 1))
        CommissionRule.objects.filter(scope="global").update(rate_bps=2000)
        self.assertEqual(self.amount(self.store_a, T.FEE, order), -500)               # still 5 %
        self.assertEqual(self.amount(self.store_a, T.FEE, self.paid((self.bb, 1))), -2000)   # the next order pays 20 %

    def test_each_line_uses_its_own_categorys_rate(self):
        CommissionRule.objects.create(scope="category", category=self.beauty, rate_bps=1000)
        CommissionRule.objects.create(scope="category", category=self.tech, rate_bps=200)
        order = self.paid((self.bb, 1), (self.cable, 5))           # 10000 @ 10 % + 10000 @ 2 %
        self.assertEqual(self.amount(self.store_a, T.FEE, order), -(1000 + 200))

    def test_a_store_specific_deal_wins(self):
        CommissionRule.objects.create(scope="store", store=self.store_a, rate_bps=250)
        self.assertEqual(self.amount(self.store_a, T.FEE, self.paid((self.bb, 1))), -250)
        self.assertEqual(self.amount(self.store_b, T.FEE, self.paid((self.watch, 1))), -1000)        # the other store stays at 5 %

    def test_with_no_commission_at_all_there_is_a_sale_and_no_fee_entry(self):
        CommissionRule.objects.all().delete()
        order = self.paid((self.bb, 1))
        self.assertEqual(self.entries(self.store_a, order=order).values_list("type", flat=True)[0], "sale")
        self.assertFalse(self.entries(self.store_a, type=T.FEE).exists())


# --------------------------------------------------------------------------- #
# The hold: when money becomes the seller's to withdraw
# --------------------------------------------------------------------------- #


class HoldTests(LedgerTestCase):
    def test_a_paid_order_is_held_not_available(self):
        self.paid((self.bb, 1))
        bal = self.balances(self.store_a)
        self.assertEqual((bal["available_cents"], bal["held_cents"]), (0, 10000 + 1099 - 500))

    def test_delivery_starts_the_return_window_hold(self):
        order = self.paid((self.bb, 1))
        self.deliver(order, self.token_a)
        sale = self.entries(self.store_a, type=T.SALE).get()
        self.assertAlmostEqual((sale.available_at - timezone.now()).total_seconds(), order.return_window_days * 86400, delta=60)
        self.assertEqual(sale.meta["hold"]["mode"], "return_window")
        self.assertEqual(self.balances(self.store_a)["available_cents"], 0 if order.return_window_days else 10599)

    def test_once_the_window_has_passed_the_money_is_available(self):
        self.deliver(self.paid((self.bb, 1)), self.token_a)
        self.pass_time()
        bal = self.balances(self.store_a)
        self.assertEqual((bal["available_cents"], bal["held_cents"]), (10599, 0))

    def test_the_admin_can_choose_fixed_days_per_store(self):
        Setting.objects.create(key="payouts.hold_mode", value="fixed_days")
        Setting.objects.create(key="payouts.hold_days", scope="store", store=self.store_a, value=3)
        order_a, order_b = self.paid((self.bb, 1)), self.paid((self.watch, 1))
        self.deliver(order_a, self.token_a)
        self.deliver(order_b, self.token_b)
        a = self.entries(self.store_a, type=T.SALE).get().available_at
        b = self.entries(self.store_b, type=T.SALE).get().available_at
        self.assertAlmostEqual((a - timezone.now()).total_seconds(), 3 * 86400, delta=60)       # the store override
        self.assertAlmostEqual((b - timezone.now()).total_seconds(), 7 * 86400, delta=60)       # the default days

    def test_the_admin_can_switch_the_hold_off(self):
        Setting.objects.create(key="payouts.hold_mode", value="none")
        self.paid((self.bb, 1))
        bal = self.balances(self.store_a)
        self.assertEqual((bal["available_cents"], bal["held_cents"]), (10599, 0))                # available at once, before delivery

    def test_the_hold_in_force_at_delivery_is_the_one_that_applies(self):
        order = self.paid((self.bb, 1))
        Setting.objects.create(key="payouts.hold_mode", value="fixed_days")
        Setting.objects.create(key="payouts.hold_days", value=1)
        self.deliver(order, self.token_a)
        sale = self.entries(self.store_a, type=T.SALE).get()
        self.assertAlmostEqual((sale.available_at - timezone.now()).total_seconds(), 86400, delta=60)

    def test_delivering_one_store_does_not_release_the_other(self):
        order = self.paid((self.bb, 1), (self.watch, 1))
        self.deliver(order, self.token_a)
        self.assertIsNotNone(self.entries(self.store_a, type=T.SALE).get().available_at)
        self.assertIsNone(self.entries(self.store_b, type=T.SALE).get().available_at)

    def test_staff_delivering_the_whole_order_releases_every_store(self):
        order = self.paid((self.bb, 1), (self.watch, 1))
        for status in ("processing", "shipped", "out_for_delivery", "delivered"):
            self.staff_status(order, status)
        self.assertEqual(LedgerEntry.objects.filter(available_at__isnull=True).count(), 0)


# --------------------------------------------------------------------------- #
# Money going back
# --------------------------------------------------------------------------- #


class RefundTests(LedgerTestCase):
    def test_a_buyer_cancelling_a_paid_order_nets_the_money_out_of_every_balance(self):
        order = self.paid((self.bb, 1))
        res = self.post(f"/api/v1/me/orders/{order.pk}/cancel", {"reason": "changed my mind"}, self.buyer_token)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(self.amount(self.store_a, T.REFUND, order), -11099)
        self.assertEqual(self.amount(self.store_a, T.FEE_REFUND, order), 500)                  # the commission goes back with it
        bal = self.balances(self.store_a)
        self.assertEqual((bal["available_cents"], bal["held_cents"]), (0, 0))

    def test_a_refund_happens_once(self):
        order = self.paid((self.bb, 1))
        self.post(f"/api/v1/me/orders/{order.pk}/cancel", {"reason": "x"}, self.buyer_token)
        count = LedgerEntry.objects.count()
        services.refund(order)
        services.refund(order)
        self.assertEqual(LedgerEntry.objects.count(), count)

    def test_a_seller_cancelling_their_lines_refunds_only_their_store(self):
        order = self.paid((self.bb, 1), (self.watch, 1))
        self.seller_moves(order, self.token_a, "cancelled")
        self.assertTrue(self.entries(self.store_a, type=T.REFUND).exists())
        self.assertFalse(self.entries(self.store_b, type=T.REFUND).exists())
        self.assertEqual(self.balances(self.store_b)["held_cents"], 20000 + 1099 - 1000)

    def test_returning_goods_after_the_money_was_available_takes_it_back(self):
        order = self.paid((self.bb, 1))
        self.deliver(order, self.token_a)
        self.pass_time()
        self.assertEqual(self.balances(self.store_a)["available_cents"], 10599)
        self.seller_moves(order, self.token_a, "returned")
        self.assertEqual(self.balances(self.store_a)["available_cents"], 0)
        self.assertEqual(self.amount(self.store_a, T.REFUND, order), -11099)

    def test_a_return_inside_the_hold_never_lets_the_money_out(self):
        order = self.paid((self.bb, 1))
        self.deliver(order, self.token_a)                                  # the window is running
        self.seller_moves(order, self.token_a, "returned")
        self.pass_time(days=-1)                                            # even once the window would have ended
        LedgerEntry.objects.update(available_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.balances(self.store_a)["available_cents"], 0)

    def test_staff_refunding_a_delivered_order_refunds_every_store(self):
        order = self.paid((self.bb, 1), (self.watch, 1))
        for status in ("processing", "shipped", "out_for_delivery", "delivered", "refunded"):
            self.staff_status(order, status)
        self.assertEqual(LedgerEntry.objects.filter(type=T.REFUND).count(), 2)

    def test_the_four_entries_of_a_refunded_order_net_to_zero(self):
        order = self.paid((self.bb, 1))
        self.post(f"/api/v1/me/orders/{order.pk}/cancel", {"reason": "x"}, self.buyer_token)
        self.assertEqual(sum(e.amount_cents for e in self.entries(self.store_a)), 0)


# --------------------------------------------------------------------------- #
# Adjustments and payouts (staff)
# --------------------------------------------------------------------------- #


class StaffMoneyTests(LedgerTestCase):
    def fund(self, store_token_owner=None):
        """Put 10599 in store_a's available balance through the real flow."""
        order = self.paid((self.bb, 1))
        self.deliver(order, self.token_a)
        self.pass_time()

    def test_a_signed_adjustment_needs_a_reason_and_is_staff_only(self):
        url = "/api/v1/admin/ledger/adjustments"
        body = {"store_id": str(self.store_a.pk), "amount_cents": 2500, "note": "Goodwill credit for a late delivery"}
        self.assertEqual(self.post(url, body, self.token_a).status_code, 403)                  # a seller cannot credit themselves
        res = self.post(url, body, self.staff_token)
        self.assertEqual((res.status_code, res.json()["type"], res.json()["amount_cents"]), (201, "adjustment", 2500))
        self.assertEqual(self.balances(self.store_a)["available_cents"], 2500)
        debit = self.post(url, {**body, "amount_cents": -1000, "note": "Chargeback"}, self.staff_token)
        self.assertEqual(debit.status_code, 201)
        self.assertEqual(self.balances(self.store_a)["available_cents"], 1500)
        self.assertEqual(self.post(url, {**body, "amount_cents": 0}, self.staff_token).status_code, 422)
        self.assertEqual(self.post(url, {**body, "note": "x"}, self.staff_token).status_code, 422)

    def test_a_payout_comes_out_of_the_available_balance_only(self):
        self.fund()
        url = "/api/v1/admin/payouts"
        too_much = self.post(url, {"store_id": str(self.store_a.pk), "amount_cents": 10600}, self.staff_token)
        self.assertEqual(too_much.status_code, 409)
        res = self.post(url, {"store_id": str(self.store_a.pk), "amount_cents": 6000, "note": "Weekly"}, self.staff_token)
        self.assertEqual(res.status_code, 201, res.content)
        self.assertTrue(re.fullmatch(r"PAY-\d{8}-\d+", res.json()["reference"]))
        bal = self.balances(self.store_a)
        self.assertEqual((bal["available_cents"], bal["pending_payout_cents"]), (4599, 6000))

    def test_completing_a_payout_clears_it_from_pending_and_failing_it_gives_the_money_back(self):
        self.fund()
        payout = services.create_payout(self.store_a, amount_cents=6000, note="", by=self.staff)
        self.assertEqual(self.patch(f"/api/v1/admin/payouts/{payout.pk}/status", {"status": "completed"}, self.staff_token).status_code, 200)
        bal = self.balances(self.store_a)
        self.assertEqual((bal["available_cents"], bal["pending_payout_cents"]), (4599, 0))      # paid out, no longer pending
        second = services.create_payout(self.store_a, amount_cents=4000, note="", by=self.staff)
        self.patch(f"/api/v1/admin/payouts/{second.pk}/status", {"status": "failed"}, self.staff_token)
        self.assertEqual(self.balances(self.store_a)["available_cents"], 4599)                  # the failed payout never happened
        self.assertEqual(self.patch(f"/api/v1/admin/payouts/{second.pk}/status", {"status": "completed"}, self.staff_token).status_code, 409)

    def test_the_minimum_payout_is_a_setting(self):
        self.fund()
        Setting.objects.create(key="payouts.minimum_cents", value=5000)
        res = self.post("/api/v1/admin/payouts", {"store_id": str(self.store_a.pk), "amount_cents": 4000}, self.staff_token)
        self.assertEqual(res.status_code, 422)
        self.assertEqual(self.post("/api/v1/admin/payouts", {"store_id": str(self.store_a.pk), "amount_cents": 5000}, self.staff_token).status_code, 201)

    def test_a_seller_cannot_touch_payouts(self):
        self.fund()
        self.assertEqual(self.post("/api/v1/admin/payouts", {"store_id": str(self.store_a.pk), "amount_cents": 100}, self.token_a).status_code, 403)
        payout = services.create_payout(self.store_a, amount_cents=100, note="", by=self.staff)
        self.assertEqual(self.patch(f"/api/v1/admin/payouts/{payout.pk}/status", {"status": "completed"}, self.token_a).status_code, 403)

    def test_staff_can_see_what_a_payout_could_be(self):
        self.fund()
        body = self.get(f"/api/v1/admin/ledger/stores/{self.store_a.pk}", self.staff_token).json()
        self.assertEqual((body["available_cents"], body["held_cents"]), (10599, 0))
        listed = self.get("/api/v1/admin/payouts", self.staff_token).json()
        self.assertEqual(listed["total"], 0)


# --------------------------------------------------------------------------- #
# What the seller sees
# --------------------------------------------------------------------------- #


class SellerViewTests(LedgerTestCase):
    def setUp(self):
        super().setUp()
        order = self.paid((self.bb, 1), (self.lip, 2))                 # sale 21099, fee -1000
        self.deliver(order, self.token_a)
        self.pass_time()
        self.refunded = self.paid((self.bb, 1))
        self.post(f"/api/v1/me/orders/{self.refunded.pk}/cancel", {"reason": "x"}, self.buyer_token)
        services.adjust(self.store_a, amount_cents=2000, note="Goodwill", by=self.staff)
        services.create_payout(self.store_a, amount_cents=5000, note="Weekly", by=self.staff)

    def test_overview_cards_and_summary(self):
        body = self.get(f"{TX}/overview", self.token_a, period="month").json()
        self.assertEqual(body["summary"], {
            "sales_revenue_cents": 21099 + 11099, "platform_fees_cents": -1000 - 500 + 500, "refunds_cents": -11099,
            "adjustments_cents": 2000, "net_payout_cents": 21099 + 11099 - 1000 - 11099 + 2000,
        })
        self.assertEqual(body["total_sales"]["value"], 21099 + 11099)
        self.assertEqual(body["total_fees"]["value"], 1000)                           # commission taken, net of what came back
        self.assertEqual(body["pending_payout"]["value_cents"], 5000)
        self.assertEqual(body["available_balance_cents"], 21099 - 1000 + 2000 - 5000)
        self.assertEqual(len(body["recent"]), 5)
        self.assertEqual(body["currency"], "ZAR")

    def test_tabs_count_each_kind_and_a_returned_fee_counts_as_a_fee(self):
        self.assertEqual(self.get(f"{TX}/tabs", self.token_a).json(), {"sale": 2, "payout": 1, "fee": 3, "refund": 1, "adjustment": 1, "all": 8})

    def test_the_table_filters(self):
        def refs(**params):
            return [r["type"] for r in self.get(TX, self.token_a, **params).json()["items"]]

        self.assertEqual(sorted(refs(tab="payout")), ["payout"])
        self.assertEqual(sorted(refs(tab="fee")), ["fee", "fee", "fee"])
        self.assertEqual(refs(status="refunded"), ["refund"])
        self.assertEqual(refs(status="processing"), ["payout"])
        self.assertEqual(sorted(refs(amount_min=20000)), ["sale"])                  # by size, whichever way the money moved
        self.assertEqual(len(refs(q=self.refunded.order_number)), 4)               # sale, fee, refund, fee refund of that order
        self.assertEqual(len(refs(q="Olivia")), 6)                                 # by customer name
        self.assertEqual(refs(date_from="2999-01-01"), [])

    def test_a_pending_sale_reads_pending_and_a_released_one_reads_completed(self):
        pending = self.paid((self.watch, 1))
        self.assertEqual(self.get(TX, self.token_b, tab="sale").json()["items"][0]["status"], "pending")
        self.deliver(pending, self.token_b)
        self.pass_time()
        self.assertEqual(self.get(TX, self.token_b, tab="sale").json()["items"][0]["status"], "completed")

    def test_the_detail_breaks_a_sale_down(self):
        sale = self.entries(self.store_a, type=T.SALE).order_by("created_at").first()
        body = self.get(f"{TX}/{sale.reference}", self.token_a).json()
        self.assertEqual({l["label"]: l["amount_cents"] for l in body["breakdown"]},
                         {"Items": 20000, "Discount": 0, "Delivery": 1099, "Platform fee": -1000, "Net amount": 21099 - 1000})
        self.assertEqual(body["order"]["customer"], "Olivia Martin")
        self.assertEqual(body["payment"]["method"], "card")
        self.assertEqual(body["hold"]["mode"], "return_window")

    def test_another_stores_transaction_is_not_found(self):
        reference = self.entries(self.store_a, type=T.SALE).first().reference
        self.assertEqual(self.get(f"{TX}/{reference}", self.token_b).status_code, 404)
        self.assertEqual(self.get(f"{TX}/nope", self.token_a).status_code, 404)

    def test_export_follows_the_filters(self):
        res = self.get(f"{TX}/export.csv", self.token_a, tab="refund")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Content-Type"], "text/csv")
        lines = res.content.decode().strip().splitlines()
        self.assertEqual(len(lines), 2)                                            # header + the one refund
        self.assertIn("-110.99", lines[1])

    def test_it_needs_the_transactions_section(self):
        user = make_user(full_name="Finance Fiona")
        member = StoreMember.objects.create(store=self.store_a, user=user, email=user.email, permissions={"orders": "manage"},
                                            status="active", joined_at=timezone.now())
        token = self.token_for(user)
        self.assertEqual(self.get(f"{TX}/overview", token).json()["detail"], "permission_denied")
        member.permissions = {"transactions": "view"}
        member.save()
        self.assertEqual(self.get(f"{TX}/overview", token).status_code, 200)
        self.assertEqual(self.get(TX, token).status_code, 200)


# --------------------------------------------------------------------------- #
# Backfill and admin
# --------------------------------------------------------------------------- #


class BackfillTests(LedgerTestCase):
    def legacy_order(self, status="confirmed"):
        """An order paid before the ledger existed: written straight to the tables, no hooks."""
        order = Order.objects.create(
            user=self.buyer, status=status, order_number=f"OLD{Order.objects.count() + 1:05d}", country_id=self.store_a.country_id,
            contact_first_name="Olivia", contact_last_name="Martin", contact_email="olivia@example.com", contact_phone="0", shipping_address={},
            subtotal_cents=10000, shipping_cents=0, total_cents=10000, currency="ZAR", payment_method_type="card", payment_status="paid",
            return_window_days=7,
        )
        OrderItem.objects.create(order=order, store=self.store_a, product=self.bb, variant=self.bb.variants.first(), product_name="BB Cream",
                                 qty=1, unit_price_cents=10000, line_total_cents=10000, status="delivered" if status == "delivered" else "pending")
        return order

    def backfill(self, *args):
        out = StringIO()
        call_command("backfill_ledger", *args, stdout=out)
        return out.getvalue()

    def test_it_posts_what_is_missing_and_is_safe_to_run_twice(self):
        order = self.legacy_order()
        self.assertEqual(LedgerEntry.objects.count(), 0)
        self.assertIn("posted entries for 1 store order", self.backfill())
        self.assertEqual(sorted(LedgerEntry.objects.filter(order=order).values_list("type", flat=True)), ["fee", "sale"])
        self.assertIn("posted entries for 0 store order", self.backfill())
        self.assertEqual(LedgerEntry.objects.filter(order=order).count(), 2)

    def test_a_dry_run_changes_nothing(self):
        self.legacy_order()
        self.assertIn("would post entries for 1", self.backfill("--dry-run"))
        self.assertEqual(LedgerEntry.objects.count(), 0)

    def test_a_delivered_order_starts_its_hold(self):
        self.legacy_order(status="delivered")
        self.backfill()
        self.assertIsNotNone(LedgerEntry.objects.get(type=T.SALE).available_at)

    def test_unpaid_orders_are_left_alone(self):
        order = self.legacy_order()
        Order.objects.filter(pk=order.pk).update(payment_status="unpaid")
        self.backfill()
        self.assertEqual(LedgerEntry.objects.count(), 0)


@override_settings(STORAGES={
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},     # no collectstatic manifest in tests
})
class AdminTests(LedgerTestCase):
    def setUp(self):
        super().setUp()
        self.admin_user = make_user(is_staff=True, is_superuser=True)
        self.client.force_login(self.admin_user)

    def test_pages_render(self):
        for url in ("/admin/ledger/ledgerentry/", "/admin/ledger/ledgerentry/add/", "/admin/ledger/payout/", "/admin/ledger/payout/add/"):
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_an_adjustment_through_admin_goes_through_the_same_rules(self):
        res = self.client.post("/admin/ledger/ledgerentry/add/", {"store": self.store_a.pk, "amount_cents": 3000, "description": "Goodwill"})
        self.assertEqual(res.status_code, 302, getattr(res, "context", None) and res.context["adminform"].form.errors)
        entry = LedgerEntry.objects.get()
        self.assertEqual((entry.type, entry.amount_cents, entry.created_by_id), (T.ADJUSTMENT, 3000, self.admin_user.pk))
        self.assertRegex(entry.reference, r"TXN-\d{8}-\d+")
        self.assertEqual(self.client.post("/admin/ledger/ledgerentry/add/", {"store": self.store_a.pk, "amount_cents": 0, "description": "x"}).status_code, 200)

    def test_a_payout_through_admin_cannot_exceed_the_available_balance(self):
        too_much = self.client.post("/admin/ledger/payout/add/", {"store": self.store_a.pk, "amount_cents": 5000, "note": ""})
        self.assertEqual(too_much.status_code, 200)                                  # form re-shown with the reason
        self.assertEqual(Payout.objects.count(), 0)
        services.adjust(self.store_a, amount_cents=9000, note="Seed funds", by=self.admin_user)
        ok = self.client.post("/admin/ledger/payout/add/", {"store": self.store_a.pk, "amount_cents": 5000, "note": "Weekly"})
        self.assertEqual(ok.status_code, 302, getattr(ok, "context", None) and ok.context["adminform"].form.errors)
        self.assertEqual(Payout.objects.get().status, "processing")
        self.assertEqual(self.balances(self.store_a)["available_cents"], 4000)

    def test_entries_cannot_be_edited_or_deleted_in_admin(self):
        entry = services.adjust(self.store_a, amount_cents=100, note="x", by=self.admin_user)
        page = self.client.get(f"/admin/ledger/ledgerentry/{entry.pk}/change/")
        self.assertNotContains(page, 'name="_save"')                                  # no Save button: read-only
        self.assertEqual(self.client.post(f"/admin/ledger/ledgerentry/{entry.pk}/delete/", {"post": "yes"}).status_code, 403)

    def test_payout_actions(self):
        services.adjust(self.store_a, amount_cents=9000, note="Seed funds", by=self.admin_user)
        payout = services.create_payout(self.store_a, amount_cents=5000, note="", by=self.admin_user)
        self.client.post("/admin/ledger/payout/", {"action": "mark_completed", "_selected_action": [payout.pk]})
        payout.refresh_from_db()
        self.assertEqual(payout.status, "completed")
