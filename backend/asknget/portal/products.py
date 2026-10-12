"""Seller Products: list/KPIs, the 5-step wizard (one PATCH + sub-resources), actions,
per-product analytics, and the staff review queue. Rules in docs/sprint-3-plan.md §2."""

from __future__ import annotations

import itertools
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Count, F, OuterRef, Prefetch, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone
from django.utils.text import slugify
from ninja.errors import HttpError

from catalog import services as catalog
from catalog.models import (
    Brand,
    Category,
    Product,
    ProductContentSection,
    ProductImage,
    ProductOptionType,
    ProductOptionValue,
    ProductVariant,
    WishlistItem,
)
from core.models import coerce_nulls
from core.uploads import safe_image
from discovery.models import ProductEvent
from orders.models import Order, OrderItem

from .periods import Period, change_pct, period_range

PStatus = Product.ProductStatus
Fulfilment = OrderItem.FulfilmentStatus
SOLD_STATUSES_EXCLUDED = (Fulfilment.CANCELLED, Fulfilment.RETURNED)
IMAGE_TYPES = {"image/jpeg", "image/png"}   # Media popup says PNG, JPG
MAX_IMAGES = 8
TABS = ("active", "pending", "draft", "rejected", "archived")

# --------------------------------------------------------------------------- #
# Querysets
# --------------------------------------------------------------------------- #


def _sold_items(store):
    """This store's order lines that count as sales (paid orders, not cancelled/returned)."""
    return OrderItem.objects.filter(store=store).exclude(status__in=SOLD_STATUSES_EXCLUDED).exclude(order__status=Order.OrderStatus.PENDING_PAYMENT)


def _subquery_sum(qs, field):
    return Coalesce(Subquery(qs.values("product").annotate(s=Sum(field)).values("s")[:1]), Value(0))


def store_products(store):
    sold = _sold_items(store).filter(product=OuterRef("pk"))
    stock = ProductVariant.objects.filter(product=OuterRef("pk"), is_active=True)
    return (
        Product.objects.filter(store=store)
        .select_related("category", "brand", "store")  # store: effective_low_stock_threshold()
        .prefetch_related(Prefetch("images", queryset=ProductImage.objects.order_by("-is_primary", "sort_order")))
        .annotate(
            sold_qty=_subquery_sum(sold, "qty"),
            earned_cents=_subquery_sum(sold, "line_total_cents"),
            stock_qty=Coalesce(Subquery(stock.values("product").annotate(s=Sum("stock_qty")).values("s")[:1]), Value(0)),
        )
    )


SORTS = {
    "updated": ("-updated_at",), "created": ("-created_at",), "price": ("price_cents", "name"),
    "price_desc": ("-price_cents", "name"), "sold": ("-sold_qty", "-updated_at"), "name": ("name",),
}
STOCK_TABS = {"in": (Product.StockStatus.IN_STOCK, Product.StockStatus.PREORDER), "low": (Product.StockStatus.LOW_STOCK,), "out": (Product.StockStatus.OUT_OF_STOCK,)}


def list_products(store, f):
    qs = store_products(store)
    if f.status != "all":
        qs = qs.filter(status=f.status)
    if f.q:
        qs = qs.filter(Q(name__icontains=f.q) | Q(variants__sku__icontains=f.q) | Q(tags__icontains=f.q)).distinct()
    if f.brand:
        qs = qs.filter(brand__slug__in=f.brand)
    if f.category:
        qs = qs.filter(category__in=catalog.category_with_descendants(f.category))
    if f.stock:
        qs = qs.filter(stock_status__in=STOCK_TABS[f.stock])
    return qs.order_by(*SORTS[f.sort])


