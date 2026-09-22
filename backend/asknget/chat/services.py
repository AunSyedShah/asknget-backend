"""Everything that writes to a conversation goes through here.

`send()` is the single write path: it appends the message, flags off-platform
contact details, moves `last_message_at` / the preview, bumps the other side's
unread counter and reopens a closed thread. Nothing else touches those columns,
so the inbox stays one query.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from django.db import transaction
from django.db.models import Count, F, Q, Sum
from django.utils import timezone
from ninja.errors import HttpError

from markets.context import get_country
from orders.models import Order
from portal import entitlements
from sellers.models import Store

from . import rules
from .models import Conversation, Message

Role = Message.SenderRole


# --------------------------------------------------------------------------- #
# Lookups
# --------------------------------------------------------------------------- #


def conversations_for_customer(user):
    return (
        Conversation.objects.filter(customer=user)
        .select_related("store", "store__country", "order")
        .order_by("-last_message_at", "-created_at")
    )


def conversations_for_store(store, *, tab: str = "all", q: str | None = None):
    qs = Conversation.objects.filter(store=store).select_related("customer", "order").order_by("-last_message_at", "-created_at")
    if tab == "unread":
        qs = qs.filter(seller_unread__gt=0)
    elif tab == "open":
        qs = qs.filter(closed_at__isnull=True)
    elif tab == "closed":
        qs = qs.filter(closed_at__isnull=False)
    if q:
        qs = qs.filter(
            Q(customer__full_name__icontains=q) | Q(customer__email__icontains=q)
            | Q(last_message_preview__icontains=q) | Q(order__order_number__icontains=q)
        )
    return qs


def get_for_customer(user, conversation_id) -> Conversation:
    convo = conversations_for_customer(user).filter(pk=conversation_id).first()
    if convo is None:
        raise HttpError(404, "Conversation not found")
    return convo


def get_for_store(store, conversation_id) -> Conversation:
    convo = conversations_for_store(store).filter(pk=conversation_id).first()
    if convo is None:
        raise HttpError(404, "Conversation not found")
    return convo


def _after_cutoff(convo: Conversation, after: str):
    """`after` is the id of the newest message the client already holds.

    Deliberately not a timestamp: responses serialise datetimes to milliseconds while
    the column keeps microseconds, so a timestamp cursor re-delivers the message it
    points at. The client always has the last message object, so it always has the id.
    """
    try:
        anchor = convo.messages.filter(pk=uuid.UUID(str(after))).first()
    except ValueError:
        raise HttpError(422, "`after` must be a message id")
    if anchor is None:
        raise HttpError(404, "Unknown message id in `after`")
    return anchor.created_at


def messages(convo: Conversation, *, after: str | None = None, limit: int = 50):
    qs = convo.messages.select_related("sender").order_by("created_at")
    if after:
        qs = qs.filter(created_at__gt=_after_cutoff(convo, after))
    return list(qs[:limit])


# --------------------------------------------------------------------------- #
# Opening a thread
# --------------------------------------------------------------------------- #


def _buyer_store(store_id=None, slug: str | None = None) -> Store:
    """The store a buyer may write to: active, and in the market they are shopping.
    Cross-market chat is meaningless — carts, orders and catalogue are per market."""
    qs = Store.objects.filter(status=Store.StoreStatus.ACTIVE).select_related("country")
    country = get_country()
    if country is not None:
        qs = qs.filter(country=country)
    store = qs.filter(pk=store_id).first() if store_id else qs.filter(slug=slug).first()
    if store is None:
        raise HttpError(404, "Store not found")
    return store


@transaction.atomic
def open_for_customer(user, *, store_id=None, store_slug: str | None = None, order_id=None, body: str, subject: str = "") -> Conversation:
    store = _buyer_store(store_id, store_slug)
    order = None
    if order_id:
        order = Order.objects.filter(pk=order_id, user=user, items__store=store).distinct().first()
        if order is None:
            raise HttpError(404, "Order not found for this store")
    convo, created = Conversation.objects.get_or_create(
        store=store, customer=user,
        defaults={"order": order, "subject": subject[:140]},
    )
    if not created and order is not None and convo.order_id != order.id:
        convo.order = order  # latest order discussed becomes the context
        convo.save(update_fields=["order", "updated_at"])
    send(convo, role=Role.CUSTOMER, sender=user, body=body)
    return convo


@transaction.atomic
def open_for_store(store, customer, *, body: str, order=None, actor=None) -> Conversation:
    """'Message Customer' from the Customers screen — premium only (checked at the router)."""
    convo, _ = Conversation.objects.get_or_create(store=store, customer=customer, defaults={"order": order})
    send(convo, role=Role.SELLER, sender=actor, body=body)
    return convo


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #


def _rate_limit(sender) -> None:
    if sender is None:
        return
    since = timezone.now() - timedelta(seconds=rules.RATE_LIMIT_SECONDS)
    if Message.objects.filter(sender=sender, created_at__gte=since).count() >= rules.RATE_LIMIT_MESSAGES:
        raise HttpError(429, "Too many messages — slow down for a minute")


@transaction.atomic
def send(convo: Conversation, *, role: str, sender, body: str) -> Message:
    body = (body or "").strip()
    if not body:
        raise HttpError(422, "Message cannot be empty")
    if len(body) > rules.MAX_BODY:
        raise HttpError(422, f"Message must be {rules.MAX_BODY} characters or fewer")
    _rate_limit(sender)

    reason = rules.flag_reason(body)
    now = timezone.now()
    message = Message.objects.create(
        conversation=convo, sender_role=role, sender=sender, body=body,
        flagged_at=now if reason else None, flag_reason=reason,
    )
    fields = ["last_message_at", "last_message_preview", "updated_at"]
    convo.last_message_at = now
    convo.last_message_preview = rules.preview(body)
    if role == Role.CUSTOMER:
        convo.seller_unread = F("seller_unread") + 1
        fields.append("seller_unread")
        if convo.closed_at:  # a new question reopens a resolved thread
            convo.closed_at = None
            fields.append("closed_at")
    elif role == Role.SELLER:
        convo.customer_unread = F("customer_unread") + 1
        convo.seller_unread = 0
        convo.seller_last_read_at = now
        fields += ["customer_unread", "seller_unread", "seller_last_read_at"]
    convo.save(update_fields=fields)
    convo.refresh_from_db(fields=[f for f in fields if f != "updated_at"])
    return message


def mark_read(convo: Conversation, *, role: str) -> Conversation:
    now = timezone.now()
    if role == Role.SELLER:
        convo.seller_unread, convo.seller_last_read_at = 0, now
        convo.save(update_fields=["seller_unread", "seller_last_read_at", "updated_at"])
    else:
        convo.customer_unread, convo.customer_last_read_at = 0, now
        convo.save(update_fields=["customer_unread", "customer_last_read_at", "updated_at"])
    return convo


def set_closed(convo: Conversation, *, closed: bool) -> Conversation:
    convo.closed_at = timezone.now() if closed else None
    convo.save(update_fields=["closed_at", "updated_at"])
    return convo


# --------------------------------------------------------------------------- #
# Serialisation
# --------------------------------------------------------------------------- #


def seller_can_reply(store) -> bool:
    return entitlements.has(store, entitlements.CHAT_INBOX)


def message_out(m: Message) -> dict:
    return {
        "id": m.id, "sender_role": m.sender_role,
        "sender_name": (m.sender.full_name or m.sender.email) if m.sender_id else "asknget",
        "body": m.body, "flagged": m.flagged_at is not None, "created_at": m.created_at,
    }


def customer_row(convo: Conversation) -> dict:
    """Buyer's list: who they are talking to."""
    return {
        "id": convo.id,
        "store": {"id": convo.store_id, "name": convo.store.display_name, "slug": convo.store.slug},
        "order_number": convo.order.order_number if convo.order_id else None,
        "subject": convo.subject,
        "last_message_at": convo.last_message_at,
        "last_message_preview": convo.last_message_preview,
        "unread": convo.customer_unread,
        "closed": convo.closed_at is not None,
        "seller_can_reply": seller_can_reply(convo.store),
    }


