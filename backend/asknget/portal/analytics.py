"""Advanced Analytics — the premium half of the seller portal (docs/sprint-5-plan.md §4).

The free dashboard says *how much*; these reports say *why*: where shoppers drop
out of the funnel, which products earn their shelf space, what people typed before
they found (or failed to find) you, whether customers come back, and how fast the
store actually ships.

Everything is derived from data already collected — `ProductEvent`, `OrderItem`,
`OrderStatusEvent` and the cached customers rollup. No new tracking, no writes on
the request path.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db.models import Avg, Count, F, Q, Sum
from django.db.models.functions import TruncMonth
from django.utils import timezone

from catalog.models import Product, ProductImage
from config import services as config
from discovery.models import ProductEvent
from ledger.models import LedgerEntry
from orders.models import Order, OrderItem, OrderStatusEvent
from orders.services import store_discount_cents

from . import customers as customers_svc
from .periods import change_pct, period_range
from .products import _sold_items

Kind = ProductEvent.Kind
Fulfilment = OrderItem.FulfilmentStatus
Status = Order.OrderStatus

SEARCH_WINDOW_MINUTES = 30
SEARCH_CACHE_SECONDS = 300

# Refunds and "adjustments" are drawn on the Sales Analytics waterfall but no such
# amount exists in the data yet — the PSP sprint owns them (data-model.md Q50).
# They are reported as 0 rather than guessed at.
REFUNDS_TRACKED = True


def _kpi(cur, prev):
    return {"value": cur, "previous": prev, "change_pct": change_pct(cur, prev)}


def _ratio(part, whole, digits=2):
    return round(part / whole * 100, digits) if whole else 0.0


def _store_items(store):
    """Every line this store ever sold, including cancelled/returned ones — the
    reports below need the failures too (return rate, cancellation rate)."""
    return OrderItem.objects.filter(store=store).exclude(order__status=Status.PENDING_PAYMENT)


def _events(store, kind):
    return ProductEvent.objects.filter(product__store=store, kind=kind)


# --------------------------------------------------------------------------- #
# Overview
# --------------------------------------------------------------------------- #


def overview(store, period_name: str) -> dict:
    p = period_range(period_name)
    items = _store_items(store)

    def window(start, end):
        rows = items.filter(order__placed_at__gte=start, order__placed_at__lt=end)
        agg = rows.aggregate(
            gross=Sum("line_total_cents"),
            units=Sum("qty"),
            orders=Count("order_id", distinct=True),
            lost=Sum("line_total_cents", filter=Q(status__in=(Fulfilment.CANCELLED, Fulfilment.RETURNED))),
            cancelled=Count("order_id", distinct=True, filter=Q(status=Fulfilment.CANCELLED)),
            returned=Count("order_id", distinct=True, filter=Q(status=Fulfilment.RETURNED)),
        )
        gross = agg["gross"] or 0
        lost = agg["lost"] or 0
        orders = agg["orders"] or 0
        buyers = rows.values("order__user_id", "order__contact_email").distinct().count()
        repeat = max(orders - buyers, 0)
        return {
            "gross": gross, "net": gross - lost, "units": agg["units"] or 0, "orders": orders,
            "aov": round((gross - lost) / orders) if orders else 0,
            "cancel_rate": _ratio(agg["cancelled"] or 0, orders),
            "return_rate": _ratio(agg["returned"] or 0, orders),
            "repeat_rate": _ratio(repeat, orders),
        }

    cur, prev = window(p.start, p.end), window(p.prev_start, p.prev_end)
    views = _events(store, Kind.VIEW)

    def conversion(start, end, orders):
        seen = views.filter(created_at__gte=start, created_at__lt=end).count()
        return _ratio(orders, seen)

    return {
        "period": period_name,
        "conversion_rate": _kpi(conversion(p.start, p.end, cur["orders"]), conversion(p.prev_start, p.prev_end, prev["orders"])),
        "daily": _sales_series(store, p.start, p.end),
        "sales_by_category": _sales_by_category(store, p.start, p.end),
        "top_products": _top_products(store, p.start, p.end),
        "customer_snapshot": _customer_snapshot(store),
        "gross_sales_cents": _kpi(cur["gross"], prev["gross"]),
        "net_sales_cents": _kpi(cur["net"], prev["net"]),
        "units": _kpi(cur["units"], prev["units"]),
        "orders": _kpi(cur["orders"], prev["orders"]),
        "aov_cents": _kpi(cur["aov"], prev["aov"]),
        "repeat_order_rate": _kpi(cur["repeat_rate"], prev["repeat_rate"]),
        "cancellation_rate": _kpi(cur["cancel_rate"], prev["cancel_rate"]),
        "return_rate": _kpi(cur["return_rate"], prev["return_rate"]),
    }


def _bucket_label(when, bucket: str) -> str:
    if bucket == "monthly":
        return when.strftime("%Y-%m")
    if bucket == "weekly":
        return (when - timedelta(days=when.weekday())).strftime("%Y-%m-%d")
    return when.strftime("%Y-%m-%d")


def _sales_series(store, start, end, bucket: str = "daily") -> list[dict]:
    """Sold value per bucket — the design's 'Sales Performance' line."""
    rows = (
        _sold_items(store).filter(order__placed_at__gte=start, order__placed_at__lt=end)
        .values("order__placed_at", "line_total_cents", "qty")
    )
    totals: dict = defaultdict(lambda: {"sales": 0.0, "units": 0.0})
    for r in rows:
        key = _bucket_label(timezone.localtime(r["order__placed_at"]), bucket)
        totals[key]["sales"] += float(r["line_total_cents"])
        totals[key]["units"] += float(r["qty"])
    return [{"label": label, "values": values} for label, values in sorted(totals.items())]


