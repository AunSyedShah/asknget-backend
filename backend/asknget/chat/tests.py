"""Customer ↔ seller chat: who may write, who may read, and what premium unlocks."""

from django.conf import settings

from core.testing import ApiTestCase, make_category, make_country, make_product, make_store, make_user
from orders.models import Order, OrderItem

from .models import Conversation, Message

ME = "/api/v1/me"
SELLER = "/api/v1/seller/chat"


class ChatTestCase(ApiTestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user(is_seller=True, full_name="Sirosh Bashir")
        cls.store = make_store(owner=cls.owner, name="Huda Beauty")
        cls.beauty = make_category("Beauty")
        cls.product = make_product(cls.store, cls.beauty, "NARS BB Cream", 2599, 4099, stock=5)

    def setUp(self):
        self.buyer = make_user(full_name="Olivia Martin")
        self.buyer_token = self.token_for(self.buyer)
        self.seller_token = self.token_for(self.owner)

    def premium(self, on=True):
        self.store.is_premium = on
        self.store.save(update_fields=["is_premium"])

    def open_thread(self, body="Is the 30 ml shade Natural in stock?", **over):
        res = self.post(f"{ME}/conversations", {"store_id": str(self.store.id), "body": body, **over}, self.buyer_token)
        self.assertEqual(res.status_code, 201, res.content)
        return res.json()


class BuyerChatTests(ChatTestCase):
    def test_guest_cannot_chat(self):
        self.assertEqual(self.post(f"{ME}/conversations", {"store_id": str(self.store.id), "body": "hi"}).status_code, 401)
        self.assertEqual(self.get(f"{ME}/conversations").status_code, 401)

    def test_open_and_reuse_one_thread_per_store(self):
        body = self.open_thread()
        self.assertEqual(body["store"]["slug"], self.store.slug)
        self.assertEqual([m["body"] for m in body["messages"]], ["Is the 30 ml shade Natural in stock?"])
        self.assertEqual(body["messages"][0]["sender_role"], "customer")
        self.assertFalse(body["seller_can_reply"])  # free store: be honest with the buyer
        again = self.open_thread("Still waiting")
        self.assertEqual(again["id"], body["id"])
        self.assertEqual(Conversation.objects.count(), 1)
        self.assertEqual(Message.objects.count(), 2)
        self.assertEqual(again["last_message_preview"], "Still waiting")

    def test_buyer_may_message_a_free_store_and_is_told_it_may_not_be_answered(self):
        body = self.open_thread()
        self.assertFalse(body["seller_can_reply"])
        self.premium()
        self.assertTrue(self.get(f"{ME}/conversations/{body['id']}", self.buyer_token).json()["seller_can_reply"])

    def test_store_must_be_active_and_in_this_market(self):
        pk = make_country("PK")
        pk_store = make_store(name="Karachi Cosmetics", country=pk)
        res = self.post(f"{ME}/conversations", {"store_id": str(pk_store.id), "body": "hi"}, self.buyer_token)
        self.assertEqual(res.status_code, 404)
        res = self.post(f"{ME}/conversations", {"store_id": str(pk_store.id), "body": "hi"}, self.buyer_token, region="PK")
        self.assertEqual(res.status_code, 201, res.content)

    def test_order_context_must_belong_to_the_buyer_and_store(self):
        order = Order.objects.create(user=self.buyer, status=Order.OrderStatus.CONFIRMED, order_number="ASK00042",
                                     country_id=self.store.country_id, contact_first_name="Olivia", contact_last_name="Martin",
                                     contact_email=self.buyer.email, contact_phone="0", shipping_address={}, subtotal_cents=2599,
                                     total_cents=2599, payment_method_type="card")
        OrderItem.objects.create(order=order, store=self.store, product=self.product, variant=self.product.variants.first(),
                                 product_name=self.product.name, qty=1, unit_price_cents=2599, line_total_cents=2599)
        body = self.open_thread("Where is my parcel?", order_id=str(order.id))
        self.assertEqual(body["order_number"], "ASK00042")
        other = make_user()
        res = self.post(f"{ME}/conversations", {"store_id": str(self.store.id), "order_id": str(order.id), "body": "hi"}, self.token_for(other))
        self.assertEqual(res.status_code, 404)  # not this buyer's order

    def test_validation_and_store_reference(self):
        self.assertEqual(self.post(f"{ME}/conversations", {"body": "hi"}, self.buyer_token).status_code, 422)
        self.assertEqual(self.post(f"{ME}/conversations", {"store_slug": "nope", "body": "hi"}, self.buyer_token).status_code, 404)
        self.assertEqual(self.post(f"{ME}/conversations", {"store_id": str(self.store.id), "body": "  "}, self.buyer_token).status_code, 422)
        res = self.post(f"{ME}/conversations", {"store_slug": self.store.slug, "body": "via slug"}, self.buyer_token)
        self.assertEqual(res.status_code, 201, res.content)

    def test_summary_and_read(self):
        convo_id = self.open_thread()["id"]
        self.premium()
        self.post(f"{SELLER}/{convo_id}/messages", {"body": "Yes, 3 left."}, self.seller_token)
        self.assertEqual(self.get(f"{ME}/chat/summary", self.buyer_token).json(), {"unread": 1, "conversations": 1})
        rows = self.get(f"{ME}/conversations", self.buyer_token).json()["items"]
        self.assertEqual((rows[0]["unread"], rows[0]["last_message_preview"]), (1, "Yes, 3 left."))
        self.assertEqual(self.post(f"{ME}/conversations/{convo_id}/read", token=self.buyer_token).json()["unread"], 0)
        self.assertEqual(self.get(f"{ME}/chat/summary", self.buyer_token).json()["unread"], 0)

    def test_polling_returns_only_what_is_new(self):
        convo_id = self.open_thread()["id"]
        first = self.get(f"{ME}/conversations/{convo_id}/messages", self.buyer_token).json()
        self.assertEqual(len(first), 1)
        self.post(f"{ME}/conversations/{convo_id}/messages", {"body": "ping"}, self.buyer_token)
        new = self.get(f"{ME}/conversations/{convo_id}/messages", self.buyer_token, after=first[0]["id"]).json()
        self.assertEqual([m["body"] for m in new], ["ping"])
        self.assertEqual(self.get(f"{ME}/conversations/{convo_id}/messages", self.buyer_token, after=new[0]["id"]).json(), [])
        # the cursor is a message id, never a timestamp (responses round to milliseconds)
        self.assertEqual(self.get(f"{ME}/conversations/{convo_id}/messages", self.buyer_token, after=first[0]["created_at"]).status_code, 422)
        self.assertEqual(self.get(f"{ME}/conversations/{convo_id}/messages", self.buyer_token,
                                  after="00000000-0000-0000-0000-000000000000").status_code, 404)


class SellerInboxTests(ChatTestCase):
    def test_free_seller_sees_counts_but_no_content(self):
        self.open_thread()
        summary = self.get(f"{SELLER}/summary", self.seller_token).json()
        self.assertEqual((summary["unread"], summary["waiting_customers"], summary["locked"], summary["locked_reason"]),
                         (1, 1, True, "premium_required"))
        for res in (self.get(SELLER, self.seller_token), self.get(f"{SELLER}/{Conversation.objects.get().id}", self.seller_token)):
            self.assertEqual(res.status_code, 403)
            self.assertEqual(res.json()["detail"], "premium_required")
        res = self.post(f"{SELLER}/{Conversation.objects.get().id}/messages", {"body": "hi"}, self.seller_token)
        self.assertEqual(res.status_code, 403)

    def test_premium_seller_reads_replies_and_closes(self):
        convo_id = self.open_thread()["id"]
        self.premium()
        summary = self.get(f"{SELLER}/summary", self.seller_token).json()
        self.assertEqual((summary["unread"], summary["locked"]), (1, False))
        rows = self.get(SELLER, self.seller_token).json()["items"]
        self.assertEqual((rows[0]["customer"]["name"], rows[0]["unread"]), ("Olivia Martin", 1))
        thread = self.get(f"{SELLER}/{convo_id}", self.seller_token).json()
        self.assertEqual(len(thread["messages"]), 1)
        res = self.post(f"{SELLER}/{convo_id}/messages", {"body": "Yes, 3 left."}, self.seller_token)
        self.assertEqual(res.status_code, 201, res.content)
        # replying clears the seller's unread and raises the customer's
        row = self.get(SELLER, self.seller_token).json()["items"][0]
        self.assertEqual(row["unread"], 0)
        self.assertEqual(Conversation.objects.get().customer_unread, 1)
        closed = self.post(f"{SELLER}/{convo_id}/close", {"closed": True}, self.seller_token).json()
        self.assertTrue(closed["closed"])
        self.assertEqual([r["id"] for r in self.get(SELLER, self.seller_token, tab="closed").json()["items"]], [convo_id])
        # a new customer message reopens it
        self.post(f"{ME}/conversations/{convo_id}/messages", {"body": "One more thing"}, self.buyer_token)
        self.assertEqual(self.get(SELLER, self.seller_token, tab="unread").json()["total"], 1)
        self.assertFalse(self.get(f"{SELLER}/{convo_id}", self.seller_token).json()["closed"])

    def test_inbox_search_and_isolation_between_stores(self):
        self.premium()
        self.open_thread("Shade question")
        other_store = make_store(name="Other Co")
        other_token = self.token_for(other_store.owner)
        other_store.is_premium = True
        other_store.save(update_fields=["is_premium"])
        self.assertEqual(self.get(SELLER, other_token).json()["total"], 0)
        self.assertEqual(self.get(SELLER, self.seller_token, q="olivia").json()["total"], 1)
        self.assertEqual(self.get(SELLER, self.seller_token, q="nobody").json()["total"], 0)
        # another store cannot open this thread
        self.assertEqual(self.get(f"{SELLER}/{Conversation.objects.get().id}", other_token).status_code, 404)

    def test_message_customer_from_the_customers_screen(self):
        order = Order.objects.create(user=self.buyer, status=Order.OrderStatus.CONFIRMED, order_number="ASK00077",
                                     country_id=self.store.country_id, contact_first_name="Olivia", contact_last_name="Martin",
                                     contact_email=self.buyer.email, contact_phone="0", shipping_address={}, subtotal_cents=2599,
                                     total_cents=2599, payment_method_type="card", payment_status="paid")
        OrderItem.objects.create(order=order, store=self.store, product=self.product, variant=self.product.variants.first(),
                                 product_name=self.product.name, qty=1, unit_price_cents=2599, line_total_cents=2599)
        url = f"/api/v1/seller/customers/{self.buyer.id}/message"
        self.assertEqual(self.post(url, {"body": "Your order shipped"}, self.seller_token).status_code, 403)
        self.premium()
        res = self.post(url, {"body": "Your order shipped"}, self.seller_token)
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()["customer"]["email"], self.buyer.email)
        self.assertEqual(self.get(f"{ME}/conversations", self.buyer_token).json()["items"][0]["last_message_preview"], "Your order shipped")

    def test_guest_customer_cannot_be_messaged(self):
        order = Order.objects.create(status=Order.OrderStatus.CONFIRMED, order_number="ASK00078", country_id=self.store.country_id,
                                     contact_first_name="Guest", contact_last_name="Buyer", contact_email="guest@example.com",
                                     contact_phone="0", shipping_address={}, subtotal_cents=2599, total_cents=2599,
                                     payment_method_type="cod", payment_status="paid")
        OrderItem.objects.create(order=order, store=self.store, product=self.product, variant=self.product.variants.first(),
                                 product_name=self.product.name, qty=1, unit_price_cents=2599, line_total_cents=2599)
        self.premium()
        res = self.post("/api/v1/seller/customers/guest@example.com/message", {"body": "hello"}, self.seller_token)
        self.assertEqual(res.status_code, 409)
        self.assertIn("guest", res.json()["detail"])