def row(p: Product) -> dict:
    images = list(p.images.all())
    primary = next((i for i in images if i.is_primary), images[0] if images else None)
    return {
        "id": p.id, "name": p.name, "slug": p.slug, "image_url": primary.image.url if primary else None,
        "category": p.category.name if p.category_id else None, "brand": p.brand.name if p.brand_id else None,
        "stock_qty": p.stock_qty, "stock_status": p.stock_status, "updated_at": p.updated_at,
        "price_cents": p.price_cents, "compare_at_price_cents": p.compare_at_price_cents, "currency": p.currency,
        "sold_qty": p.sold_qty, "earned_cents": p.earned_cents, "status": p.status,
    }


def tabs(store) -> dict:
    counts = dict(Product.objects.filter(store=store).values_list("status").annotate(n=Count("id")).values_list("status", "n"))
    out = {t: counts.get(t, 0) for t in TABS}
    out["all"] = sum(out.values())
    return out


def _kpi(current, previous) -> dict:
    return {"value": current, "previous": previous, "change_pct": change_pct(current, previous)}


def kpis(store, period_name: str) -> dict:
    p = period_range(period_name)
    products = Product.objects.filter(store=store)
    sold = _sold_items(store)
    returned = OrderItem.objects.filter(store=store, status=Fulfilment.RETURNED)

    def in_(qs, field, start, end):
        return qs.filter(**{f"{field}__gte": start, f"{field}__lt": end})

    def units(qs, start, end):
        return in_(qs, "order__placed_at", start, end).aggregate(n=Sum("qty"))["n"] or 0

    def returned_units(start, end):
        return in_(returned, "updated_at", start, end).aggregate(n=Sum("qty"))["n"] or 0

    return {
        "period": period_name,
        "total_products": _kpi(products.exclude(status=PStatus.ARCHIVED).count(), products.filter(created_at__lt=p.start).exclude(status=PStatus.ARCHIVED).count()),
        "new_added": _kpi(in_(products, "created_at", p.start, p.end).count(), in_(products, "created_at", p.prev_start, p.prev_end).count()),
        "sold_qty": _kpi(units(sold, p.start, p.end), units(sold, p.prev_start, p.prev_end)),
        "returned_qty": _kpi(returned_units(p.start, p.end), returned_units(p.prev_start, p.prev_end)),
        "tabs": tabs(store),
    }


# --------------------------------------------------------------------------- #
# Wizard
# --------------------------------------------------------------------------- #


def get_product(store, product_id) -> Product:
    product = (
        store_products(store)
        .prefetch_related(
            Prefetch("option_types", queryset=ProductOptionType.objects.prefetch_related("values")),
            Prefetch("variants", queryset=ProductVariant.objects.prefetch_related("option_values__option_type").order_by("created_at")),
            "content_sections",
        )
        .filter(pk=product_id).first()
    )
    if product is None:
        raise HttpError(404, "Product not found")
    return product


def _unique_slug(name: str, *, exclude_pk=None) -> str:
    base = slugify(name)[:200] or "product"
    slug, n = base, 2
    while Product.all_objects.filter(slug=slug).exclude(pk=exclude_pk).exists():
        slug = f"{base}-{n}"
        n += 1
    return slug


def _validate_refs(data: dict) -> None:
    if data.get("category_id") and not Category.objects.filter(pk=data["category_id"], is_active=True).exists():
        raise HttpError(422, "Unknown category")
    if data.get("brand_id") and not Brand.objects.filter(pk=data["brand_id"], is_active=True).exists():
        raise HttpError(422, "Unknown brand")


@transaction.atomic
def create(store, data: dict) -> Product:
    _validate_refs(data)
    product = Product.objects.create(
        store=store, slug=_unique_slug(data["name"]), price_cents=0, country=store.country, currency=store.country.currency,
        stock_status=Product.StockStatus.OUT_OF_STOCK, **coerce_nulls(Product, data),
    )
    # One default variant until options are defined, so pricing/stock always has a home.
    ProductVariant.objects.create(product=product, sku=_unique_sku(product, 1), stock_qty=0)
    return product