def seller_row(convo: Conversation) -> dict:
    """Seller's inbox: which customer, how long they have been waiting."""
    customer = convo.customer
    return {
        "id": convo.id,
        "customer": {"id": str(customer.pk), "name": customer.full_name or customer.email, "email": customer.email},
        "order_number": convo.order.order_number if convo.order_id else None,
        "subject": convo.subject,
        "last_message_at": convo.last_message_at,
        "last_message_preview": convo.last_message_preview,
        "unread": convo.seller_unread,
        "closed": convo.closed_at is not None,
    }


def thread_out(convo: Conversation, *, for_role: str, after=None, limit: int = 50) -> dict:
    row = seller_row(convo) if for_role == Role.SELLER else customer_row(convo)
    return {**row, "messages": [message_out(m) for m in messages(convo, after=after, limit=limit)]}


def summary_for_customer(user) -> dict:
    agg = Conversation.objects.filter(customer=user).aggregate(unread=Sum("customer_unread"), n=Count("id"))
    return {"unread": agg["unread"] or 0, "conversations": agg["n"] or 0}


def summary_for_store(store) -> dict:
    """Allowed on every plan — a free seller sees that messages are waiting and what
    unlocking the inbox would give them (docs/sprint-5-plan.md §3)."""
    agg = Conversation.objects.filter(store=store).aggregate(unread=Sum("seller_unread"), n=Count("id"))
    locked = not seller_can_reply(store)
    return {
        "unread": agg["unread"] or 0,
        "conversations": agg["n"] or 0,
        "waiting_customers": Conversation.objects.filter(store=store, seller_unread__gt=0).count(),
        "locked": locked,
        "locked_reason": "premium_required" if locked else "",
    }
