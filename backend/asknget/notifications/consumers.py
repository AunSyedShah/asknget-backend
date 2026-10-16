"""`/ws/notifications?ticket=...`: one socket per open app, joined to its person's group (see realtime.py).

Close codes: 4401 = no valid ticket (ask for a new one and reconnect). The client may send {"type": "ping"}; the server answers
{"type": "pong"} (uvicorn also pings every 20 s, so an idle socket stays open behind proxies)."""

from channels.generic.websocket import AsyncJsonWebsocketConsumer

from . import realtime


class BellConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        from urllib.parse import parse_qs

        ticket = (parse_qs(self.scope.get("query_string", b"").decode()).get("ticket") or [""])[0]
        from asgiref.sync import sync_to_async

        self.user_id = await sync_to_async(realtime.redeem_ticket)(ticket) if ticket else None
        if self.user_id is None:
            await self.accept()                   # accept, then close: a refused handshake shows the browser no code at all
            await self.close(code=4401)
            return
        self.group_name = realtime.group(self.user_id)
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        await self.send_json({"type": "hello"})

    async def disconnect(self, code):
        if getattr(self, "user_id", None):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive_json(self, content, **kwargs):
        if isinstance(content, dict) and content.get("type") == "ping":
            await self.send_json({"type": "pong"})

    async def bell(self, event):
        await self.send_json(event["message"])
