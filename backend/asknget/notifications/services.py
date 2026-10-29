"""Rendering and reading notifications, and the seller-only notices not yet moved to events.

Orders and returns go through events (`notifications.bus.emit`, catalogue in notifications/catalog.py). The helpers below
(`stock_alert`, `payout_processed`, ...) write a seller notification directly, in the caller's transaction; they move to events
in a later phase.
"""

from datetime import timedelta

from django.db.models import Count, Exists, OuterRef, Q
from django.utils import timezone
from ninja.errors import HttpError

from config import services as config
from markets.i18n import pick

from .defaults import CATEGORY_SECTION, TEMPLATES
from .models import Notification, NotificationRead, NotificationTemplate

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
    return Notification.objects.create(
        store_id=getattr(store, "pk", store), kind=kind, category=template.category if template else default[0], params=params or {},
        link_type=link[0] if link else "", link_id=str(link[1]) if link else "",
    )


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


def render(notification: Notification, known: dict, country=None) -> tuple[str, str]:
    template, default = known.get(notification.kind), TEMPLATES.get(notification.kind)
    title_map = template.title if template else (default[1] if default else {})
    body_map = template.body if template else (default[2] if default else {})
    # A parameter may itself be per-language ({"en": ..., "ur": ...}): pick the reader's language, like the template.
    params = {k: (pick(v, country=country) if isinstance(v, dict) else v) for k, v in notification.params.items()}
    if "amount_cents" in params and "currency" in params:
        params["amount"] = f"{params['currency']} {params['amount_cents'] / 100:,.2f}"
    return _fill(pick(title_map, country=country, default=notification.kind), params), _fill(pick(body_map, country=country), params)


def group_of(notification: Notification) -> str:
    today = timezone.localdate()
    day = timezone.localtime(notification.created_at).date()
    return "today" if day == today else "yesterday" if day == today - timedelta(days=1) else "earlier"


# --------------------------------------------------------------------------------------
# Reading (per person)
# --------------------------------------------------------------------------------------


def visible_categories(access) -> list[str]:
    """The categories this person may see: those whose portal section they can view; `system` is for the whole team."""
    return [cat for cat, section in CATEGORY_SECTION.items() if section is None or access.allows(section, "view")]


# The tabs of the customer's and the staff member's bell (a seller's depend on their sections, above).
CUSTOMER_CATEGORIES = ["orders", "returns"]
STAFF_CATEGORIES = ["orders", "returns"]


def _recent(qs, user, categories, tab=None):
    if tab and tab != "all":
        categories = [c for c in categories if c == tab]
    cutoff = timezone.now() - timedelta(days=config.get("notifications.recent_days"))
    reads = NotificationRead.objects.filter(notification=OuterRef("pk"), user=user)
    return qs.filter(category__in=categories, created_at__gte=cutoff).annotate(is_read=Exists(reads))


def feed(store, user, access, tab: str | None = None):
    """The store's recent notifications this person may see, each with `is_read` for them."""
    return _recent(Notification.objects.filter(audience=Notification.Audience.SELLER, store=store), user, visible_categories(access), tab)


def customer_feed(user, tab: str | None = None):
    return _recent(Notification.objects.filter(audience=Notification.Audience.CUSTOMER, user=user), user, CUSTOMER_CATEGORIES, tab)


def staff_feed(user, staff, tab: str | None = None):
    """What staff with these permissions see in their markets: lines for one of their markets or for every market."""
    qs = Notification.objects.filter(audience=Notification.Audience.STAFF)
    if not staff.is_superuser:
        qs = qs.filter(permission__in=staff.permissions)
    if staff.markets is not None:
        qs = qs.filter(Q(market__isnull=True) | Q(market_id__in=staff.markets))
    return _recent(qs, user, STAFF_CATEGORIES, tab)


def summary_of(qs, categories) -> dict:
    rows = qs.order_by().values("category").annotate(total=Count("id"), unread=Count("id", filter=Q(is_read=False)))
    by = {row["category"]: row for row in rows}
    tabs = {cat: {"total": by.get(cat, {}).get("total", 0), "unread": by.get(cat, {}).get("unread", 0)} for cat in categories}
    tabs["all"] = {"total": sum(t["total"] for t in tabs.values()), "unread": sum(t["unread"] for t in tabs.values())}
    return {"unread": tabs["all"]["unread"], "tabs": tabs}


def summary(store, user, access) -> dict:
    return summary_of(feed(store, user, access), visible_categories(access))


def mark_read_in(qs, user, notification_id) -> None:
    notification = qs.filter(pk=notification_id).first()
    if notification is None:
        raise HttpError(404, "Notification not found")
    NotificationRead.objects.get_or_create(notification=notification, user=user)


def mark_all_read_in(qs, user) -> int:
    ids = list(qs.filter(is_read=False).values_list("pk", flat=True))
    NotificationRead.objects.bulk_create([NotificationRead(notification_id=pk, user=user) for pk in ids], ignore_conflicts=True)
    return len(ids)


def mark_read(store, user, access, notification_id) -> None:
    mark_read_in(feed(store, user, access), user, notification_id)


def mark_all_read(store, user, access, tab: str | None = None) -> int:
    return mark_all_read_in(feed(store, user, access, tab), user)


def rows(qs, country=None) -> list[dict]:
    """The feed as the API shows it: wording rendered in the reader's language."""
    known = templates()
    out = []
    for n in qs:
        title, body = render(n, known, country)
        out.append({
            "id": n.pk, "category": n.category, "kind": n.kind, "title": title, "body": body, "link_type": n.link_type, "link_id": n.link_id,
            "created_at": n.created_at, "group": group_of(n), "is_read": n.is_read,
        })
    return out