def _sales_by_category(store, start, end, limit: int = 8) -> list[dict]:
    rows = (
        _sold_items(store).filter(order__placed_at__gte=start, order__placed_at__lt=end)
        .values("product__category__name")
        .annotate(revenue=Sum("line_total_cents"), orders=Count("order_id", distinct=True))
    )
    total = sum(r["revenue"] or 0 for r in rows) or 0
    out = [
        {
            "category": r["product__category__name"] or "Uncategorised",
            "orders": r["orders"],
            "revenue_cents": r["revenue"] or 0,
            "share_pct": _ratio(r["revenue"] or 0, total),
        }
        for r in rows
    ]
    return sorted(out, key=lambda r: -r["revenue_cents"])[:limit]


def _top_products(store, start, end, limit: int = 5) -> list[dict]:
    rows = (
        _sold_items(store).filter(order__placed_at__gte=start, order__placed_at__lt=end)
        .values("product_id", "product__name")
        .annotate(orders=Count("order_id", distinct=True), revenue=Sum("line_total_cents"), units=Sum("qty"))
        .order_by("-revenue")[:limit]
    )
    rows = list(rows)
    images = dict(
        ProductImage.objects.filter(product_id__in=[r["product_id"] for r in rows])
        .order_by("product_id", "-is_primary", "sort_order")
        .values_list("product_id", "image")
    )
    return [
        {
            "rank": i,
            "product_id": r["product_id"],
            "name": r["product__name"],
            "image_url": (settings.MEDIA_URL + images[r["product_id"]]) if images.get(r["product_id"]) else None,
            "orders": r["orders"],
            "units": r["units"] or 0,
            "revenue_cents": r["revenue"] or 0,
        }
        for i, r in enumerate(rows, start=1)
    ]


def _customer_snapshot(store) -> dict:
    """New / Returning / One-Time — the three types the customers rollup already
    computes, which the Store Performance screen shows as counts."""
    now = timezone.now()
    counts = defaultdict(int)
    for c in customers_svc.all_customers(store).values():
        counts[c.type(now)] += 1
    return {"new": counts["new"], "returning": counts["returning"], "one_time": counts["one_time"]}


