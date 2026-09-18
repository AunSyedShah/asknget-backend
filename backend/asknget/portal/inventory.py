"""Seller Inventory: stock per product/variant, adjustments with an audit trail,
CSV export. Rules in docs/sprint-3-plan.md §4."""

from __future__ import annotations

import csv
from datetime import timedelta

from django.db.models import Count, Max, OuterRef, Prefetch, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.http import StreamingHttpResponse
from django.utils import timezone
from ninja.errors import HttpError

from catalog import services as catalog
from catalog.models import Product, ProductImage, ProductVariant, StockMovement
from orders.models import Order, OrderItem

from .products import STOCK_TABS, store_products

Fulfilment = OrderItem.FulfilmentStatus
RESERVED_STATUSES = (Fulfilment.PENDING, Fulfilment.PROCESSING, Fulfilment.PACKED)  # paid, not yet shipped
RECENT_DAYS = 7
SORTS = {"updated": ("-last_movement_at", "-updated_at"), "stock": ("stock_qty", "name"), "stock_desc": ("-stock_qty", "name"), "name": ("name",)}


def inventory_qs(store):
    reserved = (
        OrderItem.objects.filter(store=store, product=OuterRef("pk"), status__in=RESERVED_STATUSES)
        .exclude(order__status=Order.OrderStatus.PENDING_PAYMENT)
        .values("product").annotate(s=Sum("qty")).values("s")[:1]
    )
    variants = ProductVariant.objects.filter(product=OuterRef("pk"), is_active=True).values("product").annotate(n=Count("id")).values("n")[:1]
    last_move = StockMovement.objects.filter(product=OuterRef("pk")).values("product").annotate(m=Max("created_at")).values("m")[:1]
    return (
        store_products(store)
        .exclude(status=Product.ProductStatus.ARCHIVED)
        .prefetch_related(Prefetch("variants", queryset=ProductVariant.objects.prefetch_related("option_values__option_type").order_by("created_at")))
        .annotate(
            reserved_qty=Coalesce(Subquery(reserved), Value(0)),
            variant_count=Coalesce(Subquery(variants), Value(0)),
            last_movement_at=Subquery(last_move),
        )
    )


def list_inventory(store, *, tab: str = "all", q: str | None = None, brand=None, category: str | None = None, sort: str = "updated"):
    qs = inventory_qs(store)
    if tab in STOCK_TABS:
        qs = qs.filter(stock_status__in=STOCK_TABS[tab])
    elif tab == "recent":
        qs = qs.filter(last_movement_at__gte=timezone.now() - timedelta(days=RECENT_DAYS))
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(variants__sku__icontains=q)).distinct()
    if brand:
        qs = qs.filter(brand__slug__in=brand)
    if category:
        qs = qs.filter(category__in=catalog.category_with_descendants(category))
    return qs.order_by(*SORTS[sort])


def variant_row(v: ProductVariant) -> dict:
    values = sorted(v.option_values.all(), key=lambda ov: ov.option_type.sort_order)
    return {"id": v.id, "sku": v.sku, "option_values": {ov.option_type.name: ov.value for ov in values}, "stock_qty": v.stock_qty, "is_active": v.is_active}


def row(p: Product) -> dict:
    images = list(p.images.all())
    primary = next((i for i in images if i.is_primary), images[0] if images else None)
    return {
        "id": p.id, "name": p.name, "slug": p.slug, "image_url": primary.image.url if primary else None,
        "category": p.category.name if p.category_id else None, "brand": p.brand.name if p.brand_id else None,
        "on_hand": p.stock_qty, "reserved": p.reserved_qty, "available": p.stock_qty,
        "variant_count": p.variant_count, "stock_status": p.stock_status, "status": p.status,
        "low_stock_threshold": p.effective_low_stock_threshold(),
        "last_updated": p.last_movement_at or p.updated_at,
        "variants": [variant_row(v) for v in p.variants.all() if v.is_active],
    }


def kpis(store) -> dict:
    qs = inventory_qs(store)
    by_status = dict(qs.values_list("stock_status").annotate(n=Count("id")).values_list("stock_status", "n"))
    low = by_status.get(Product.StockStatus.LOW_STOCK, 0)
    out = by_status.get(Product.StockStatus.OUT_OF_STOCK, 0)
    recent = qs.filter(last_movement_at__gte=timezone.now() - timedelta(days=RECENT_DAYS)).count()
    total = qs.count()
    return {
        "total_products": total,
        "total_variants": ProductVariant.objects.filter(product__store=store, is_active=True).exclude(product__status=Product.ProductStatus.ARCHIVED).count(),
        "total_units": ProductVariant.objects.filter(product__store=store, is_active=True).exclude(product__status=Product.ProductStatus.ARCHIVED).aggregate(n=Sum("stock_qty"))["n"] or 0,
        "out_of_stock": out, "low_stock": low,
        "tabs": {"all": total, "low": low, "out": out, "recent": recent},
    }


def get_variant(store, variant_id) -> ProductVariant:
    v = ProductVariant.objects.select_related("product__store").filter(pk=variant_id, product__store=store).first()
    if v is None:
        raise HttpError(404, "Variant not found")
    return v


def adjust(store, variant_id, *, set_to: int | None, delta: int | None, note: str, actor) -> StockMovement:
    v = get_variant(store, variant_id)
    if delta == 0 or (set_to is not None and set_to == v.stock_qty):
        raise HttpError(422, "Nothing to change")
    return catalog.adjust_stock(v, set_to=set_to, delta=delta, reason=StockMovement.Reason.MANUAL, actor_user=actor, note=note)


def movements(store, variant_id, limit: int = 50):
    v = get_variant(store, variant_id)
    return StockMovement.objects.filter(variant=v).select_related("actor_user", "order").order_by("-created_at")[:limit]


def movement_out(m: StockMovement) -> dict:
    return {
        "id": m.id, "delta": m.delta, "qty_after": m.qty_after, "reason": m.reason, "note": m.note,
        "order_number": m.order.order_number if m.order_id else None,
        "actor": (m.actor_user.full_name or m.actor_user.email) if m.actor_user_id else None, "created_at": m.created_at,
    }


class _Echo:
    def write(self, value):
        return value


def csv_response(filename: str, header: list[str], rows) -> StreamingHttpResponse:
    writer = csv.writer(_Echo())

    def stream():
        yield writer.writerow(header)
        for r in rows:
            yield writer.writerow(r)

    res = StreamingHttpResponse(stream(), content_type="text/csv; charset=utf-8")
    res["Content-Disposition"] = f'attachment; filename="{filename}"'
    return res


def export(store) -> StreamingHttpResponse:
    def rows():
        for p in list_inventory(store):
            for v in p.variants.all():
                if v.is_active:
                    opts = " | ".join(f"{k}: {val}" for k, val in variant_row(v)["option_values"].items())
                    yield [p.name, v.sku, opts, p.category.name if p.category_id else "", p.brand.name if p.brand_id else "",
                           v.stock_qty, p.stock_status, p.status, (p.last_movement_at or p.updated_at).isoformat()]

    return csv_response("inventory.csv", ["product", "sku", "options", "category", "brand", "stock_qty", "stock_status", "status", "last_updated"], rows())
