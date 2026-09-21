"""Seller Dashboard (home) and the header "Search anything" (docs/sprint-3-plan.md §6).

Definitions:
  sales_cents    this store's line totals on paid, non-cancelled/returned orders **placed** in the period (booked)
  revenue_cents  same lines, for orders this store **delivered** in the period (recognised)
  conversion     store orders placed ÷ product views (ProductEvent) in the period, in %
  aov_cents      sales ÷ orders
"""

from __future__ import annotations

from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncMonth
from django.utils import timezone

from catalog.models import Product
from discovery.models import ProductEvent
from orders.models import Order, OrderItem, OrderStatusEvent

from . import customers as customers_svc
from .orders import row as order_row, store_orders
from .periods import change_pct, month_buckets, period_range
from .products import _sold_items

Status = Order.OrderStatus
PStatus = Product.ProductStatus


def _kpi(cur, prev):
    return {"value": cur, "previous": prev, "change_pct": change_pct(cur, prev)}


def summary(store, user, period_name: str) -> dict:
    p = period_range(period_name)
    sold = _sold_items(store)
    orders = store_orders(store)
    views = ProductEvent.objects.filter(product__store=store, kind=ProductEvent.Kind.VIEW)
    delivered_events = OrderStatusEvent.objects.filter(store=store, status=Status.DELIVERED)

    def sales(a, b):
        return sold.filter(order__placed_at__gte=a, order__placed_at__lt=b).aggregate(s=Sum("line_total_cents"))["s"] or 0

    def revenue(a, b):
        order_ids = delivered_events.filter(created_at__gte=a, created_at__lt=b).values("order_id")
        return sold.filter(order_id__in=order_ids).aggregate(s=Sum("line_total_cents"))["s"] or 0

    def n_orders(a, b):
        return orders.filter(placed_at__gte=a, placed_at__lt=b).count()

    def n_views(a, b):
        return views.filter(created_at__gte=a, created_at__lt=b).count()

    cs, ps = sales(p.start, p.end), sales(p.prev_start, p.prev_end)
    co, po = n_orders(p.start, p.end), n_orders(p.prev_start, p.prev_end)
    cv, pv = n_views(p.start, p.end), n_views(p.prev_start, p.prev_end)
    conv = lambda o, v: round(o / v * 100, 2) if v else 0.0  # noqa: E731
    aov = lambda s, o: round(s / o) if o else 0  # noqa: E731

    products = Product.objects.filter(store=store)
    live = products.filter(status=PStatus.ACTIVE)
    by_stock = dict(live.values_list("stock_status").annotate(n=Count("id")).values_list("stock_status", "n"))

    year = timezone.localtime().year
    series = {m: {"revenue": 0.0, "orders": 0.0} for m in range(1, 13)}
    for r in orders.filter(placed_at__year=year).annotate(m=TruncMonth("placed_at")).values("m").annotate(n=Count("id", distinct=True)):
        series[r["m"].month]["orders"] = float(r["n"])
    for r in delivered_events.filter(created_at__year=year).annotate(m=TruncMonth("created_at")).values("m", "order_id").distinct():
        series[r["m"].month]["revenue"] += float(sold.filter(order_id=r["order_id"]).aggregate(s=Sum("line_total_cents"))["s"] or 0)

    cust = customers_svc.overview(store, period_name)
    return {
        "period": period_name,
        "seller": {"name": user.full_name or user.email, "first_name": (user.full_name or user.email).split()[0], "role": "Store Owner"},
        "store": {"id": store.id, "name": store.display_name, "slug": store.slug, "currency": store.country.currency},
        "sales_cents": _kpi(cs, ps),
        "orders": _kpi(co, po),
        "products": _kpi(live.count(), live.filter(created_at__lt=p.start).count()),
        "new_products": products.filter(created_at__gte=p.start, created_at__lt=p.end).count(),
        "low_stock": by_stock.get(Product.StockStatus.LOW_STOCK, 0),
        "revenue_cents": _kpi(revenue(p.start, p.end), revenue(p.prev_start, p.prev_end)),
        "revenue_series": [{"label": b.strftime("%b"), "values": series[b.month]} for b in month_buckets(year)],
        "performance": {
            "product_views": _kpi(cv, pv),
            "conversion_rate": _kpi(conv(co, cv), conv(po, pv)),
            "aov_cents": _kpi(aov(cs, co), aov(ps, po)),
            "customer_growth": cust["new_customers"],
        },
        "latest_orders": [order_row(o) for o in orders.order_by("-placed_at")[:5]],
        "stock_breakdown": {
            "in_stock": by_stock.get(Product.StockStatus.IN_STOCK, 0) + by_stock.get(Product.StockStatus.PREORDER, 0),
            "low_stock": by_stock.get(Product.StockStatus.LOW_STOCK, 0),
            "out_of_stock": by_stock.get(Product.StockStatus.OUT_OF_STOCK, 0),
        },
    }


def search(store, q: str, limit: int = 5) -> dict:
    q = q.strip()
    prods = Product.objects.filter(store=store).filter(Q(name__icontains=q) | Q(variants__sku__icontains=q)).distinct().order_by("-updated_at")[:limit]
    ords = store_orders(store).filter(Q(order_number__icontains=q) | Q(contact_email__icontains=q) | Q(contact_first_name__icontains=q) | Q(contact_last_name__icontains=q)).order_by("-placed_at")[:limit]
    return {
        "products": [{"id": str(p.id), "label": p.name, "sub": p.status} for p in prods],
        "orders": [{"id": str(o.id), "label": o.order_number, "sub": f"{o.contact_first_name} {o.contact_last_name} · {o.store_status}"} for o in ords],
        "customers": [{"id": c["key"], "label": c["name"], "sub": c["email"]} for c in customers_svc.list_customers(store, q=q)[:limit]],
    }
