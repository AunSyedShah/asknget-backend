"""The live bell: tell an open app at once that its notifications changed.

The socket is a hint, not the record: the database is. A client that was offline (or whose socket dropped) calls the summary
and list endpoints when it reconnects and loses nothing. So publishing is best effort: with no channel layer configured (Vercel,
which cannot hold WebSockets) it does nothing, and the frontend polls `/summary` instead.

Messages, sent to the group `user.<id>` of each person concerned (never to a store or staff group, so permissions decided here
cannot leak):
    {"type": "notification.created", "id", "audience", "category", "kind", "title", "body", "link": {"type", "id"}, "unread"}
    {"type": "notification.read", "audience", "unread"}

Connecting (see consumers.py): the signed-in client asks `POST /realtime/ticket` for a ticket (valid 30 s, used once) and opens
`wss://<host>/ws/notifications?ticket=<ticket>`. The access token never goes in the URL.
"""

import logging
import secrets

from django.core import signing

log = logging.getLogger(__name__)

TICKET_SALT = "notifications.realtime.ticket"
TICKET_SECONDS = 30


def group(user_id) -> str:
    return f"user.{user_id}"


# --------------------------------------------------------------------------------------
# Tickets
# --------------------------------------------------------------------------------------


def issue_ticket(user) -> str:
    """Signed (so any process can check it without a shared store), short-lived, and carrying a nonce so a shared cache can
    make it single-use."""
    return signing.dumps({"u": str(user.pk), "n": secrets.token_urlsafe(9)}, salt=TICKET_SALT, compress=True)


def redeem_ticket(ticket: str) -> str | None:
    """The user id the ticket was issued to, or None (forged, expired, or already used)."""
    try:
        data = signing.loads(ticket, salt=TICKET_SALT, max_age=TICKET_SECONDS)
    except signing.BadSignature:
        return None
    from django.core.cache import cache

    if not cache.add(f"rt-ticket:{data['n']}", 1, TICKET_SECONDS + 5):     # single use (across processes when the cache is Redis)
        return None
    return data["u"]


# --------------------------------------------------------------------------------------
# Publishing
# --------------------------------------------------------------------------------------


def _layer():
    try:
        from channels.layers import get_channel_layer
    except ImportError:                          # channels not installed: nothing to publish to
        return None
    return get_channel_layer()


def send(user_ids, message: dict) -> None:
    layer = _layer()
    if layer is None or not user_ids:
        return
    from asgiref.sync import async_to_sync

    for user_id in user_ids:
        try:
            async_to_sync(layer.group_send)(group(user_id), {"type": "bell", "message": message})
        except Exception:                        # Redis down: the bell catches up on the next refresh
            log.exception("could not push to user %s", user_id)
            return


def published(notifications, rules_by_id: dict) -> None:
    """After commit: push each new notification to the people who may see it (rules with realtime off are skipped)."""
    if _layer() is None:
        return
    from . import bus, services

    known = services.templates()
    teams = bus._store_teams({n.store_id for n in notifications if n.audience == "seller"})
    badges = {}                                   # one count per person and bell, however many lines they get at once
    for n in reversed(notifications):             # oldest first, so the newest arrives last
        rule = rules_by_id.get(n.pk)
        if rule is not None and not rule.realtime:
            continue
        users = _who_sees(n, teams, bus)
        if not users:
            continue
        title, body = services.render(n, known, None)
        message = {"type": "notification.created", "id": str(n.pk), "audience": n.audience, "category": n.category, "kind": n.kind,
                   "title": title, "body": body, "link": {"type": n.link_type, "id": n.link_id} if n.link_type else None}
        for user in users:
            key = (user.pk, n.audience, n.store_id)
            if key not in badges:
                badges[key] = unread(user, n.audience, store=n.store if n.audience == "seller" else None)
            send([user.pk], {**message, "unread": badges[key]})


def _who_sees(n, teams, bus) -> list:
    from .defaults import CATEGORY_SECTION

    if n.audience == "customer":
        return [n.user] if n.user_id else []
    if n.audience == "seller":
        section = CATEGORY_SECTION.get(n.category)
        return [user for user, _addr, _lang, access in teams.get(n.store_id, [])
                if access is None or section is None or access.allows(section, "view")]
    return list(bus.staff_with(n.permission, n.market_id))


def unread(user, audience: str, *, store=None) -> int:
    """The badge for one person in one bell (what `/summary` would say)."""
    from . import services

    if audience == "customer":
        return services.summary_of(services.customer_feed(user), services.CUSTOMER_CATEGORIES)["unread"]
    if audience == "staff":
        from staff.access import access_for

        return services.summary_of(services.staff_feed(user, access_for(user)), services.STAFF_CATEGORIES)["unread"]
    from team.services import resolve_membership

    _store, member = resolve_membership(user, str(store.pk))
    access = member.access() if member else None
    if access is None:
        from team import sections

        access = sections.Access({}, is_owner=True)
    return services.summary(store, user, access)["unread"]


def read_changed(user, audience: str, *, store=None) -> None:
    """After someone marks notifications read: every open tab of theirs updates its badge."""
    if _layer() is None:
        return
    send([user.pk], {"type": "notification.read", "audience": audience, "unread": unread(user, audience, store=store)})