@transaction.atomic
def patch(product: Product, data: dict) -> Product:
    if product.status == PStatus.PENDING:
        raise HttpError(409, "This product is under review; withdraw it (unpublish) to edit")
    _validate_refs(data)
    preorder = data.pop("preorder", None)
    if "compare_at_price_cents" in data and data["compare_at_price_cents"] == 0:
        data["compare_at_price_cents"] = None
    if "price_cents" in data and "compare_at_price_cents" not in data and product.compare_at_price_cents and product.compare_at_price_cents <= data["price_cents"]:
        data["compare_at_price_cents"] = None  # keep the DB constraint happy; 'was' price no longer applies
    price = data.get("price_cents", product.price_cents)
    compare = data.get("compare_at_price_cents", product.compare_at_price_cents)
    if compare is not None and compare <= price:
        raise HttpError(422, "Compare-at price must be greater than the price")
    if "currency" in data:
        data["currency"] = data["currency"].upper()
    for k, v in coerce_nulls(Product, data).items():
        setattr(product, k, v)
    if preorder is not None:
        product.stock_status = Product.StockStatus.PREORDER if preorder else Product.StockStatus.OUT_OF_STOCK
    product.save()
    catalog.refresh_stock_status(product)
    return product


def _combo_key(values) -> frozenset:
    return frozenset((v.option_type.name.lower(), v.value.lower()) for v in values)


@transaction.atomic
def set_options(product: Product, options: list) -> Product:
    """Replace option types/values and regenerate variants for every combination.
    Variants whose combination survives keep sku/price/stock; orphans are deleted
    (or deactivated when an order references them)."""
    if product.status == PStatus.PENDING:
        raise HttpError(409, "This product is under review")
    existing = {
        _combo_key(v.option_values.all()): v
        for v in product.variants.prefetch_related("option_values__option_type")
    }
    product.option_types.all().delete()
    value_objs: list[list[ProductOptionValue]] = []
    for i, opt in enumerate(options):
        ot = ProductOptionType.objects.create(product=product, name=opt.name, sort_order=i)
        value_objs.append([ProductOptionValue.objects.create(option_type=ot, value=v.value, swatch_hex=v.swatch_hex, sort_order=j) for j, v in enumerate(opt.values)])
    combos = list(itertools.product(*value_objs)) if value_objs else [()]
    kept, links = set(), []
    Link = ProductVariant.option_values.through
    for n, combo in enumerate(combos, start=1):
        key = frozenset((v.option_type.name.lower(), v.value.lower()) for v in combo)
        variant = existing.get(key)
        if variant is None:
            variant = ProductVariant.objects.create(product=product, sku=_unique_sku(product, n), stock_qty=0)
        # Old option rows were cascade-deleted above, so every surviving variant needs re-linking.
        links += [Link(productvariant=variant, productoptionvalue=v) for v in combo]
        if not variant.is_active:
            variant.is_active = True
            variant.save(update_fields=["is_active", "updated_at"])
        kept.add(variant.pk)
    Link.objects.bulk_create(links)  # one INSERT instead of a SELECT+INSERT per variant
    for variant in product.variants.exclude(pk__in=kept):
        if variant.order_items.exists():
            variant.is_active = False
            variant.save(update_fields=["is_active", "updated_at"])
        else:
            variant.delete()
    catalog.refresh_stock_status(product)
    return product


def _unique_sku(product: Product, n: int) -> str:
    base = product.slug.upper()[:40]
    sku = f"{base}-{n}"
    while ProductVariant.objects.filter(sku=sku).exists():
        n += 1
        sku = f"{base}-{n}"
    return sku


