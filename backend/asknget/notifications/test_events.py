"""Sprint 9 phase 2: one event tells customers, seller teams and staff, by the admin's rules, in-app and by e-mail."""

from unittest import mock

from django.core import mail
from django.core.management import call_command
from django.db import transaction
from django.utils import timezone

from core.testing import make_country, make_staff, make_user
from notifications import bus
from notifications.admin import RuleForm
from notifications.models import Delivery, Event, Notification, NotificationRule, NotificationTemplate, RuleChange
from notifications.tests import NotificationTestCase
from team.models import StoreMember

ME, ADMIN = "/api/v1/me/notifications", "/api/v1/admin/notifications"


class EventTestCase(NotificationTestCase):
    def place(self, token, *products, **kw):
        with self.captureOnCommitCallbacks(execute=True):
            return self.order(token, *products, **kw)

    def paid(self, order):
        with self.captureOnCommitCallbacks(execute=True):
            self.confirm(order)

    def sent_to(self, address):
        return [m.subject for m in mail.outbox if address in m.to]


class CustomerTests(EventTestCase):
    def test_placing_an_order_puts_it_in_the_customers_bell_and_mails_them_once_it_is_saved(self):
        user, token = self.buyer()
        order = self.place(token, self.bb, email=user.email)
        self.assertEqual(self.sent_to(user.email), [f"asknget order {order.order_number} received"])
        self.assertIn(f"Total: {order.currency} {order.total_cents / 100:,.2f}.", mail.outbox[0].body)
        items = self.get(ME, token).json()["items"]
        self.assertEqual([(i["kind"], i["title"], i["is_read"]) for i in items], [("customer_order_placed", "Order Placed", False)])
        self.assertEqual((items[0]["link_type"], items[0]["link_id"]), ("order", str(order.pk)))
        self.assertEqual(self.get(f"{ME}/summary", token).json()["unread"], 1)

    def test_a_guest_has_no_bell_but_gets_the_email(self):
        cart = self.post("/api/v1/cart/items", {"variant_id": str(self.bb.variants.first().id), "qty": 1}).json()
        contact = {"first_name": "Guest", "last_name": "Buyer", "email": "guest@example.com", "phone": "+27 82 123 4567"}
        with self.captureOnCommitCallbacks(execute=True):
            res = self.client.post("/api/v1/checkout", {"contact": contact, "address": {"address": "1 Long St", "city": "Cape Town", "postal_code": "8001"},
                                   "payment_method_type": "card"}, content_type="application/json", headers={"X-Cart-Token": cart["cart_token"]})
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(len(self.sent_to("guest@example.com")), 1)
        self.assertFalse(Notification.objects.filter(audience="customer").exists())

    def test_reading_is_personal_and_nobody_reads_someone_elses(self):
        user, token = self.buyer()
        self.place(token, self.bb, email=user.email)
        _, other = self.buyer("Someone Else")
        mine = self.get(ME, token).json()["items"][0]["id"]
        self.assertEqual(self.get(ME, other).json()["items"], [])
        self.assertEqual(self.post(f"{ME}/{mine}/read", token=other).status_code, 404)
        self.assertEqual(self.post(f"{ME}/{mine}/read", token=token).status_code, 204)
        self.assertEqual(self.get(f"{ME}/summary", token).json()["unread"], 0)
        self.place(token, self.watch, email=user.email)
        self.assertEqual(self.post(f"{ME}/read-all", token=token).json(), {"marked": 1})

    def test_payment_and_progress_are_told_to_the_customer(self):
        user, token = self.buyer()
        order = self.place(token, self.bb, email=user.email)
        self.paid(order)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.post(f"/api/v1/seller/orders/{order.pk}/status", {"status": "processing"}, self.token_a).status_code, 200)
            self.assertEqual(self.post(f"/api/v1/seller/orders/{order.pk}/status", {"status": "packed"}, self.token_a).status_code, 200)
            self.assertEqual(self.post(f"/api/v1/seller/orders/{order.pk}/status", {"status": "shipped"}, self.token_a).status_code, 200)
        kinds = [i["kind"] for i in self.get(ME, token).json()["items"]]
        self.assertEqual(kinds, ["customer_order_shipped_store", "customer_order_paid", "customer_order_placed"])
        self.assertIn(f"asknget order {order.order_number}: items shipped", self.sent_to(user.email))


