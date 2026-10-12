"""Catalog read model + reviews + wishlist rules."""

from collections import defaultdict

from django.conf import settings
from django.db import transaction
from django.db.models import Avg, Case, Count, IntegerField, Max, Min, Prefetch, Q, Sum, When
from ninja.errors import HttpError

from markets import services as markets
from markets.context import get_country
from markets.models import Country
from sellers.models import Store

from .models import (
    Brand,
    Category,
    Product,
    ProductContentSection,
    ProductImage,
    ProductOptionType,
    ProductOptionValue,
    ProductVariant,
    Review,
    ReviewMedia,
    StockMovement,
    WishlistItem,
)

# --------------------------------------------------------------------------- #
# Categories / brands
# --------------------------------------------------------------------------- #


def category_tree(*, only_active: bool = True) -> list[dict]:
    country = get_country()
    qs = Category.objects.all()
    if only_active:
        qs = qs.filter(is_active=True)
    nodes = {c.id: {"id": c.id, "name": c.name, "slug": c.slug, "image_url": c.image.url if c.image else None,
                    "product_count": c.product_count_for(country), "children": [], "_parent": c.parent_id} for c in qs}
    roots = []
    for node in nodes.values():
        parent = nodes.get(node.pop("_parent"))
        (parent["children"] if parent else roots).append(node)
    return roots


def category_with_descendants(slug: str) -> list[Category]:
    root = Category.objects.filter(slug=slug, is_active=True).first()
    if root is None:
        raise HttpError(404, "Category not found")
    found, frontier = [root], [root]
    while frontier:
        children = list(Category.objects.filter(parent__in=frontier, is_active=True))
        found += children
        frontier = children
    return found


def refresh_category_counts() -> None:
    """Denormalised '128 items' per category **per market** (own + descendants).
    Call from a job or after seeding."""
    parents = dict(Category.objects.values_list("id", "parent_id"))
    per_country: dict = defaultdict(lambda: defaultdict(int))
    rows = public_products(country=None).values_list("country_id", "category_id").annotate(n=Count("id"))
    for code, cat_id, n in rows:
        node = cat_id
        while node is not None:
            per_country[node][code] += n
            node = parents.get(node)
    for cat in Category.objects.all():
        Category.objects.filter(pk=cat.pk).update(product_counts=dict(per_country.get(cat.pk, {})))


# --------------------------------------------------------------------------- #
# Products
# --------------------------------------------------------------------------- #


CURRENT = object()  # sentinel: "the market of the current request"


def public_products(country=CURRENT):
    """Everything a shopper may see. Scoped to one market: by default the request's
    (`markets.context.get_country()`); pass a `Country` to pick one, or `None` for
    every market (jobs, counts). Outside a request nothing is active → unscoped."""
    if country is CURRENT:
        country = get_country()
    qs = Product.objects.filter(status=Product.ProductStatus.ACTIVE, store__status=Store.StoreStatus.ACTIVE)
    if country is not None:
        qs = qs.filter(country_id=country.code if isinstance(country, Country) else country)
    return (
        qs.select_related("category", "brand", "store")
        .prefetch_related(Prefetch("images", queryset=ProductImage.objects.order_by("-is_primary", "sort_order")))
    )


COLOUR_OPTION_NAMES = ("Color", "Colour")  # option types that feed the "Colors" filter

SORTS = {
    "new": ("-published_at", "-created_at"),
    "price_asc": ("price_cents", "name"),
    "price_desc": ("-price_cents", "name"),
    "rating": ("-rating_avg", "-rating_count"),
    "popular": ("-rating_count", "-rating_avg"),
}


def filter_products(qs, f):
    """Apply every sidebar filter exactly, then rank. With `q`, ranking is the
    hybrid keyword + semantic search (discovery.search) over the filtered set."""
    if f.category:
        qs = qs.filter(category__in=category_with_descendants(f.category))
    if f.brand:
        qs = qs.filter(brand__slug__in=f.brand)
    if f.color:
        match = Q()
        for c in f.color:
            match |= Q(option_types__values__value__iexact=c)
        qs = qs.filter(match, option_types__name__in=COLOUR_OPTION_NAMES)
    if f.price_min is not None:
        qs = qs.filter(price_cents__gte=f.price_min)
    if f.price_max is not None:
        qs = qs.filter(price_cents__lte=f.price_max)
    if f.in_stock is True:
        qs = qs.filter(stock_status__in=Product.AVAILABLE_STOCK_STATUSES)
    elif f.in_stock is False:
        qs = qs.exclude(stock_status__in=Product.AVAILABLE_STOCK_STATUSES)
    if f.store:
        qs = qs.filter(store__slug=f.store)
    if f.featured:
        qs = qs.filter(is_featured=True)
    if f.best_seller:
        qs = qs.filter(is_best_seller=True)
    if f.new_arrival:
        qs = qs.filter(is_new_arrival=True)
    qs = qs.distinct()
    sort = f.sort or ("relevance" if f.q else "new")
    if f.q:
        from discovery.search import hybrid_search

        ranked = [pid for pid, _ in hybrid_search(qs, f.q)]
        qs = qs.filter(pk__in=ranked)
        if sort == "relevance":
            position = Case(*[When(pk=pid, then=i) for i, pid in enumerate(ranked)], output_field=IntegerField())
            return qs.order_by(position) if ranked else qs.none()
    elif sort == "relevance":
        sort = "new"
    return qs.order_by(*SORTS[sort])