@transaction.atomic
def patch_variant(product: Product, variant_id, data: dict, *, actor) -> ProductVariant:
    variant = product.variants.filter(pk=variant_id).first()
    if variant is None:
        raise HttpError(404, "Variant not found")
    stock = data.pop("stock_qty", None)
    if "sku" in data and ProductVariant.objects.filter(sku=data["sku"]).exclude(pk=variant.pk).exists():
        raise HttpError(409, "SKU already in use")
    if data.get("price_cents") == 0:
        data["price_cents"] = None
    price = data.get("price_cents", variant.price_cents)
    compare = data.get("compare_at_price_cents", variant.compare_at_price_cents)
    effective = price if price is not None else product.price_cents
    if compare is not None and compare <= effective:
        raise HttpError(422, "Compare-at price must be greater than the price")
    for k, v in data.items():
        setattr(variant, k, v)
    variant.save()
    if stock is not None and stock != variant.stock_qty:
        catalog.adjust_stock(variant, set_to=stock, reason="manual", actor_user=actor, note="Set in product wizard")
    else:
        catalog.refresh_stock_status(product)
    return variant


def add_image(product: Product, file, alt: str = "") -> ProductImage:
    if file.content_type not in IMAGE_TYPES:
        raise HttpError(422, "Images must be JPEG, PNG, WebP or GIF")
    if file.size > settings.PRODUCT_IMAGE_MAX_BYTES:
        raise HttpError(422, "Each image must be 5 MB or smaller")
    count = product.images.count()
    if count >= MAX_IMAGES:
        raise HttpError(422, f"At most {MAX_IMAGES} images")
    content = safe_image(file, max_bytes=settings.PRODUCT_IMAGE_MAX_BYTES)     # real format, server-chosen name
    image = ProductImage(product=product, alt=alt[:200], sort_order=count, is_primary=count == 0)
    image.image.save(content.name, content, save=True)
    return image


@transaction.atomic
def reorder_images(product: Product, image_ids: list) -> None:
    images = {i.pk: i for i in product.images.all()}
    if set(image_ids) != set(images):
        raise HttpError(422, "image_ids must list every image of this product exactly once")
    product.images.update(is_primary=False)
    for n, pk in enumerate(image_ids):
        ProductImage.objects.filter(pk=pk).update(sort_order=n, is_primary=n == 0)


@transaction.atomic
def delete_image(product: Product, image_id) -> None:
    image = product.images.filter(pk=image_id).first()
    if image is None:
        raise HttpError(404, "Image not found")
    was_primary = image.is_primary
    image.delete()
    if was_primary:
        first = product.images.order_by("sort_order").first()
        if first:
            first.is_primary = True
            first.save(update_fields=["is_primary", "updated_at"])


@transaction.atomic
def set_content(product: Product, sections: list) -> None:
    product.content_sections.all().delete()
    counters = {"benefit": 0, "usage_step": 0}
    for s in sections:
        ProductContentSection.objects.create(product=product, section=s.section, title=s.title, body=s.body, sort_order=counters[s.section])
        counters[s.section] += 1


# --------------------------------------------------------------------------- #
# Completion / status / actions
# --------------------------------------------------------------------------- #


def completion(product: Product) -> dict:
    missing = []
    basic = bool(product.name) and product.category_id is not None
    if not product.name:
        missing.append("name")
    if product.category_id is None:
        missing.append("category")
    images = list(product.images.all())
    media = len(images) > 0
    if not media:
        missing.append("image")
    variants = [v for v in product.variants.all() if v.is_active]
    pricing = product.price_cents > 0 and len(variants) > 0
    if product.price_cents <= 0:
        missing.append("price")
    if not variants:
        missing.append("variant")
    if variants and product.stock_status != Product.StockStatus.PREORDER and sum(v.stock_qty for v in variants) <= 0:
        missing.append("stock")
    shipping = product.weight_grams is not None or bool(product.ships_from)
    return {
        "basic_info": basic, "media": media, "pricing": pricing and "stock" not in missing, "shipping": shipping,
        "ready_to_submit": not missing, "missing": missing,
    }


