"""Sprint 9 phase 3: the live bell over WebSocket.

The tests are async (one event loop for the sockets and the in-memory channel layer); the synchronous API calls run through
sync_to_async, in the test's own thread and transaction."""

import json
import time
from unittest import mock

from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from channels.testing.websocket import WebsocketCommunicator   # (channels.testing needs daphne: a dev dependency)
from django.test import override_settings

from asknget.asgi import application
from notifications import realtime
from notifications.models import NotificationRule
from notifications.test_events import EventTestCase

ORIGIN = [(b"origin", b"http://localhost:3000")]
s2a = sync_to_async


@override_settings(CORS_ALLOWED_ORIGINS=["http://localhost:3000"], CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}})
class RealtimeTests(EventTestCase):
    def ticket(self, token):
        res = self.post("/api/v1/realtime/ticket", token=token)
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertEqual((body["expires_in"], body["url"]), (30, "/ws/notifications"))
        return body["ticket"]

    async def open(self, ticket, headers=ORIGIN):
        socket = WebsocketCommunicator(application, f"/ws/notifications?ticket={ticket}", headers=headers)
        connected, code = await socket.connect()
        if connected:                                   # a bad ticket is accepted, then closed with 4401 (a browser sees the code)
            first = await socket.receive_output()
            if first["type"] == "websocket.close":
                return socket, False, first.get("code")
            self.assertEqual(json.loads(first["text"]), {"type": "hello"})
        return socket, connected, code

    async def test_a_ticket_opens_the_socket_once_and_a_bad_one_is_refused(self):
        _, token = await s2a(self.buyer)()
        ticket = await s2a(self.ticket)(token)
        socket, connected, _ = await self.open(ticket)
        self.assertTrue(connected)
        await socket.disconnect()
        _, connected, code = await self.open(ticket)                    # used already
        self.assertEqual((connected, code), (False, 4401))
        _, connected, _ = await self.open("forged")
        self.assertFalse(connected)
        self.assertEqual((await s2a(self.post)("/api/v1/realtime/ticket")).status_code, 401)

    def test_an_old_ticket_is_refused(self):
        user, _ = self.buyer()
        with mock.patch("django.core.signing.time.time", return_value=time.time() - 60):
            old = realtime.issue_ticket(user)
        self.assertIsNone(realtime.redeem_ticket(old))
        self.assertIsNotNone(realtime.redeem_ticket(realtime.issue_ticket(user)))

    async def test_a_page_from_an_unknown_site_cannot_open_it(self):
        _, token = await s2a(self.buyer)()
        _, connected, _ = await self.open(await s2a(self.ticket)(token), headers=[(b"origin", b"https://evil.example")])
        self.assertFalse(connected)

    async def test_a_new_notification_arrives_at_once_with_the_badge_and_reading_it_updates_every_tab(self):
        user, token = await s2a(self.buyer)()
        tab1, *_ = await self.open(await s2a(self.ticket)(token))
        tab2, *_ = await self.open(await s2a(self.ticket)(token))
        order = await s2a(self.place)(token, self.bb, email=user.email)
        for tab in (tab1, tab2):
            message = await tab.receive_json_from()
            self.assertEqual((message["type"], message["kind"], message["audience"], message["unread"]),
                             ("notification.created", "customer_order_placed", "customer", 1))
            self.assertEqual(message["link"], {"type": "order", "id": str(order.pk)})
            self.assertIn(order.order_number, message["body"])
        await s2a(self.post)("/api/v1/me/notifications/read-all", token=token)
        for tab in (tab1, tab2):
            self.assertEqual(await tab.receive_json_from(), {"type": "notification.read", "audience": "customer", "unread": 0})
            await tab.disconnect()

    async def test_each_person_hears_only_their_own_and_ping_gets_pong(self):
        user, token = await s2a(self.buyer)()
        _, other_token = await s2a(self.buyer)("Someone Else")
        mine, *_ = await self.open(await s2a(self.ticket)(token))
        theirs, *_ = await self.open(await s2a(self.ticket)(other_token))
        await s2a(self.place)(token, self.bb, email=user.email)
        self.assertEqual((await mine.receive_json_from())["kind"], "customer_order_placed")
        self.assertTrue(await theirs.receive_nothing(timeout=0.2))
        await theirs.send_json_to({"type": "ping"})
        self.assertEqual(await theirs.receive_json_from(), {"type": "pong"})
        await mine.disconnect()
        await theirs.disconnect()

    async def test_the_seller_team_hears_a_paid_order_and_an_admin_can_turn_the_push_off(self):
        _, buyer_token = await s2a(self.buyer)()
        owner, *_ = await self.open(await s2a(self.ticket)(self.token_a))
        order = await s2a(self.place)(buyer_token, self.bb)
        await s2a(self.paid)(order)
        kinds = {(await owner.receive_json_from())["kind"] for _ in range(3)}
        self.assertEqual(kinds, {"order_received", "payment_received", "new_customer"})
        await s2a(NotificationRule.objects.create)(event="order.paid", audience="seller", realtime=False, email=True)
        second = await s2a(self.place)(buyer_token, self.watch, self.bb)
        await s2a(self.paid)(second)
        self.assertTrue(await owner.receive_nothing(timeout=0.2))      # still in the bell (database), just not pushed
        summary = await s2a(self.get)("/api/v1/seller/notifications/summary", self.token_a)
        self.assertEqual(summary.json()["unread"], 5)
        await owner.disconnect()

    def test_without_a_channel_layer_nothing_is_pushed_and_nothing_breaks(self):
        with override_settings(CHANNEL_LAYERS={}):
            self.assertIsNone(get_channel_layer())
            user, token = self.buyer()
            self.place(token, self.bb, email=user.email)
            self.assertEqual(self.post("/api/v1/me/notifications/read-all", token=token).status_code, 200)
