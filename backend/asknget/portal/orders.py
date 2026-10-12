"""Seller Orders: the store's slice of every paid buyer order (docs/sprint-3-plan.md §3).

A *store order* is the set of one order's lines that belong to this store. Its
status lives on those lines (OrderItem.status); moving it is orders.services.
set_store_status, which also re-derives the buyer-facing Order.status.
"""

from __future__ import annotations

from collections import defaultdict

from django.db.models import Count, OuterRef, Prefetch, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce, TruncMonth
from django.utils import timezone
from ninja.errors import HttpError

from orders import services as order_services
from orders.models import Order, OrderItem, OrderStatusEvent

from .inventory import csv_response
from .periods import change_pct, month_buckets, period_range

Status = Order.OrderStatus
Fulfilment = OrderItem.FulfilmentStatus
TABS = {
    "pending": (Fulfilment.PENDING,),
    "processing": (Fulfilment.PROCESSING, Fulfilment.PACKED),  # "currently being prepared"
    "shipped": (Fulfilment.SHIPPED,),
    "delivered": (Fulfilment.DELIVERED,),
    "cancelled": (Fulfilment.CANCELLED,),
    "returned": (Fulfilment.RETURNED,),
}
TIMELINE = [  # View Order → "Order Timeline"
    ("placed", "Order Placed"), ("paid", "Payment Confirmed"), ("processing", "Processing"),
    ("packed", "Packed"), ("shipped", "Shipped"), ("delivered", "Delivered"),
]


# --------------------------------------------------------------------------- #
# Querysets
# --------------------------------------------------------------------------- #


def store_orders(store):
    """Paid orders containing this store's lines, annotated with the store's part."""
    mine = OrderItem.objects.filter(order=OuterRef("pk"), store=store)
    return (
        Order.objects.filter(items__store=store)
        .exclude(status=Status.PENDING_PAYMENT)
        .distinct()
        .select_related("user")
        .annotate(
            # the first line still in play (see orders.services.store_status); one expression, no extra query
            store_status=Coalesce(
                Subquery(mine.exclude(status__in=order_services.SETTLED).order_by("created_at").values("status")[:1]),
                Subquery(mine.order_by("created_at").values("status")[:1]),
            ),
            items_count=Coalesce(Subquery(mine.values("order").annotate(n=Sum("qty")).values("n")[:1]), Value(0)),
            store_total_cents=Coalesce(Subquery(mine.values("order").annotate(s=Sum("line_total_cents")).values("s")[:1]), Value(0)),
        )
    )