@transaction.atomic
def change_status(product: Product, action: str) -> Product:
    now = timezone.now()
    if action == "submit":
        if product.status not in (PStatus.DRAFT, PStatus.REJECTED):
            raise HttpError(409, f"Only draft or rejected products can be submitted (this one is {product.status})")
        missing = completion(product)["missing"]
        if missing:
            raise HttpError(422, "Complete the product first: missing " + ", ".join(missing))
        product.status, product.submitted_at, product.rejection_reason = PStatus.PENDING, now, ""
    elif action == "unpublish":
        if product.status not in (PStatus.ACTIVE, PStatus.PENDING):
            raise HttpError(409, "Only active or pending products can be unpublished")
        product.status = PStatus.DRAFT
    elif action == "archive":
        if product.status == PStatus.ARCHIVED:
            raise HttpError(409, "Already archived")
        product.status = PStatus.ARCHIVED
    elif action == "unarchive":
        if product.status != PStatus.ARCHIVED:
            raise HttpError(409, "Not archived")
        product.status = PStatus.ACTIVE if product.published_at else PStatus.DRAFT
    product.save(update_fields=["status", "submitted_at", "rejection_reason", "updated_at"])
    return product


@transaction.atomic
def duplicate(product: Product) -> Product:
    name = f"{product.name} (copy)"
    fields = {
        f.attname: getattr(product, f.attname) for f in Product._meta.fields  # attname → FK ids, no extra queries
        if f.name not in ("id", "slug", "name", "status", "published_at", "submitted_at", "rejection_reason",
                          "rating_avg", "rating_count", "created_at", "updated_at", "deleted_at", "stock_status",
                          "is_featured", "is_best_seller", "is_new_arrival")
    }
    copy = Product.objects.create(name=name, slug=_unique_slug(name), status=PStatus.DRAFT, stock_status=Product.StockStatus.OUT_OF_STOCK, **fields)
    value_map = {}
    for ot in product.option_types.all():
        new_ot = ProductOptionType.objects.create(product=copy, name=ot.name, sort_order=ot.sort_order)
        for v in ot.values.all():
            value_map[v.pk] = ProductOptionValue.objects.create(option_type=new_ot, value=v.value, swatch_hex=v.swatch_hex, sort_order=v.sort_order)
    Link = ProductVariant.option_values.through
    links = []
    for n, v in enumerate(product.variants.filter(is_active=True).prefetch_related("option_values"), start=1):
        nv = ProductVariant.objects.create(product=copy, sku=_unique_sku(copy, n), price_cents=v.price_cents, compare_at_price_cents=v.compare_at_price_cents, stock_qty=0)
        links += [Link(productvariant=nv, productoptionvalue=value_map[ov.pk]) for ov in v.option_values.all()]
    Link.objects.bulk_create(links)
    for s in product.content_sections.all():
        ProductContentSection.objects.create(product=copy, section=s.section, title=s.title, body=s.body, sort_order=s.sort_order)
    return copy


def delete(product: Product) -> None:
    if product.status == PStatus.ACTIVE:
        raise HttpError(409, "Archive the product before deleting it")
    product.soft_delete()


# --------------------------------------------------------------------------- #
# Serialisation
# --------------------------------------------------------------------------- #


def delivery_options(store):
    return store.delivery_options.all()


def create_delivery_option(store, data: dict):
    from sellers.models import StoreDeliveryOption

    if StoreDeliveryOption.objects.filter(store=store, name__iexact=data["name"]).exists():
        raise HttpError(409, "You already have an option with that name")
    return StoreDeliveryOption.objects.create(store=store, **data)


def patch_delivery_option(store, option_id, data: dict):
    option = store.delivery_options.filter(pk=option_id).first()
    if option is None:
        raise HttpError(404, "Delivery option not found")
    for field, value in data.items():
        setattr(option, field, value)
    option.save()
    return option