def filters_payload() -> dict:
    qs = public_products()
    price = qs.aggregate(lo=Min("price_cents"), hi=Max("price_cents"))
    colours = (
        ProductOptionValue.objects.filter(option_type__product__in=qs, option_type__name__in=COLOUR_OPTION_NAMES)
        .values("value", "swatch_hex").order_by("value").distinct()
    )
    return {
        "categories": category_tree(),
        "brands": [b for b in Brand.objects.filter(is_active=True, products__in=qs).distinct()],
        "colors": list(colours),
        "price_min_cents": price["lo"] or 0,
        "price_max_cents": price["hi"] or 0,
        "currency": markets.current_currency(),
    }


def request_wishlist_ids(request) -> set | None:
    """Wishlisted product ids for the signed-in caller, computed once per request
    (None for guests so `is_wishlisted` is omitted rather than false)."""
    if request is None:
        return None
    user = getattr(request, "auth", None)
    if user is None or not hasattr(user, "wishlist_items"):
        return None
    cached = getattr(request, "_wishlist_ids", None)
    if cached is None:
        cached = set(WishlistItem.objects.filter(user=user).values_list("product_id", flat=True))
        request._wishlist_ids = cached
    return cached


def get_product(slug: str, *, full: bool = True) -> Product:
    """PDP loader. `full=False` skips the option/variant/content prefetches for
    endpoints that only need the product row (reviews, related, similar)."""
    qs = public_products()
    if full:
        qs = qs.prefetch_related(
            Prefetch("option_types", queryset=ProductOptionType.objects.prefetch_related("values")),
            Prefetch("variants", queryset=ProductVariant.objects.filter(is_active=True).prefetch_related("option_values")),
            "content_sections",
        )
    product = qs.filter(slug=slug).first()
    if product is None:
        raise HttpError(404, "Product not found")
    return product


def rating_breakdown(product: Product) -> dict:
    rows = Review.objects.filter(product=product, status=Review.ReviewStatus.APPROVED).values("rating").annotate(n=Count("id"))
    dist = {r: 0 for r in (5, 4, 3, 2, 1)}
    for row in rows:
        dist[row["rating"]] = row["n"]
    count = sum(dist.values())
    avg = round(sum(r * n for r, n in dist.items()) / count, 1) if count else 0.0
    return {"average": avg, "count": count, "distribution": dist}


def related(product: Product, limit: int = 6):
    """'You may also like': same category first, then same brand, newest."""
    qs = public_products().exclude(pk=product.pk)
    same_cat = list(qs.filter(category=product.category).order_by("-rating_count", "-published_at")[:limit])
    if len(same_cat) < limit and product.brand_id:
        seen = {p.id for p in same_cat}
        same_cat += [p for p in qs.filter(brand=product.brand).order_by("-published_at")[:limit] if p.id not in seen][: limit - len(same_cat)]
    return same_cat


# --------------------------------------------------------------------------- #
# Stock (single write path, so every change is logged and the derived status is right)
# --------------------------------------------------------------------------- #


def product_stock_qty(product: Product) -> int:
    return product.variants.filter(is_active=True).aggregate(n=Sum("stock_qty"))["n"] or 0


def refresh_stock_status(product: Product) -> str:
    """Derive in/low/out from variant stock; `preorder` is a manual flag and is kept."""
    if product.stock_status == Product.StockStatus.PREORDER:
        return product.stock_status
    qty = product_stock_qty(product)
    if qty <= 0:
        status = Product.StockStatus.OUT_OF_STOCK
    elif qty <= product.effective_low_stock_threshold():
        status = Product.StockStatus.LOW_STOCK
    else:
        status = Product.StockStatus.IN_STOCK
    if status != product.stock_status:
        previous = product.stock_status
        Product.objects.filter(pk=product.pk).update(stock_status=status)
        product.stock_status = status
        # Tell the seller once, when stock *crosses* into low or out - not on every sale below the line, and not
        # for a draft that was never in stock or a restock on the way back up.
        if (product.status == Product.ProductStatus.ACTIVE and status in (Product.StockStatus.LOW_STOCK, Product.StockStatus.OUT_OF_STOCK)
                and previous in (Product.StockStatus.IN_STOCK, Product.StockStatus.LOW_STOCK)):
            from notifications import services as notifications

            notifications.stock_alert(product, status, qty)
    return status


def lock_variants(ids):
    """Row-lock several variants in one query (product + store joined). Pass the
    results to adjust_stock(..., locked=True) when changing stock in a loop."""
    return ProductVariant.objects.select_for_update(of=("self",)).select_related("product__store").filter(pk__in=list(ids))