class SellerTeamTests(EventTestCase):
    def test_the_owner_and_members_who_may_see_orders_are_mailed_a_paid_order_and_others_are_not(self):
        orders_clerk = make_user(full_name="Orders Clerk")
        stock_clerk = make_user(full_name="Stock Clerk")
        for user, perms in ((orders_clerk, {"orders": "view"}), (stock_clerk, {"inventory": "manage"})):
            StoreMember.objects.create(store=self.store_a, user=user, email=user.email, permissions=perms, status="active", joined_at=timezone.now())
        _, token = self.buyer()
        order = self.place(token, self.bb, self.watch)
        mail.outbox.clear()
        self.paid(order)
        self.assertEqual(self.sent_to(self.owner_a.email), [f"New order #{order.order_number}"])
        self.assertEqual(self.sent_to(orders_clerk.email), [f"New order #{order.order_number}"])
        self.assertEqual(self.sent_to(stock_clerk.email), [])
        self.assertEqual(self.sent_to(self.owner_b.email), [f"New order #{order.order_number}"])           # the other store in the order


class RuleTests(EventTestCase):
    def test_an_admin_switches_an_audience_off_and_a_market_row_beats_the_every_market_row(self):
        NotificationRule.objects.create(event="order.placed", audience="customer", in_app=False, email=False)
        user, token = self.buyer()
        self.place(token, self.bb, email=user.email)
        self.assertEqual((self.sent_to(user.email), self.get(ME, token).json()["items"]), ([], []))
        NotificationRule.objects.create(event="order.placed", audience="customer", market_id="ZA", in_app=True, email=True)
        self.place(token, self.watch, email=user.email)
        self.assertEqual(len(self.sent_to(user.email)), 1)
        self.assertEqual(len(self.get(ME, token).json()["items"]), 1)

    def test_switching_a_wording_off_stops_that_kind(self):
        NotificationTemplate.objects.filter(kind="customer_order_placed").update(is_active=False)
        user, token = self.buyer()
        self.place(token, self.bb, email=user.email)
        self.assertEqual((self.sent_to(user.email), self.get(ME, token).json()["items"]), ([], []))

    def test_high_value_orders_reach_the_staff_who_may_see_orders_in_that_market_only(self):
        make_country("PK")
        NotificationRule.objects.create(event="order.placed", audience="staff", email=True, min_amount_cents=15000)
        za_orders = make_staff("orders.view", markets=("ZA",), email="za-orders@asknget.test")
        pk_orders = make_staff("orders.view", markets=("PK",), email="pk-orders@asknget.test")
        kyc = make_staff("sellers.review", email="kyc@asknget.test")
        _, token = self.buyer()
        self.place(token, self.bb)                                              # ZAR 100: under the threshold
        self.assertEqual(self.sent_to("za-orders@asknget.test"), [])
        big = self.place(token, self.watch)                                     # ZAR 200
        self.assertEqual(len(self.sent_to("za-orders@asknget.test")), 1)
        self.assertEqual((self.sent_to("pk-orders@asknget.test"), self.sent_to("kyc@asknget.test")), ([], []))
        seen = lambda staff: [i["kind"] for i in self.get(ADMIN, self.token_for(staff)).json()["items"]]
        self.assertEqual(seen(za_orders), ["staff_order_placed"])
        self.assertEqual((seen(pk_orders), seen(kyc)), ([], []))
        self.assertIn(big.order_number, self.get(ADMIN, self.token_for(za_orders)).json()["items"][0]["body"])

    def test_the_rule_form_refuses_an_audience_the_event_has_nothing_for_and_unknown_permissions(self):
        form = RuleForm(data={"event": "order.shipped", "audience": "staff", "is_active": True, "in_app": True})
        self.assertFalse(form.is_valid())
        form = RuleForm(data={"event": "order.placed", "audience": "staff", "staff_permission": "orders.fly", "in_app": True})
        self.assertFalse(form.is_valid())
        self.assertIn("staff_permission", form.errors)

    def test_rule_changes_made_in_django_admin_are_recorded(self):
        admin = make_user(is_staff=True, is_superuser=True)
        self.client.force_login(admin)
        res = self.client.post("/admin/notifications/notificationrule/add/", {
            "event": "order.placed", "audience": "staff", "is_active": "on", "in_app": "on", "email": "on", "realtime": "on",
            "user_can_opt_out": "on", "default_on": "on", "staff_permission": "", "min_amount_cents": "50000",
        })
        self.assertEqual(res.status_code, 302, res.content[:2000])
        rule = NotificationRule.objects.get()
        change = RuleChange.objects.get(rule=rule)
        self.assertEqual((change.user, change.changes["min_amount_cents"]), (admin, [None, 50000]))
        res = self.client.post(f"/admin/notifications/notificationrule/{rule.pk}/change/", {
            "event": "order.placed", "audience": "staff", "is_active": "on", "in_app": "on", "realtime": "on",
            "user_can_opt_out": "on", "default_on": "on", "staff_permission": "", "min_amount_cents": "50000",
        })
        self.assertEqual(res.status_code, 302)
        self.assertEqual(RuleChange.objects.filter(rule=rule).order_by("-created_at").first().changes, {"email": [True, False]})


