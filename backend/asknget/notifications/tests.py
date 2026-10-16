"""Notifications: the right events create them, each person sees only their share, read state is
personal, and the wording is data an admin controls."""

from datetime import timedelta

from django.utils import timezone

from config.models import Setting
from core.testing import ApiTestCase, make_category, make_product, make_store, make_user, make_staff
from ledger import services as ledger
from markets.models import Country
from notifications.models import NotificationRead, NotificationTemplate, Notification as StoreNotification
from orders.models import Order
from team.models import StoreMember

N = "/api/v1/seller/notifications"
CART, CHECKOUT = "/api/v1/cart", "/api/v1/checkout"
ADDRESS = {"address": "123 Main Street", "city": "Cape Town", "postal_code": "6001"}


class NotificationTestCase(ApiTestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner_a, cls.owner_b = make_user(is_seller=True), make_user(is_seller=True)
        cls.store_a = make_store(owner=cls.owner_a, name="Huda Beauty")
        cls.store_b = make_store(owner=cls.owner_b, name="Tech Hub")
        cls.beauty = make_category("Beauty")
        cls.bb = make_product(cls.store_a, cls.beauty, "BB Cream", 10000, None, stock=50)
        cls.watch = make_product(cls.store_b, cls.beauty, "Smart Watch", 20000, None, stock=50)
        cls.staff = make_staff()

    def setUp(self):
        self.token_a, self.token_b = self.token_for(self.owner_a), self.token_for(self.owner_b)
        self.staff_token = self.token_for(self.staff)

    def buyer(self, name="Olivia Martin"):
        user = make_user(full_name=name)
        return user, self.token_for(user)

    def order(self, token, *products, name="Olivia", last="Martin", email=None):
        for product in products:
            res = self.post(f"{CART}/items", {"variant_id": str(product.variants.first().id), "qty": 1}, token)
            self.assertEqual(res.status_code, 200, res.content)
        contact = {"first_name": name, "last_name": last, "email": email or "olivia@example.com", "phone": "+27 82 123 4567"}
        res = self.post(CHECKOUT, {"contact": contact, "address": ADDRESS, "payment_method_type": "card"}, token)
        self.assertEqual(res.status_code, 201, res.content)
        return Order.objects.get(order_number=res.json()["order"]["order_number"])

    def confirm(self, order):
        res = self.post(f"/api/v1/admin/orders/{order.pk}/status", {"status": "confirmed"}, self.staff_token)
        self.assertEqual(res.status_code, 200, res.content)

    def kinds(self, store):
        return sorted(StoreNotification.objects.filter(store=store).values_list("kind", flat=True))

    def feed(self, token, **params):
        res = self.get(N, token, **params)
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()["items"]

    def join(self, perms, store=None):
        user = make_user(full_name="Team Mate")
        StoreMember.objects.create(store=store or self.store_a, user=user, email=user.email, permissions=perms, status="active", joined_at=timezone.now())
        return user, self.token_for(user)


class EmitterTests(NotificationTestCase):
    def test_an_unpaid_order_tells_no_seller(self):
        _, token = self.buyer()
        self.order(token, self.bb)
        self.assertEqual(StoreNotification.objects.filter(audience="seller").count(), 0)      # the customer is told (sprint 9)

    def test_a_paid_order_reaches_every_store_in_it(self):
        _, token = self.buyer()
        order = self.order(token, self.bb, self.watch)
        self.confirm(order)
        for store in (self.store_a, self.store_b):
            self.assertEqual(self.kinds(store), ["new_customer", "order_received", "payment_received"])
        received = StoreNotification.objects.get(store=self.store_a, kind="order_received")
        self.assertEqual((received.category, received.link_type, received.link_id), ("orders", "order", str(order.pk)))

    def test_only_a_first_order_makes_a_new_customer(self):
        user, token = self.buyer()
        first = self.order(token, self.bb)
        self.confirm(first)
        second = self.order(token, self.bb)
        self.confirm(second)
        self.assertEqual(StoreNotification.objects.filter(store=self.store_a, kind="new_customer").count(), 1)
        self.assertEqual(StoreNotification.objects.filter(store=self.store_a, kind="order_received").count(), 2)
        # the same person is new to a store they have not bought from before
        third = self.order(token, self.watch)
        self.confirm(third)
        self.assertEqual(StoreNotification.objects.filter(store=self.store_b, kind="new_customer").count(), 1)

    def test_a_cancellation_is_attributed_to_who_did_it(self):
        _, token = self.buyer()
        by_customer = self.order(token, self.bb)
        self.confirm(by_customer)
        self.post(f"/api/v1/me/orders/{by_customer.pk}/cancel", {"reason": "x"}, token)
        by_staff = self.order(token, self.bb)
        self.confirm(by_staff)
        self.post(f"/api/v1/admin/orders/{by_staff.pk}/status", {"status": "cancelled"}, self.staff_token)
        self.assertEqual(sorted(StoreNotification.objects.filter(store=self.store_a, kind__startswith="order_cancelled").values_list("kind", flat=True)),
                         ["order_cancelled_customer", "order_cancelled_staff"])

    def test_cancelling_an_order_the_seller_never_saw_or_cancelling_your_own_lines_says_nothing(self):
        _, token = self.buyer()
        unpaid = self.order(token, self.bb)
        self.post(f"/api/v1/me/orders/{unpaid.pk}/cancel", {"reason": "x"}, token)
        paid = self.order(token, self.bb)
        self.confirm(paid)
        self.post(f"/api/v1/seller/orders/{paid.pk}/status", {"status": "cancelled"}, self.token_a)
        self.assertFalse(StoreNotification.objects.filter(kind__startswith="order_cancelled").exists())

    def test_a_payout_notifies_only_when_completed(self):
        ledger.adjust(self.store_a, amount_cents=9000, note="Seed funds", by=self.staff)
        failed = ledger.create_payout(self.store_a, amount_cents=1000, note="", by=self.staff)
        done = ledger.create_payout(self.store_a, amount_cents=2000, note="", by=self.staff)
        self.patch(f"/api/v1/admin/payouts/{failed.pk}/status", {"status": "failed"}, self.staff_token)
        self.assertEqual(self.kinds(self.store_a), [])
        self.patch(f"/api/v1/admin/payouts/{done.pk}/status", {"status": "completed"}, self.staff_token)
        note = StoreNotification.objects.get(kind="payout_processed")
        self.assertEqual((note.category, note.params["amount_cents"]), ("payments", 2000))

    def test_staff_decisions_reach_the_seller_as_system_messages(self):
        pending = make_product(self.store_a, self.beauty, "Serum", 500, None, status="pending")
        self.post(f"/api/v1/admin/products/{pending.pk}/approve", {}, self.staff_token)
        rejected = make_product(self.store_a, self.beauty, "Toner", 500, None, status="pending")
        self.post(f"/api/v1/admin/products/{rejected.pk}/reject", {"reason": "Blurry photos"}, self.staff_token)
        self.post(f"/api/v1/admin/stores/{self.store_a.pk}/plan", {"is_premium": True}, self.staff_token)
        self.post(f"/api/v1/admin/stores/{self.store_a.pk}/plan", {"is_premium": True}, self.staff_token)       # no change: no message
        self.assertEqual(self.kinds(self.store_a), ["plan_changed", "product_approved", "product_rejected"])
        texts = {n["kind"]: n["body"] for n in self.feed(self.token_a)}
        self.assertEqual(texts["product_rejected"], "Toner was not approved: Blurry photos")
        self.assertEqual(texts["plan_changed"], "Your store is now on the premium plan.")


class StockAlertTests(NotificationTestCase):
    def set_stock(self, qty, product=None):
        variant = (product or self.bb).variants.first()
        res = self.post(f"/api/v1/seller/inventory/{variant.pk}/adjust", {"set": qty}, self.token_a)
        self.assertEqual(res.status_code, 200, res.content)

    def test_it_fires_once_per_crossing_not_on_every_sale_below_the_line(self):
        self.set_stock(9)                                           # 50 -> 9: crosses into low (threshold 10)
        self.set_stock(8)
        self.set_stock(5)
        self.assertEqual(self.kinds(self.store_a), ["low_stock"])
        self.set_stock(0)                                           # low -> out: a different, worse alert
        self.assertEqual(self.kinds(self.store_a), ["low_stock", "out_of_stock"])

    def test_restocking_then_running_down_again_alerts_again(self):
        self.set_stock(3)
        self.set_stock(50)
        self.set_stock(4)
        self.assertEqual(self.kinds(self.store_a).count("low_stock"), 2)

    def test_the_message_names_the_product_and_the_quantity(self):
        self.set_stock(7)
        note = self.feed(self.token_a)[0]
        self.assertEqual((note["title"], note["body"], note["link_type"]), ("Low Stock Alert", "BB Cream has only 7 units remaining.", "product"))

    def test_a_draft_or_unpublished_product_never_alerts(self):
        draft = make_product(self.store_a, self.beauty, "Draft", 500, None, status="draft", stock=0)
        self.set_stock(3, draft)
        self.assertEqual(self.kinds(self.store_a), [])

    def test_a_sale_that_runs_stock_down_alerts_too(self):
        self.bb.variants.update(stock_qty=11)
        _, token = self.buyer()
        self.confirm(self.order(token, self.bb))                    # checkout takes one: 11 -> 10, which is "low" (<= 10)
        self.assertIn("low_stock", self.kinds(self.store_a))


class WordingTests(NotificationTestCase):
    def one_order(self):
        _, token = self.buyer()
        self.confirm(self.order(token, self.bb))

    def test_the_wording_matches_the_design(self):
        self.one_order()
        titles = {n["kind"]: (n["title"], n["body"]) for n in self.feed(self.token_a)}
        self.assertEqual(titles["order_received"][0], "New Order Received")
        self.assertRegex(titles["order_received"][1], r"^Order #ORD-\d+ has been placed by Olivia Martin\.$")
        self.assertEqual(titles["payment_received"][0], "Payment Received")
        self.assertEqual(titles["new_customer"][1], "Olivia Martin placed their first order with your store.")

    def test_an_admin_edit_rewords_notifications_that_already_exist(self):
        self.one_order()
        NotificationTemplate.objects.filter(kind="order_received").update(title={"en": "Ka-ching!"}, body={"en": "{customer} just bought something."})
        note = next(n for n in self.feed(self.token_a) if n["kind"] == "order_received")
        self.assertEqual((note["title"], note["body"]), ("Ka-ching!", "Olivia Martin just bought something."))

    def test_a_kind_can_be_switched_off(self):
        NotificationTemplate.objects.filter(kind="payment_received").update(is_active=False)
        self.one_order()
        self.assertEqual(self.kinds(self.store_a), ["new_customer", "order_received"])

    def test_the_reader_gets_their_own_language_with_english_as_the_fallback(self):
        Country.objects.filter(pk=self.store_a.country_id).update(languages=["en", "af"])
        NotificationTemplate.objects.filter(kind="order_received").update(
            title={"en": "New Order Received", "af": "Nuwe bestelling ontvang"}, body={"en": "Order #{order} by {customer}.", "af": "Bestelling #{order} deur {customer}."},
        )
        self.one_order()
        english = next(n for n in self.feed(self.token_a) if n["kind"] == "order_received")
        afrikaans = next(n for n in self.client.get(N, headers={"Authorization": f"Bearer {self.token_a}", "X-Language": "af"}).json()["items"]
                         if n["kind"] == "order_received")
        self.assertEqual(english["title"], "New Order Received")
        self.assertEqual((afrikaans["title"], afrikaans["body"].startswith("Bestelling #")), ("Nuwe bestelling ontvang", True))
        # a kind with no Afrikaans text reads in English rather than blank
        payment = next(n for n in self.client.get(N, headers={"Authorization": f"Bearer {self.token_a}", "X-Language": "af"}).json()["items"]
                       if n["kind"] == "payment_received")
        self.assertEqual(payment["title"], "Payment Received")

    def test_a_missing_template_row_falls_back_to_the_built_in_wording(self):
        NotificationTemplate.objects.all().delete()
        self.one_order()
        self.assertEqual(next(n for n in self.feed(self.token_a) if n["kind"] == "order_received")["title"], "New Order Received")

    def test_a_broken_template_cannot_break_the_list(self):
        self.one_order()
        NotificationTemplate.objects.filter(kind="order_received").update(body={"en": "Order {order has {broken"})
        note = next(n for n in self.feed(self.token_a) if n["kind"] == "order_received")
        self.assertEqual(note["body"], "Order {order has {broken")

    def test_the_admin_form_rejects_bad_wording(self):
        from django.core.exceptions import ValidationError

        for bad in ({}, {"af": "Alleen Afrikaans"}, {"en": "ok", "xx": "unknown language"}, {"en": ""}, "plain text"):
            with self.assertRaises(ValidationError, msg=repr(bad)):
                NotificationTemplate(kind="x", category="system", title=bad, body={"en": "ok"}).full_clean(exclude=["kind"])

    def test_an_amount_is_formatted_from_cents_and_currency(self):
        ledger.adjust(self.store_a, amount_cents=900000, note="Seed funds", by=self.staff)
        payout = ledger.create_payout(self.store_a, amount_cents=500000, note="", by=self.staff)
        self.patch(f"/api/v1/admin/payouts/{payout.pk}/status", {"status": "completed"}, self.staff_token)
        body = next(n for n in self.feed(self.token_a) if n["kind"] == "payout_processed")["body"]
        self.assertEqual(body, f"Your payout of {payout.currency} 5,000.00 has been processed successfully.")


class VisibilityAndReadStateTests(NotificationTestCase):
    def setUp(self):
        super().setUp()
        _, token = self.buyer()
        self.confirm(self.order(token, self.bb))                                # orders x1, payments x1, customers x1
        self.bb.variants.update(stock_qty=50)
        self.post(f"/api/v1/seller/inventory/{self.bb.variants.first().pk}/adjust", {"set": 4}, self.token_a)   # inventory x1
        self.post(f"/api/v1/admin/stores/{self.store_a.pk}/plan", {"is_premium": True}, self.staff_token)        # system x1

    def test_the_owner_sees_everything_newest_first(self):
        items = self.feed(self.token_a)
        self.assertEqual(sorted({n["category"] for n in items}), ["customers", "inventory", "orders", "payments", "system"])
        self.assertEqual(items, sorted(items, key=lambda n: n["created_at"], reverse=True))
        self.assertTrue(all(n["group"] == "today" for n in items))

    def test_a_member_sees_only_the_categories_of_the_sections_they_can_view(self):
        _, stock_clerk = self.join({"inventory": "view"})
        _, finance = self.join({"transactions": "view"})
        _, nobody = self.join({})
        self.assertEqual(sorted({n["category"] for n in self.feed(stock_clerk)}), ["inventory", "system"])
        self.assertEqual(sorted({n["category"] for n in self.feed(finance)}), ["payments", "system"])
        self.assertEqual({n["category"] for n in self.feed(nobody)}, {"system"})         # system messages are for the whole team

    def test_tabs_filter_and_only_offer_what_is_visible(self):
        self.assertEqual({n["category"] for n in self.feed(self.token_a, tab="orders")}, {"orders"})
        _, stock_clerk = self.join({"inventory": "view"})
        self.assertEqual(self.feed(stock_clerk, tab="orders"), [])                       # asking for a tab you cannot see yields nothing
        tabs = self.get(f"{N}/summary", stock_clerk).json()["tabs"]
        self.assertEqual(sorted(tabs), ["all", "inventory", "system"])

    def test_the_summary_counts_unread_per_tab_and_in_total(self):
        body = self.get(f"{N}/summary", self.token_a).json()
        self.assertEqual(body["unread"], 5)                                              # one each: order, payment, customer, stock, plan
        self.assertEqual({k: v["unread"] for k, v in body["tabs"].items()}, {"orders": 1, "payments": 1, "inventory": 1, "customers": 1, "returns": 0, "system": 1, "all": 5})

    def test_reading_is_personal(self):
        user, member_token = self.join({"inventory": "view"})
        note = next(n for n in self.feed(self.token_a) if n["category"] == "inventory")
        self.assertEqual(self.post(f"{N}/{note['id']}/read", {}, self.token_a).status_code, 204)
        self.assertTrue(next(n for n in self.feed(self.token_a) if n["id"] == note["id"])["is_read"])
        self.assertFalse(next(n for n in self.feed(member_token) if n["id"] == note["id"])["is_read"])      # the team mate still has it unread
        self.assertEqual(self.get(f"{N}/summary", self.token_a).json()["tabs"]["inventory"]["unread"], 0)
        self.assertEqual(self.get(f"{N}/summary", member_token).json()["tabs"]["inventory"]["unread"], 1)

    def test_reading_twice_is_harmless(self):
        note = self.feed(self.token_a)[0]
        self.post(f"{N}/{note['id']}/read", {}, self.token_a)
        self.assertEqual(self.post(f"{N}/{note['id']}/read", {}, self.token_a).status_code, 204)
        self.assertEqual(NotificationRead.objects.filter(notification_id=note["id"]).count(), 1)

    def test_mark_all_as_read_can_be_limited_to_a_tab(self):
        self.assertEqual(self.post(f"{N}/read-all?tab=orders", {}, self.token_a).json(), {"marked": 1})
        self.assertEqual(self.get(f"{N}/summary", self.token_a).json()["unread"], 4)
        self.assertEqual(self.post(f"{N}/read-all", {}, self.token_a).json(), {"marked": 4})
        self.assertEqual(self.get(f"{N}/summary", self.token_a).json()["unread"], 0)
        self.assertEqual(self.post(f"{N}/read-all", {}, self.token_a).json(), {"marked": 0})

    def test_you_cannot_read_what_you_cannot_see_or_what_belongs_to_another_store(self):
        order_note = next(n for n in self.feed(self.token_a) if n["category"] == "orders")
        _, stock_clerk = self.join({"inventory": "view"})
        self.assertEqual(self.post(f"{N}/{order_note['id']}/read", {}, stock_clerk).status_code, 404)
        self.assertEqual(self.post(f"{N}/{order_note['id']}/read", {}, self.token_b).status_code, 404)
        self.assertEqual(self.feed(self.token_b), [])                                     # store B has none of store A's

    def test_old_notifications_fall_out_of_the_window_which_is_a_setting(self):
        StoreNotification.objects.filter(kind="plan_changed").update(created_at=timezone.now() - timedelta(days=30))
        self.assertIn("plan_changed", [n["kind"] for n in self.feed(self.token_a)])        # default window: 90 days
        Setting.objects.create(key="notifications.recent_days", value=7)
        self.assertNotIn("plan_changed", [n["kind"] for n in self.feed(self.token_a)])

    def test_the_list_is_paginated_like_every_other_list(self):
        body = self.get(N, self.token_a, per_page=2).json()
        self.assertEqual((len(body["items"]), body["total"], body["pages"]), (2, 5, 3))