@transaction.atomic
def adjust_stock(variant: ProductVariant, *, delta: int | None = None, set_to: int | None = None, reason: str,
                 actor_user=None, order=None, note: str = "", locked: bool = False) -> StockMovement:
    """Change a variant's stock by `delta` or to `set_to` (exactly one). Locks the row
    (unless the caller already did via lock_variants), refuses to go negative, logs a
    StockMovement and refreshes the product's stock status."""
    if (delta is None) == (set_to is None):
        raise ValueError("pass exactly one of delta / set_to")
    locked = variant if locked else ProductVariant.objects.select_for_update(of=("self",)).select_related("product__store").get(pk=variant.pk)
    if set_to is not None:
        delta = set_to - locked.stock_qty
    new_qty = locked.stock_qty + delta
    if new_qty < 0:
        raise HttpError(409, f"{locked.product.name}: only {locked.stock_qty} left")
    ProductVariant.objects.filter(pk=locked.pk).update(stock_qty=new_qty)
    variant.stock_qty = new_qty
    movement = StockMovement.objects.create(
        variant=locked, product=locked.product, delta=delta, qty_after=new_qty, reason=reason,
        note=note[:255], order=order, actor_user=actor_user,
    )
    refresh_stock_status(locked.product)
    return movement


# --------------------------------------------------------------------------- #
# Reviews
# --------------------------------------------------------------------------- #

REVIEW_SORTS = {"new": ("-created_at",), "top": ("-rating", "-created_at"), "low": ("rating", "-created_at")}
REVIEW_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
REVIEW_VIDEO_TYPES = {"video/mp4", "video/quicktime", "video/webm"}
REVIEW_MAX_FILES = 5


def public_reviews(product: Product, sort: str = "new"):
    return Review.objects.filter(product=product, status=Review.ReviewStatus.APPROVED).prefetch_related("media").order_by(*REVIEW_SORTS[sort])


def abbreviate(name: str) -> str:
    """'Ayesha Khan' → 'Ayesha K.' (design never shows full surnames)."""
    parts = name.split()
    return f"{parts[0]} {parts[-1][0]}." if len(parts) > 1 else name


def _verified_order_item(user, product):
    """Latest delivered order line for this product, if any → 'Verified Purchase'."""
    if user is None:
        return None
    from orders.models import Order, OrderItem

    return (
        OrderItem.objects.filter(order__user=user, product=product, order__status=Order.OrderStatus.DELIVERED, review__isnull=True)
        .order_by("-created_at").first()
    )


@transaction.atomic
def create_review(product: Product, *, user, name: str | None, email: str | None, rating: int, body: str, files) -> Review:
    if user is not None:
        name = name or user.full_name or user.email.split("@")[0]
        email = email or user.email
    if not name or not email:
        raise HttpError(422, "Name and email are required")
    if user is not None and Review.objects.filter(product=product, user=user).exists():
        raise HttpError(409, "You have already reviewed this product")
    if len(files) > REVIEW_MAX_FILES:
        raise HttpError(422, f"At most {REVIEW_MAX_FILES} photos/videos")
    for f in files:
        if f.content_type not in REVIEW_IMAGE_TYPES | REVIEW_VIDEO_TYPES:
            raise HttpError(422, f"Unsupported file type: {f.content_type}")
        if f.size > settings.REVIEW_MEDIA_MAX_BYTES:
            raise HttpError(422, "Each file must be 25 MB or smaller")

    review = Review.objects.create(
        product=product, user=user, order_item=_verified_order_item(user, product),
        reviewer_name=name, reviewer_email=email, rating=rating, body=body,
    )
    for i, f in enumerate(files):
        media = ReviewMedia(review=review, sort_order=i, type="image" if f.content_type in REVIEW_IMAGE_TYPES else "video")
        media.file.save(f.name, f, save=True)
    return review


def refresh_product_rating(product: Product) -> None:
    agg = Review.objects.filter(product=product, status=Review.ReviewStatus.APPROVED).aggregate(avg=Avg("rating"), n=Count("id"))
    Product.objects.filter(pk=product.pk).update(rating_avg=round(agg["avg"] or 0, 1), rating_count=agg["n"])


# --------------------------------------------------------------------------- #
# Wishlist
# --------------------------------------------------------------------------- #


def wishlist(user):
    return WishlistItem.objects.filter(user=user, product__in=public_products()).select_related("product__category", "product__brand").prefetch_related("product__images").order_by("-created_at")


def wishlist_add(user, product_id) -> WishlistItem:
    product = public_products().filter(pk=product_id).first()
    if product is None:
        raise HttpError(404, "Product not found")
    item, created = WishlistItem.objects.get_or_create(user=user, product=product)
    if created:
        from discovery import events

        events.record(events.ProductEvent.Kind.WISHLIST, product=product, user=user)
    return item


def wishlist_remove(user, product_id) -> None:
    WishlistItem.objects.filter(user=user, product_id=product_id).delete()