class ModerationTests(ChatTestCase):
    def test_off_platform_contact_details_are_flagged_not_blocked(self):
        body = self.open_thread("Call me on 0821234567 instead")
        self.assertTrue(body["messages"][0]["flagged"])
        self.assertEqual(Message.objects.get().flag_reason, "phone")
        self.assertEqual(self.open_thread("mail me at me@example.com")["messages"][-1]["flagged"], True)
        self.assertEqual(self.open_thread("Is it in stock?")["messages"][-1]["flagged"], False)

    def test_staff_queue(self):
        self.open_thread("whatsapp me")
        staff = make_user(is_staff=True)
        rows = self.get("/api/v1/admin/chat/flagged", self.token_for(staff)).json()["items"]
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["flag_reason"], rows[0]["store"]["slug"]), ("off_platform", self.store.slug))
        self.assertEqual(self.get("/api/v1/admin/chat/flagged", self.buyer_token).status_code, 403)

    def test_rate_limit(self):
        from . import rules

        convo_id = self.open_thread()["id"]
        for i in range(rules.RATE_LIMIT_MESSAGES - 1):
            self.assertEqual(self.post(f"{ME}/conversations/{convo_id}/messages", {"body": f"m{i}"}, self.buyer_token).status_code, 201)
        res = self.post(f"{ME}/conversations/{convo_id}/messages", {"body": "one too many"}, self.buyer_token)
        self.assertEqual(res.status_code, 429)