class OutboxTests(EventTestCase):
    def test_a_change_that_rolls_back_leaves_no_event_no_notification_and_sends_nothing(self):
        user, token = self.buyer()
        order = self.place(token, self.bb, email=user.email)
        mail.outbox.clear()
        before = (Event.objects.count(), Notification.objects.count(), Delivery.objects.count())
        with self.captureOnCommitCallbacks(execute=True):
            try:
                with transaction.atomic():
                    bus.emit("order.paid", order)
                    raise RuntimeError("the payment step failed after telling people")
            except RuntimeError:
                pass
        self.assertEqual((Event.objects.count(), Notification.objects.count(), Delivery.objects.count()), before)
        self.assertEqual(mail.outbox, [])

    def test_a_failed_send_is_retried_later_and_never_sent_twice(self):
        user, token = self.buyer()
        with mock.patch("notifications.bus.send_mail", side_effect=OSError("SMTP down")):
            self.place(token, self.bb, email=user.email)
        d = Delivery.objects.get(to=user.email)
        self.assertEqual((d.status, d.attempts, d.last_error), ("pending", 1, "SMTP down"))
        self.assertIsNotNone(d.next_attempt_at)
        self.assertEqual(bus.deliver(), {"sent": 0, "failed": 0, "retry": 0})                 # not due yet
        Delivery.objects.filter(pk=d.pk).update(next_attempt_at=timezone.now())
        call_command("deliver_notifications", stdout=mock.MagicMock())
        d.refresh_from_db()
        self.assertEqual((d.status, d.attempts), ("sent", 2))
        self.assertEqual(bus.deliver(), {"sent": 0, "failed": 0, "retry": 0})
        self.assertEqual(len(self.sent_to(user.email)), 1)

    def test_it_gives_up_after_five_attempts(self):
        user, token = self.buyer()
        with mock.patch("notifications.bus.send_mail", side_effect=OSError("mailbox full")):
            self.place(token, self.bb, email=user.email)
            for _ in range(4):
                Delivery.objects.update(next_attempt_at=timezone.now())
                bus.deliver()
        self.assertEqual(Delivery.objects.get(to=user.email).status, "failed")

    def test_the_same_message_cannot_be_queued_twice(self):
        user, token = self.buyer()
        order = self.place(token, self.bb, email=user.email)
        event = Event.objects.get(name="order.placed")
        before = Delivery.objects.count()
        Delivery.objects.bulk_create([Delivery(key=Delivery.objects.first().key, event=event, to=user.email, subject="x", body="x")], ignore_conflicts=True)
        self.assertEqual(Delivery.objects.count(), before)
        self.assertTrue(order)