# --------------------------------------------------------------------------- #
# Funnel
# --------------------------------------------------------------------------- #


FUNNEL_STEPS = (("views", Kind.VIEW), ("wishlists", Kind.WISHLIST), ("add_to_cart", Kind.ADD_TO_CART))


def funnel(store, period_name: str) -> dict:
    p = period_range(period_name)

    def counts(start, end):
        rows = dict(
            ProductEvent.objects.filter(product__store=store, created_at__gte=start, created_at__lt=end)
            .values_list("kind").annotate(n=Count("id")).values_list("kind", "n")
        )
        purchases = _sold_items(store).filter(order__placed_at__gte=start, order__placed_at__lt=end).aggregate(n=Sum("qty"))["n"] or 0
        return {name: rows.get(kind, 0) for name, kind in FUNNEL_STEPS} | {"purchases": purchases}

    cur, prev = counts(p.start, p.end), counts(p.prev_start, p.prev_end)
    order = ["views", "wishlists", "add_to_cart", "purchases"]
    steps, worst, worst_drop = [], None, -1.0
    for i, name in enumerate(order):
        previous_step = cur[order[i - 1]] if i else 0
        drop = _ratio(previous_step - cur[name], previous_step) if i and previous_step else 0.0
        if i and drop > worst_drop:
            worst, worst_drop = f"{order[i - 1]}→{name}", drop
        steps.append({
            "step": name,
            "count": _kpi(cur[name], prev[name]),
            "from_previous_pct": _ratio(cur[name], previous_step) if i and previous_step else 100.0,
            "drop_off_pct": drop,
        })
    return {
        "period": period_name,
        "steps": steps,
        "view_to_purchase_pct": _kpi(_ratio(cur["purchases"], cur["views"]), _ratio(prev["purchases"], prev["views"])),
        "biggest_drop_off": worst or "",
    }


# --------------------------------------------------------------------------- #
# Products
# --------------------------------------------------------------------------- #

PRODUCT_SORTS = {"revenue": "-revenue_cents", "units": "-units", "views": "-views", "conversion": "-conversion_rate"}