def delete_delivery_option(store, option_id) -> None:
    """Deactivates rather than deletes once orders reference it, so old orders keep
    their snapshot and products do not silently lose their only option."""
    option = store.delivery_options.filter(pk=option_id).first()
    if option is None:
        raise HttpError(404, "Delivery option not found")
    if store.delivery_options.filter(is_active=True).count() <= 1 and option.is_active:
        raise HttpError(409, "A store needs at least one active delivery option")
    if option.order_deliveries.exists() or option.products.exists():
        option.is_active = False
        option.save(update_fields=["is_active", "updated_at"])
        return
    option.delete()


def option_out(option) -> dict:
    return {
        "id": option.id, "name": option.name, "eta_label": option.eta_label,
        "price_cents": option.price_cents, "kind": option.kind, "is_active": option.is_active,
        "sort_order": option.sort_order, "products_count": option.products.count(),
    }


def set_product_delivery_options(store, product: Product, option_ids: list):
    valid = {str(o.id) for o in store.delivery_options.filter(is_active=True)}
    unknown = [str(i) for i in option_ids if str(i) not in valid]
    if unknown:
        raise HttpError(422, "Unknown or inactive delivery option")
    product.delivery_options.set(list(store.delivery_options.filter(pk__in=option_ids)))
    return product


def serialize(product: Product) -> dict:
    cat_path = []
    node = product.category if product.category_id else None
    while node is not None:
        cat_path.append(node.name)
        node = node.parent
    variants = []
    for v in product.variants.all():
        values = sorted(v.option_values.all(), key=lambda ov: ov.option_type.sort_order)
        variants.append({
            "id": v.id, "sku": v.sku, "option_values": {ov.option_type.name: ov.value for ov in values},
            "price_cents": v.price_cents, "effective_price_cents": v.price_cents if v.price_cents is not None else product.price_cents,
            "compare_at_price_cents": v.compare_at_price_cents, "stock_qty": v.stock_qty, "is_active": v.is_active,
        })
    sections = list(product.content_sections.all())
    return {
        "id": product.id, "name": product.name, "slug": product.slug, "status": product.status, "rejection_reason": product.rejection_reason,
        "category_id": product.category_id, "category_path": list(reversed(cat_path)),
        "brand_id": product.brand_id, "brand": product.brand.name if product.brand_id else None,
        "tags": product.tags or [], "short_description": product.short_description,
        "description_title": product.description_title, "description": product.description,
        "price_cents": product.price_cents, "compare_at_price_cents": product.compare_at_price_cents, "currency": product.currency,
        "cost_price_cents": product.cost_price_cents, "barcode": product.barcode,
        "delivery_option_ids": [o.id for o in product.delivery_options.all()],
        "stock_qty": sum(v.stock_qty for v in product.variants.all() if v.is_active), "stock_status": product.stock_status,
        "low_stock_threshold": product.low_stock_threshold, "effective_low_stock_threshold": product.effective_low_stock_threshold(),
        "weight_grams": product.weight_grams, "length_mm": product.length_mm, "width_mm": product.width_mm, "height_mm": product.height_mm,
        "ships_from": product.ships_from, "handling_days": product.handling_days,
        "is_featured": product.is_featured, "is_new_arrival": product.is_new_arrival,
        "images": [{"id": i.id, "url": i.image.url, "alt": i.alt, "is_primary": i.is_primary, "sort_order": i.sort_order} for i in product.images.all()],
        "option_types": [{"name": ot.name, "values": [{"value": v.value, "swatch_hex": v.swatch_hex} for v in ot.values.all()]} for ot in product.option_types.all()],
        "variants": variants,
        "key_benefits": [{"section": s.section, "title": s.title, "body": s.body} for s in sections if s.section == "benefit"],
        "usage_steps": [{"section": s.section, "title": s.title, "body": s.body} for s in sections if s.section == "usage_step"],
        "completion": completion(product),
        "public_url": f"{settings.STOREFRONT_URL}/products/{product.slug}" if product.status == PStatus.ACTIVE else None,
        "created_at": product.created_at, "updated_at": product.updated_at,
        "published_at": product.published_at, "submitted_at": product.submitted_at,
    }