class PlanTests(ChatTestCase):
    def test_plan_payload_and_staff_toggle(self):
        body = self.get("/api/v1/seller/plan", self.seller_token).json()
        self.assertEqual((body["plan"], body["is_premium"], body["features"]), ("free", False, []))
        self.assertEqual(sorted(body["locked_features"]), ["analytics_advanced", "chat_inbox"])
        self.assertEqual({f["key"]: f["included"] for f in body["catalogue"]}, {"analytics_advanced": False, "chat_inbox": False})
        staff = make_user(is_staff=True)
        res = self.post(f"/api/v1/admin/stores/{self.store.id}/plan", {"is_premium": True}, self.token_for(staff))
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()["plan"], "premium")
        self.store.refresh_from_db()
        self.assertTrue(self.store.is_premium)
        body = self.get("/api/v1/seller/plan", self.seller_token).json()
        self.assertEqual(sorted(body["features"]), ["analytics_advanced", "chat_inbox"])
        self.assertEqual(body["locked_features"], [])

    def test_dashboard_carries_the_plan(self):
        body = self.get("/api/v1/seller/dashboard", self.seller_token).json()
        self.assertEqual((body["store"]["plan"], body["store"]["is_premium"]), ("free", False))
        self.assertEqual(sorted(body["plan"]["locked_features"]), ["analytics_advanced", "chat_inbox"])
        self.assertEqual(body["plan"]["plan_label"], "Standard Seller")

    def test_only_staff_may_change_the_plan(self):
        self.assertEqual(self.post(f"/api/v1/admin/stores/{self.store.id}/plan", {"is_premium": True}, self.seller_token).status_code, 403)
        self.assertEqual(self.post(f"/api/v1/admin/stores/{settings.DEFAULT_COUNTRY}/plan", {"is_premium": True}, self.seller_token).status_code, 403)
