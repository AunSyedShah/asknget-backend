"""Creating, rendering and reading notifications.

Emitters (`order_paid`, `stock_alert`, ...) are called from the same transaction as the event
they describe, so if the event rolls back the notification goes with it. Each reads its template
once per call rather than once per notification.
"""

from datetime import timedelta

from django.db.models import Count, Exists, OuterRef, Q
from django.utils import timezone
from ninja.errors import HttpError

from config import services as config
from markets.i18n import pick
from orders.models import Order, OrderItem

from .defaults import CATEGORY_SECTION, TEMPLATES
from .models import NotificationRead, NotificationTemplate, StoreNotification

PAID_STATES = [Order.OrderPaymentStatus.PAID, Order.OrderPaymentStatus.PARTIALLY_REFUNDED, Order.OrderPaymentStatus.REFUNDED]


# --------------------------------------------------------------------------------------
# Creating
# --------------------------------------------------------------------------------------


def templates() -> dict:
    return {t.kind: t for t in NotificationTemplate.objects.all()}


def notify(store, kind: str, params: dict | None = None, *, link: tuple[str, object] | None = None, known: dict | None = None):
    """Record that `kind` happened in `store`. Returns None when an admin has switched that kind off.
    Pass `known` (from `templates()`) when creating several at once."""
    known = known if known is not None else templates()
    template, default = known.get(kind), TEMPLATES.get(kind)
    if template is None and default is None:
        raise ValueError(f"unknown notification kind {kind!r}")
    if template is not None and not template.is_active:
        return None
    return StoreNotification.objects.create(
        store_id=getattr(store, "pk", store), kind=kind, category=template.category if template else default[0], params=params or {},
        link_type=link[0] if link else "", link_id=str(link[1]) if link else "",
    )


def _customer(order) -> str:
    return f"{order.contact_first_name} {order.contact_last_name}".strip()


def order_paid(order: Order) -> None:
    """The order became visible to its sellers: tell each store, and spot first-time customers."""
    store_ids = list(OrderItem.objects.filter(order=order).values_list("store_id", flat=True).distinct())
    known = templates()
    seen_before = set(
        OrderItem.objects.filter(store_id__in=store_ids, order__payment_status__in=PAID_STATES).filter(
            Q(order__user_id=order.user_id) if order.user_id else Q(order__contact_email__iexact=order.contact_email)
        ).exclude(order=order).values_list("store_id", flat=True)
    )
    params = {"order": order.order_number, "customer": _customer(order)}
    for store_id in store_ids:
        notify(store_id, "order_received", params, link=("order", order.pk), known=known)
        notify(store_id, "payment_received", params, link=("order", order.pk), known=known)
        if store_id not in seen_before:
            notify(store_id, "new_customer", params, link=("order", order.pk), known=known)


def order_cancelled(order: Order, *, by: str) -> None:
    """`by` is "customer" or "staff". A seller cancelling their own lines doesn't need telling."""
    if order.payment_status not in PAID_STATES:
        return                                  # sellers never saw an unpaid order, so there is nothing to retract
    known = templates()
    for store_id in OrderItem.objects.filter(order=order).values_list("store_id", flat=True).distinct():
        notify(store_id, f"order_cancelled_{by}", {"order": order.order_number}, link=("order", order.pk), known=known)


def stock_alert(product, status: str, qty: int) -> None:
    kind = "out_of_stock" if status == "out_of_stock" else "low_stock"
    notify(product.store_id, kind, {"product": product.name, "qty": qty}, link=("product", product.pk))


def payout_processed(payout) -> None:
    notify(payout.store_id, "payout_processed", {"amount_cents": payout.amount_cents, "currency": payout.currency}, link=("payout", payout.pk))


def product_reviewed(product, *, approved: bool, reason: str = "") -> None:
    kind = "product_approved" if approved else "product_rejected"
    notify(product.store_id, kind, {"product": product.name, "reason": reason}, link=("product", product.pk))


def plan_changed(store) -> None:
    notify(store.pk, "plan_changed", {"plan": "premium" if store.is_premium else "standard"})


# --------------------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------------------


class _Params(dict):
    def __missing__(self, key):
        return ""                               # a placeholder with no value reads as empty, never as an error


def _fill(text: str, params: dict) -> str:
    try:
        return text.format_map(_Params(params))
    except (ValueError, IndexError, KeyError, AttributeError):
        return text                             # an admin's stray brace must not break the notification list


def render(notification: StoreNotification, known: dict, country=None) -> tuple[str, str]:
    template, default = known.get(notification.kind), TEMPLATES.get(notification.kind)
    title_map = template.title if template else (default[1] if default else {})
    body_map = template.body if template else (default[2] if default else {})
    params = dict(notification.params)
    if "amount_cents" in params and "currency" in params:
        params["amount"] = f"{params['currency']} {params['amount_cents'] / 100:,.2f}"
    return _fill(pick(title_map, country=country, default=notification.kind), params), _fill(pick(body_map, country=country), params)


def group_of(notification: StoreNotification) -> str:
    today = timezone.localdate()
    day = timezone.localtime(notification.created_at).date()
    return "today" if day == today else "yesterday" if day == today - timedelta(days=1) else "earlier"


# --------------------------------------------------------------------------------------
# Reading (per person)
# --------------------------------------------------------------------------------------


def visible_categories(access) -> list[str]:
    """The categories this person may see: those whose portal section they can view; `system` is for the whole team."""
    return [cat for cat, section in CATEGORY_SECTION.items() if section is None or access.allows(section, "view")]


def feed(store, user, access, tab: str | None = None):
    """The store's recent notifications this person may see, each with `is_read` for them."""
    categories = visible_categories(access)
    if tab and tab != "all":
        categories = [c for c in categories if c == tab]
    cutoff = timezone.now() - timedelta(days=config.get("notifications.recent_days"))
    reads = NotificationRead.objects.filter(notification=OuterRef("pk"), user=user)
    return StoreNotification.objects.filter(store=store, category__in=categories, created_at__gte=cutoff).annotate(is_read=Exists(reads))


def summary(store, user, access) -> dict:
    rows = feed(store, user, access).order_by().values("category").annotate(total=Count("id"), unread=Count("id", filter=Q(is_read=False)))
    by = {row["category"]: row for row in rows}
    tabs = {cat: {"total": by.get(cat, {}).get("total", 0), "unread": by.get(cat, {}).get("unread", 0)} for cat in visible_categories(access)}
    tabs["all"] = {"total": sum(t["total"] for t in tabs.values()), "unread": sum(t["unread"] for t in tabs.values())}
    return {"unread": tabs["all"]["unread"], "tabs": tabs}


def mark_read(store, user, access, notification_id) -> None:
    notification = feed(store, user, access).filter(pk=notification_id).first()
    if notification is None:
        raise HttpError(404, "Notification not found")
    NotificationRead.objects.get_or_create(notification=notification, user=user)


def mark_all_read(store, user, access, tab: str | None = None) -> int:
    ids = list(feed(store, user, access, tab).filter(is_read=False).values_list("pk", flat=True))
    NotificationRead.objects.bulk_create([NotificationRead(notification_id=pk, user=user) for pk in ids], ignore_conflicts=True)
    return len(ids)