# --------------------------------------------------------------------------- #
# Analytics ('View Analytics')
# --------------------------------------------------------------------------- #


def analytics(store, product: Product, period_name: str) -> dict:
    p = period_range(period_name)
    views = ProductEvent.objects.filter(product=product, kind=ProductEvent.Kind.VIEW)
    sold = _sold_items(store).filter(product=product)

    def between(qs, field, start, end):
        return qs.filter(**{f"{field}__gte": start, f"{field}__lt": end})

    def stats(start, end):
        v = between(views, "created_at", start, end).count()
        s = between(sold, "order__placed_at", start, end).aggregate(q=Sum("qty"), e=Sum("line_total_cents"), o=Count("order_id", distinct=True))
        return v, s["q"] or 0, s["e"] or 0, s["o"] or 0

    cv, cq, ce, co = stats(p.start, p.end)
    pv, pq, pe, po = stats(p.prev_start, p.prev_end)
    conv = lambda o, v: round(o / v * 100, 2) if v else 0.0  # noqa: E731

    since = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=29)
    by_day = {}
    for r in views.filter(created_at__gte=since).annotate(d=TruncDate("created_at")).values("d").annotate(n=Count("id")):
        by_day.setdefault(str(r["d"]), {"views": 0.0, "sold": 0.0})["views"] = float(r["n"])
    for r in sold.filter(order__placed_at__gte=since).annotate(d=TruncDate("order__placed_at")).values("d").annotate(n=Sum("qty")):
        by_day.setdefault(str(r["d"]), {"views": 0.0, "sold": 0.0})["sold"] = float(r["n"])
    daily = [{"label": str((since + timedelta(days=i)).date()), "values": by_day.get(str((since + timedelta(days=i)).date()), {"views": 0.0, "sold": 0.0})} for i in range(30)]
    return {
        "product_id": product.id, "period": period_name,
        "views": _kpi(cv, pv), "sold_qty": _kpi(cq, pq), "earned_cents": _kpi(ce, pe),
        "conversion_rate": _kpi(conv(co, cv), conv(po, pv)),
        "wishlists": WishlistItem.objects.filter(product=product).count(),
        "rating_avg": float(product.rating_avg), "rating_count": product.rating_count, "daily": daily,
    }


# --------------------------------------------------------------------------- #
# Staff review queue
# --------------------------------------------------------------------------- #


def review_queue(status: str | None):
    qs = Product.objects.select_related("store", "category").prefetch_related("images").order_by("submitted_at", "created_at")
    return qs.filter(status=status) if status else qs.exclude(status=PStatus.DRAFT)


def admin_row(p: Product) -> dict:
    images = list(p.images.all())
    primary = next((i for i in images if i.is_primary), images[0] if images else None)
    return {
        "id": p.id, "name": p.name, "slug": p.slug, "store": p.store.display_name, "store_slug": p.store.slug,
        "category": p.category.name if p.category_id else None, "price_cents": p.price_cents, "status": p.status,
        "submitted_at": p.submitted_at, "image_url": primary.image.url if primary else None,
    }


@transaction.atomic
def approve(product: Product) -> Product:
    if product.status != PStatus.PENDING:
        raise HttpError(409, f"Product is {product.status}, not pending")
    product.status = PStatus.ACTIVE
    product.published_at = product.published_at or timezone.now()
    product.rejection_reason = ""
    product.save(update_fields=["status", "published_at", "rejection_reason", "updated_at"])
    catalog.refresh_category_counts()
    return product


@transaction.atomic
def reject(product: Product, reason: str) -> Product:
    if product.status != PStatus.PENDING:
        raise HttpError(409, f"Product is {product.status}, not pending")
    product.status, product.rejection_reason = PStatus.REJECTED, reason
    product.save(update_fields=["status", "rejection_reason", "updated_at"])
    return product