def products(store, period_name: str, *, sort: str = "revenue", limit: int = 10) -> dict:
    p = period_range(period_name)
    sold = _sold_items(store).filter(order__placed_at__gte=p.start, order__placed_at__lt=p.end)
    per_product = {
        r["product_id"]: r for r in sold.values("product_id").annotate(units=Sum("qty"), revenue=Sum("line_total_cents"), orders=Count("order_id", distinct=True))
    }
    views = dict(
        ProductEvent.objects.filter(product__store=store, kind=Kind.VIEW, created_at__gte=p.start, created_at__lt=p.end)
        .values_list("product_id").annotate(n=Count("id")).values_list("product_id", "n")
    )
    carts = dict(
        ProductEvent.objects.filter(product__store=store, kind=Kind.ADD_TO_CART, created_at__gte=p.start, created_at__lt=p.end)
        .values_list("product_id").annotate(n=Count("id")).values_list("product_id", "n")
    )
    live = Product.objects.filter(store=store).exclude(status=Product.ProductStatus.ARCHIVED).select_related("brand", "category")
    images = dict(
        ProductImage.objects.filter(product__store=store).order_by("product_id", "-is_primary", "sort_order")
        .values_list("product_id", "image")
    )

    rows = []
    for product in live:
        stats = per_product.get(product.id, {})
        units, revenue = stats.get("units", 0), stats.get("revenue", 0)
        seen = views.get(product.id, 0)
        rows.append({
            "product_id": product.id, "name": product.name, "slug": product.slug,
            "status": product.status, "stock_status": product.stock_status,
            "image_url": (settings.MEDIA_URL + images[product.id]) if images.get(product.id) else None,
            "views": seen, "add_to_cart": carts.get(product.id, 0), "units": units, "revenue_cents": revenue,
            "orders": stats.get("orders", 0),
            "conversion_rate": _ratio(stats.get("orders", 0), seen),
        })
    rows.sort(key=lambda r: (r["revenue_cents"], r["units"], r["views"]), reverse=True)
    key = PRODUCT_SORTS.get(sort, "-revenue_cents").lstrip("-")
    top = sorted(rows, key=lambda r: r[key], reverse=True)[:limit]
    # Slow movers: live, in stock, seen by shoppers, but nothing sold in the period.
    slow = [
        r for r in rows
        if r["status"] == Product.ProductStatus.ACTIVE
        and r["stock_status"] in Product.AVAILABLE_STOCK_STATUSES
        and r["units"] == 0
    ]
    slow.sort(key=lambda r: (-r["views"], r["name"]))
    earned = sum(r["revenue_cents"] for r in rows)
    selling = [r for r in rows if r["units"]]

    # Conversion is only meaningful with enough views behind it. How many is "enough", and how long the
    # list is, are the admin's call (analytics.low_performer_*), per market or per store.
    policy = config.get_many(["analytics.low_performer_min_views", "analytics.low_performer_limit"], store=store)
    min_views, list_size = policy["analytics.low_performer_min_views"], policy["analytics.low_performer_limit"]
    judged = [r for r in rows if r["status"] == Product.ProductStatus.ACTIVE and r["views"] >= min_views]
    low_performers = sorted(judged, key=lambda r: (r["conversion_rate"], -r["views"], r["name"]))[:list_size]
    conversion_leaders = sorted(judged, key=lambda r: (-r["conversion_rate"], -r["views"], r["name"]))[:list_size]

    return {"period": period_name, "sort": sort, "top": top, "slow_movers": slow[:limit],
            "products_with_no_sales": len(slow), "products_total": len(rows),
            "top_selling_count": len(selling),
            "avg_product_revenue_cents": round(earned / len(rows)) if rows else 0,
            "low_performers": low_performers, "conversion_leaders": conversion_leaders, "low_performer_min_views": min_views,
            "daily": _product_daily(store, p)}


def _product_daily(store, p) -> list[dict]:
    """One point per day of the period, up to today: the Product Performance chart's four tabs
    (Orders / Units Sold / Revenue / Views). Quiet days are present as zeros so the line is continuous."""
    today = timezone.localdate()
    first = timezone.localtime(p.start).date()
    last = min(timezone.localtime(p.end - timedelta(microseconds=1)).date(), today)
    days = {first + timedelta(days=i): {"date": first + timedelta(days=i), "orders": 0, "units": 0, "revenue_cents": 0, "views": 0}
            for i in range((last - first).days + 1)}
    seen_orders: dict = defaultdict(set)
    for placed, order_id, qty, cents in (
        _sold_items(store).filter(order__placed_at__gte=p.start, order__placed_at__lt=p.end)
        .values_list("order__placed_at", "order_id", "qty", "line_total_cents")
    ):
        day = days.get(timezone.localtime(placed).date())
        if day is not None:
            seen_orders[day["date"]].add(order_id)
            day["units"] += qty
            day["revenue_cents"] += cents
    for day, ids in seen_orders.items():
        days[day]["orders"] = len(ids)
    for (created,) in ProductEvent.objects.filter(product__store=store, kind=Kind.VIEW, created_at__gte=p.start, created_at__lt=p.end).values_list("created_at"):
        day = days.get(timezone.localtime(created).date())
        if day is not None:
            day["views"] += 1
    return list(days.values())


# --------------------------------------------------------------------------- #
# Search terms
# --------------------------------------------------------------------------- #