def list_orders(store, *, tab: str = "all", q: str | None = None, date_from=None, date_to=None, sort: str = "newest"):
    qs = store_orders(store)
    if tab in TABS:
        qs = qs.filter(store_status__in=TABS[tab])
    if q:
        qs = qs.filter(Q(order_number__icontains=q) | Q(contact_email__icontains=q) | Q(contact_first_name__icontains=q) | Q(contact_last_name__icontains=q))
    if date_from:
        qs = qs.filter(placed_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(placed_at__date__lte=date_to)
    order = {"newest": "-placed_at", "oldest": "placed_at", "total": "-store_total_cents", "total_asc": "store_total_cents"}[sort]
    return qs.order_by(order)


def customer_name(order: Order) -> str:
    return f"{order.contact_first_name} {order.contact_last_name}".strip()


def row(o: Order) -> dict:
    return {
        "id": o.id, "order_number": o.order_number, "customer": customer_name(o), "customer_email": o.contact_email,
        "items_count": o.items_count, "placed_at": o.placed_at, "total_cents": o.store_total_cents, "currency": o.currency,
        "payment_method": o.payment_method_type, "payment_status": o.payment_status, "status": o.store_status,
    }


def tabs(store) -> dict:
    counts = defaultdict(int)
    for status, n in store_orders(store).values_list("store_status").annotate(n=Count("id", distinct=True)).values_list("store_status", "n"):
        counts[status] += n
    out = {tab: sum(counts[s] for s in statuses) for tab, statuses in TABS.items()}
    out["all"] = sum(counts.values())
    return out


# --------------------------------------------------------------------------- #
# Overview
# --------------------------------------------------------------------------- #


def _kpi(cur, prev):
    return {"value": cur, "previous": prev, "change_pct": change_pct(cur, prev)}


def overview(store, period_name: str) -> dict:
    p = period_range(period_name)
    qs = store_orders(store)
    placed = lambda a, b: qs.filter(placed_at__gte=a, placed_at__lt=b)  # noqa: E731
    delivered_events = OrderStatusEvent.objects.filter(store=store, status=Status.DELIVERED)
    delivered = lambda a, b: delivered_events.filter(created_at__gte=a, created_at__lt=b).values("order").distinct().count()  # noqa: E731

    year = timezone.localtime().year
    series = {m: {"delivered": 0.0, "pending": 0.0, "refunded": 0.0} for m in range(1, 13)}
    rows = (
        qs.filter(placed_at__year=year).annotate(m=TruncMonth("placed_at"))
        .values("m", "store_status").annotate(n=Count("id", distinct=True))
    )
    for r in rows:
        key = "delivered" if r["store_status"] == Fulfilment.DELIVERED else "refunded" if r["store_status"] in (Fulfilment.RETURNED, Fulfilment.CANCELLED) else "pending"
        series[r["m"].month][key] += r["n"]

    in_period = placed(p.start, p.end)
    total = in_period.count()
    share = {}
    if total:
        for status, n in in_period.values_list("store_status").annotate(n=Count("id", distinct=True)).values_list("store_status", "n"):
            share[status] = round(n / total * 100, 1)

    return {
        "period": period_name,
        "total_orders": _kpi(total, placed(p.prev_start, p.prev_end).count()),
        "pending": _kpi(qs.filter(store_status=Fulfilment.PENDING).count(), None),
        "processing": _kpi(qs.filter(store_status__in=TABS["processing"]).count(), None),
        "delivered": _kpi(delivered(p.start, p.end), delivered(p.prev_start, p.prev_end)),
        "monthly": [{"label": b.strftime("%b"), "values": series[b.month]} for b in month_buckets(year)],
        "status_share": share,
        "latest": [row(o) for o in qs.order_by("-placed_at")[:5]],
        "tabs": tabs(store),
    }


# --------------------------------------------------------------------------- #
# Detail / actions
# --------------------------------------------------------------------------- #


def get_order(store, order_id) -> Order:
    order = (
        store_orders(store)
        .prefetch_related(Prefetch("items", queryset=OrderItem.objects.filter(store=store).select_related("product")),
                          Prefetch("status_events", queryset=OrderStatusEvent.objects.filter(Q(store=store) | Q(store__isnull=True)).order_by("created_at")))
        .filter(pk=order_id).first()
    )
    if order is None:
        raise HttpError(404, "Order not found")
    return order


def timeline(order: Order, store) -> list[dict]:
    events = list(order.status_events.all())
    first = {}
    for e in events:
        key = {Status.CONFIRMED: "paid", Status.PROCESSING: "processing", Status.PACKED: "packed", Status.SHIPPED: "shipped",
               Status.OUT_FOR_DELIVERY: "shipped", Status.DELIVERED: "delivered", Status.CANCELLED: "cancelled", Status.RETURNED: "returned"}.get(e.status)
        if key and key not in first and (e.store_id == store.pk or e.store_id is None):
            first[key] = e
    first["placed"] = None
    stages = []
    for key, label in TIMELINE:
        at = order.placed_at if key == "placed" else (first[key].created_at if key in first else None)
        if key == "paid" and at is None and order.payment_status == Order.OrderPaymentStatus.PAID:
            at = order.placed_at
        stages.append({"key": key, "label": label, "at": at, "state": "done" if at else "waiting", "note": first[key].note if key in first and key != "placed" else ""})
    for key, label in (("cancelled", "Cancelled"), ("returned", "Returned")):
        if key in first:
            stages.append({"key": key, "label": label, "at": first[key].created_at, "state": "done", "note": first[key].note})
            for s in stages:
                if s["state"] == "waiting":
                    s["state"] = "skipped"
    return stages


def item_out(i: OrderItem) -> dict:
    return {
        "id": i.id, "product_id": i.product_id, "product_slug": i.product.slug if i.product_id else None,
        "product_name": i.product_name, "variant_summary": i.variant_summary, "image_url": i.image_url or None,
        "qty": i.qty, "unit_price_cents": i.unit_price_cents, "line_total_cents": i.line_total_cents, "status": i.status,
    }


def detail(order: Order, store) -> dict:
    items = list(order.items.all())
    subtotal = sum(i.line_total_cents for i in items)
    multi = OrderItem.objects.filter(order=order).exclude(store=store).exists()
    total = subtotal if multi else subtotal + order.shipping_cents - order.discount_cents
    return {
        "id": order.id, "order_number": order.order_number, "placed_at": order.placed_at, "status": order_services.store_status(items),
        "allowed_next": sorted(order_services.FULFILMENT_NEXT[order_services.store_status(items)]),
        "customer": {"name": customer_name(order), "email": order.contact_email, "phone": order.contact_phone,
                     "customer_key": str(order.user_id) if order.user_id else order.contact_email.lower()},
        "shipping_address": order.shipping_address, "billing_address": order.billing_address,
        "delivery_method": order.delivery_method_snapshot or None, "special_note": order.special_note,
        "payment": {"method": order.payment_method_type, "status": order.payment_status, "amount_cents": order.total_cents, "currency": order.currency},
        "timeline": timeline(order, store),
        "items": [item_out(i) for i in items],
        "summary": {"subtotal_cents": subtotal, "shipping_cents": order.shipping_cents, "discount_cents": order.discount_cents, "tax_cents": 0,
                    "total_cents": total, "currency": order.currency, "multi_store": multi, "order_total_cents": order.total_cents,
                    "shipping_shared": multi},
        "cancel_reason": order.cancel_reason,
    }


def update_status(store, order_id, *, status: str, note: str, actor) -> Order:
    order = get_order(store, order_id)
    order_services.set_store_status(order, store, status=status, actor_user=actor, note=note)
    return get_order(store, order_id)


def cancel(store, order_id, *, reason: str, actor) -> Order:
    return update_status(store, order_id, status=Fulfilment.CANCELLED, note=reason, actor=actor)


def invoice(order: Order, store) -> dict:
    d = detail(order, store)
    d["invoice_number"] = f"INV-{order.order_number}-{store.slug[:12].upper()}"
    d["issued_at"] = timezone.now()
    d["seller"] = {"name": store.display_name, "slug": store.slug}
    return d


def export(store):
    def rows():
        for o in list_orders(store):
            yield [o.order_number, o.placed_at.isoformat(), customer_name(o), o.contact_email, o.items_count, o.store_total_cents / 100,
                   o.currency, o.payment_method_type, o.payment_status, o.store_status]

    return csv_response("orders.csv", ["order_number", "placed_at", "customer", "email", "items", "total", "currency", "payment_method", "payment_status", "status"], rows())