def search_terms(store, period_name: str, *, limit: int = 10) -> dict:
    """What shoppers typed before they landed on this store.

    A search is marketplace-wide — we do not record which products it returned — so a
    query is attributed to the store when the same actor viewed one of its products
    within SEARCH_WINDOW_MINUTES. The scan is capped and cached; it is the only report
    that is not a plain aggregate.
    """
    p = period_range(period_name)
    cache_key = f"portal:analytics:search:{store.pk}:{period_name}:{limit}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    limit_rows = settings.ANALYTICS_EVENT_SCAN_LIMIT
    window = timedelta(minutes=SEARCH_WINDOW_MINUTES)
    store_products = set(Product.objects.filter(store=store).values_list("id", flat=True))
    events = list(
        ProductEvent.objects.filter(created_at__gte=p.start, created_at__lt=p.end)
        .filter(Q(kind=Kind.SEARCH) | Q(kind=Kind.VIEW, product_id__in=store_products))
        .order_by("-created_at", "-id")
        .values("id", "kind", "query", "result_count", "product_id", "user_id", "cart_token", "created_at")[:limit_rows]
    )
    # Oldest first: a search precedes the view it caused. Two events can share a timestamp (some clocks tick
    # coarser than a request), so the id, which only grows, keeps the order they were recorded in.
    events.sort(key=lambda e: (e["created_at"], e["id"]))

    pending: dict[tuple, list] = defaultdict(list)
    hits: dict[str, dict] = {}
    zero: dict[str, int] = defaultdict(int)
    for e in events:
        actor = (e["user_id"], e["cart_token"])
        if e["kind"] == Kind.SEARCH:
            query = (e["query"] or "").strip().lower()
            if not query:
                continue
            if not e["result_count"]:
                zero[query] += 1
            pending[actor].append((e["created_at"], query))
            continue
        for at, query in reversed(pending.get(actor, [])):
            if e["created_at"] - at <= window:
                row = hits.setdefault(query, {"query": query, "searches": 0, "product_views": 0})
                row["product_views"] += 1
                break
    for actor_queries in pending.values():
        for _at, query in actor_queries:
            if query in hits:
                hits[query]["searches"] += 1

    payload = {
        "period": period_name,
        "store_queries": sorted(hits.values(), key=lambda r: (-r["product_views"], r["query"]))[:limit],
        "market_zero_result_queries": [
            {"query": q, "searches": n} for q, n in sorted(zero.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
        ],
        "scanned_events": len(events),
        "truncated": len(events) >= limit_rows,
    }
    cache.set(cache_key, payload, SEARCH_CACHE_SECONDS)
    return payload


# --------------------------------------------------------------------------- #
# Customers
# --------------------------------------------------------------------------- #


def customers(store, period_name: str, *, limit: int = 10) -> dict:
    p = period_range(period_name)
    now = timezone.now()
    everyone = customers_svc.all_customers(store)
    in_period = [c for c in everyone.values() if c.last_order_at and p.start <= c.last_order_at < p.end]
    repeat = [c for c in everyone.values() if c.orders_count >= 2]
    orders_total = sum(c.orders_count for c in everyone.values())
    spend_total = sum(c.total_spent_cents for c in everyone.values())

    monthly: dict = defaultdict(lambda: {"new": 0.0, "returning": 0.0})
    for c in everyone.values():
        if c.first_order_at:
            monthly[c.first_order_at.strftime("%Y-%m")]["new"] += 1
        for year, month in sorted(c.order_months)[1:]:
            monthly[f"{year}-{month:02d}"]["returning"] += 1

    top = sorted(everyone.values(), key=lambda c: c.total_spent_cents, reverse=True)[:limit]
    return {
        "period": period_name,
        "customers_total": len(everyone),
        "active_in_period": len(in_period),
        "repeat_customers": len(repeat),
        "repeat_rate": _ratio(len(repeat), len(everyone)),
        "orders_per_customer": round(orders_total / len(everyone), 2) if everyone else 0.0,
        "spend_per_customer_cents": round(spend_total / len(everyone)) if everyone else 0,
        "monthly": [{"label": label, "values": values} for label, values in sorted(monthly.items())],
        "top_customers": [
            {"key": c.key, "name": c.name, "email": c.email, "orders": c.orders_count,
             "total_spent_cents": c.total_spent_cents, "last_order_at": c.last_order_at, "type": c.type(now)}
            for c in top
        ],
    }


# --------------------------------------------------------------------------- #
# Fulfilment
# --------------------------------------------------------------------------- #


def _timing(pairs) -> dict:
    """Median and average hours over (start, end) pairs."""
    gaps = sorted((end - start).total_seconds() / 3600 for start, end in pairs if end >= start)
    if not gaps:
        return {"orders": 0, "median_hours": None, "average_hours": None}
    mid = len(gaps) // 2
    median = gaps[mid] if len(gaps) % 2 else (gaps[mid - 1] + gaps[mid]) / 2
    return {"orders": len(gaps), "median_hours": round(median, 1), "average_hours": round(sum(gaps) / len(gaps), 1)}


def _first_event_times(store, order_ids, statuses) -> dict:
    """{(order_id, status): first time this store moved that order there}."""
    firsts: dict = {}
    rows = (
        OrderStatusEvent.objects.filter(store=store, order_id__in=order_ids, status__in=statuses)
        .order_by("created_at").values("order_id", "status", "created_at")
    )
    for row in rows:
        firsts.setdefault((row["order_id"], row["status"]), row["created_at"])
    return firsts


def fulfilment(store, period_name: str) -> dict:
    """Timings are measured from the moment the buyer paid, not from a per-store
    `pending` event — that state is implicit at checkout and never written."""
    p = period_range(period_name)
    items = _store_items(store).filter(order__placed_at__gte=p.start, order__placed_at__lt=p.end)
    by_status = dict(items.values_list("status").annotate(n=Count("order_id", distinct=True)).values_list("status", "n"))
    placed = dict(items.values_list("order_id", "order__placed_at"))
    events = _first_event_times(store, list(placed), (Fulfilment.SHIPPED, Fulfilment.DELIVERED))
    to_shipped = [(placed[oid], at) for (oid, status), at in events.items() if status == Fulfilment.SHIPPED and oid in placed]
    to_delivered = [
        (events[(oid, Fulfilment.SHIPPED)], at)
        for (oid, status), at in events.items()
        if status == Fulfilment.DELIVERED and (oid, Fulfilment.SHIPPED) in events
    ]
    orders = len(placed)
    return {
        "period": period_name,
        "orders": orders,
        "by_status": {s.value: by_status.get(s.value, 0) for s in Fulfilment},
        "to_shipped": _timing(to_shipped),
        "to_delivered": _timing(to_delivered),
        "cancellation_rate": _ratio(by_status.get(Fulfilment.CANCELLED, 0), orders),
        "return_rate": _ratio(by_status.get(Fulfilment.RETURNED, 0), orders),
    }


# --------------------------------------------------------------------------- #
# Sales Analytics
# --------------------------------------------------------------------------- #


def _discounts_cents(store, start, end) -> int:
    """The store's pro-rata share of every voucher in the period (Q49)."""
    orders = (
        Order.objects.filter(items__store=store, placed_at__gte=start, placed_at__lt=end, discount_cents__gt=0)
        .exclude(status=Status.PENDING_PAYMENT).distinct()
    )
    return sum(store_discount_cents(o, store.pk) for o in orders)


_PAID = [Order.OrderPaymentStatus.PAID, Order.OrderPaymentStatus.PARTIALLY_REFUNDED, Order.OrderPaymentStatus.REFUNDED]


def _order_money(store, start, end) -> dict:
    """{order_id: figures} for the paid orders this store took in a window: what its lines were worth
    (`gross`, whatever became of them), its share of the voucher (`discount`), and how much went back
    (`refund`: its cancelled and returned lines, less their own share of that voucher, so a fully refunded
    order nets to exactly zero)."""
    lines = OrderItem.objects.filter(
        store=store, order__placed_at__gte=start, order__placed_at__lt=end, order__payment_status__in=_PAID,
    ).values_list("order_id", "order__placed_at", "order__discount_cents", "status", "line_total_cents")
    per: dict = {}
    for order_id, placed, order_discount, status, cents in lines:
        o = per.setdefault(order_id, {"placed": placed, "discounted": order_discount > 0, "gross": 0, "returned": 0})
        o["gross"] += cents
        if status in (OrderItem.FulfilmentStatus.CANCELLED, OrderItem.FulfilmentStatus.RETURNED):
            o["returned"] += cents
    shares = {}
    discounted = [oid for oid, o in per.items() if o["discounted"]]
    if discounted:
        for order in Order.objects.filter(pk__in=discounted):
            shares[order.pk] = store_discount_cents(order, store.pk)
    for oid, o in per.items():
        share = shares.get(oid, 0)
        o["discount"] = share
        # the refunded lines' slice of the discount, rounded half up, in integers
        back = (2 * share * o["returned"] + o["gross"]) // (2 * o["gross"]) if o["gross"] else 0
        o["refund"] = o["returned"] - back
    return per


def _adjustments(store, start, end) -> list[tuple]:
    """(created_at, signed cents) for staff adjustments in the window: + credits the seller, - debits."""
    return list(
        LedgerEntry.objects.filter(store=store, type=LedgerEntry.Type.ADJUSTMENT, created_at__gte=start, created_at__lt=end)
        .exclude(status=LedgerEntry.Status.FAILED).values_list("created_at", "amount_cents")
    )


def sales(store, period_name: str, *, bucket: str = "daily") -> dict:
    """The designer's revenue view: gross, what was taken off it, and what is left.

    Their *net* is gross − discounts − refunds − adjustments, which is a different
    quantity from `overview.net_sales_cents` (gross − cancelled/returned lines).
    Both are correct; they answer different questions, so both keep their own name.
    """
    p = period_range(period_name)

    def window(start, end):
        orders = _order_money(store, start, end)
        adjustments = _adjustments(store, start, end)
        gross = sum(o["gross"] for o in orders.values())
        discounts = sum(o["discount"] for o in orders.values())
        refunds = sum(o["refund"] for o in orders.values())
        adj = sum(amount for _, amount in adjustments)
        return {"gross": gross, "discounts": discounts, "refunds": refunds, "adjustments": adj,
                "net": gross - discounts - refunds + adj, "orders": orders, "adjustment_rows": adjustments}

    cur, prev = window(p.start, p.end), window(p.prev_start, p.prev_end)

    # Gross and net per bucket, computed from the same per-order figures as the totals so the chart
    # adds up to the cards (scaling by a single ratio would smear a refund across days it didn't happen on).
    buckets: dict = defaultdict(lambda: {"gross": 0.0, "net": 0.0})
    for o in cur["orders"].values():
        point = buckets[_bucket_label(timezone.localtime(o["placed"]), bucket)]
        point["gross"] += o["gross"]
        point["net"] += o["gross"] - o["discount"] - o["refund"]
    for created, amount in cur["adjustment_rows"]:
        buckets[_bucket_label(timezone.localtime(created), bucket)]["net"] += amount
    revenue_over_time = [{"label": label, "values": {k: round(v, 2) for k, v in values.items()}} for label, values in sorted(buckets.items())]

    channels = dict(
        _sold_items(store).filter(order__placed_at__gte=p.start, order__placed_at__lt=p.end)
        .values_list("order__channel").annotate(s=Sum("line_total_cents")).values_list("order__channel", "s")
    )
    total_channel = sum(channels.values()) or 0
    return {
        "period": period_name,
        "bucket": bucket,
        "gross_sales_cents": _kpi(cur["gross"], prev["gross"]),
        "discounts_cents": _kpi(cur["discounts"], prev["discounts"]),
        "refunds_cents": _kpi(cur["refunds"], prev["refunds"]),
        "adjustments_cents": _kpi(cur["adjustments"], prev["adjustments"]),
        "net_sales_cents": _kpi(cur["net"], prev["net"]),
        "revenue_over_time": revenue_over_time,
        "revenue_breakdown": [
            {"label": "Gross Sales", "amount_cents": cur["gross"]},
            {"label": "Discounts", "amount_cents": -cur["discounts"]},
            {"label": "Refunds", "amount_cents": -cur["refunds"]},
            {"label": "Adjustments", "amount_cents": cur["adjustments"]},
            {"label": "Net Sales", "amount_cents": cur["net"]},
        ],
        "sales_by_channel": [
            {"channel": channel, "revenue_cents": value, "share_pct": _ratio(value, total_channel)}
            for channel, value in sorted(channels.items(), key=lambda kv: -kv[1])
        ],
        "sales_by_category": _sales_by_category(store, p.start, p.end),
        "refunds_tracked": REFUNDS_TRACKED,
    }


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #

REPORTS = {
    "overview": overview,
    "sales": sales,
    "funnel": funnel,
    "products": products,
    "search": search_terms,
    "customers": customers,
    "fulfilment": fulfilment,
}


def _is_kpi(value) -> bool:
    return isinstance(value, dict) and {"value", "previous", "change_pct"} <= set(value)


def export(store, report: str, period_name: str):
    """Flat CSV of one report — the rows a seller would paste into a spreadsheet."""
    from .inventory import csv_response

    data = REPORTS[report](store, period_name)
    if report == "products":
        header = ["section", "product", "status", "stock_status", "views", "units", "orders", "revenue", "conversion_%"]
        rows = [
            [section, r["name"], r["status"], r["stock_status"], r["views"], r["units"], r["orders"], r["revenue_cents"] / 100, r["conversion_rate"]]
            for section, key in (("top", "top"), ("slow_mover", "slow_movers"), ("low_performer", "low_performers")) for r in data[key]
        ]
    elif report == "funnel":
        header = ["step", "count", "previous", "change_%", "from_previous_%", "drop_off_%"]
        rows = [[s["step"], s["count"]["value"], s["count"]["previous"], s["count"]["change_pct"], s["from_previous_pct"], s["drop_off_pct"]] for s in data["steps"]]
    elif report == "search":
        header = ["section", "query", "searches", "product_views"]
        rows = [["store_query", r["query"], r["searches"], r["product_views"]] for r in data["store_queries"]]
        rows += [["market_zero_result", r["query"], r["searches"], 0] for r in data["market_zero_result_queries"]]
    elif report == "customers":
        header = ["name", "email", "orders", "total_spent", "type", "last_order_at"]
        rows = [[c["name"], c["email"], c["orders"], c["total_spent_cents"] / 100, c["type"], c["last_order_at"]] for c in data["top_customers"]]
    elif report == "fulfilment":
        header = ["metric", "value"]
        rows = [["orders", data["orders"]], ["cancellation_rate_%", data["cancellation_rate"]], ["return_rate_%", data["return_rate"]]]
        rows += [[f"{stage}_{k}", v] for stage in ("to_shipped", "to_delivered") for k, v in data[stage].items()]
        rows += [[f"status_{k}", v] for k, v in data["by_status"].items()]
    elif report == "sales":
        header = ["metric", "value", "previous", "change_%"]
        rows = [[k, v["value"], v["previous"], v["change_pct"]] for k, v in data.items() if _is_kpi(v)]
        rows += [[f"breakdown_{step['label']}", step["amount_cents"] / 100, "", ""] for step in data["revenue_breakdown"]]
        rows += [[f"channel_{c['channel']}", c["revenue_cents"] / 100, "", c["share_pct"]] for c in data["sales_by_channel"]]
    else:  # overview: every KPI as one row
        header = ["metric", "value", "previous", "change_%"]
        rows = [[k, v["value"], v["previous"], v["change_pct"]] for k, v in data.items() if _is_kpi(v)]
        rows += [[f"customers_{k}", v, "", ""] for k, v in data["customer_snapshot"].items()]
    return csv_response(f"{report}-{period_name}.csv", header, rows)
