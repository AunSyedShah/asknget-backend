"""Recommendation rails.

  similar(product)                    PDP "You may also like" — embedding neighbours
  frequently_bought_together(product) PDP / cart — order co-occurrence
  trending()                          home / cold start — event-weighted popularity
  for_actor(user, cart_token)         "Recommended for you" — taste-profile vector

The taste profile is the weighted mean of the embeddings of what the shopper
viewed / wishlisted / carted / bought (weights in models.EVENT_WEIGHTS, halved
every PROFILE_HALF_LIFE_DAYS). No training, works from the first click, and
each result can be explained by the seed products that produced it.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import timedelta

from django.core.cache import cache
from django.db.models import Count, Q
from django.utils import timezone

from catalog import services as catalog
from catalog.models import Product, WishlistItem

from .embeddings import normalise
from .models import EVENT_WEIGHTS, ProductEmbedding, ProductEvent
from .search import nearest

PROFILE_WINDOW_DAYS = 90
PROFILE_HALF_LIFE_DAYS = 30
PROFILE_MAX_EVENTS = 300
TRENDING_WINDOW_DAYS = 30
FBT_CACHE_SECONDS = 600


def _ordered(ids: list, qs=None) -> list[Product]:
    """Products in the given id order, serialisable as cards."""
    if not ids:
        return []
    by_id = (qs if qs is not None else catalog.public_products()).in_bulk(ids)
    return [by_id[i] for i in ids if i in by_id]


# --------------------------------------------------------------------------- #
# Item-to-item
# --------------------------------------------------------------------------- #


def similar(product: Product, limit: int = 6) -> list[Product]:
    emb = ProductEmbedding.objects.filter(product=product).values_list("vector", flat=True).first()
    if emb is None:
        return catalog.related(product, limit)
    hits = nearest(catalog.public_products().exclude(pk=product.pk), emb, limit=limit)
    items = _ordered([pid for pid, _ in hits])
    if len(items) < limit:  # top up the rail the old way (same category / brand)
        seen = {p.id for p in items} | {product.id}
        items += [p for p in catalog.related(product, limit) if p.id not in seen][: limit - len(items)]
    return items


def frequently_bought_together(product: Product, limit: int = 4) -> list[Product]:
    from orders.models import Order, OrderItem

    key = f"fbt:{product.pk}:{limit}"
    ids = cache.get(key)
    if ids is None:
        live_orders = ~Q(order__status=Order.OrderStatus.CANCELLED)
        order_ids = OrderItem.objects.filter(product=product).filter(live_orders).values("order_id")
        rows = (
            OrderItem.objects.filter(order_id__in=order_ids, product__isnull=False).filter(live_orders)
            .exclude(product=product)
            .values("product_id").annotate(n=Count("order_id", distinct=True))
            .order_by("-n")[:limit]
        )
        ids = [r["product_id"] for r in rows]
        cache.set(key, ids, FBT_CACHE_SECONDS)
    items = _ordered(ids)
    if not items:
        return similar(product, limit)
    return items


# --------------------------------------------------------------------------- #
# Popularity
# --------------------------------------------------------------------------- #


def trending(limit: int = 8, *, days: int = TRENDING_WINDOW_DAYS) -> list[Product]:
    since = timezone.now() - timedelta(days=days)
    scores: dict = defaultdict(float)
    rows = (
        ProductEvent.objects.filter(created_at__gte=since, product__isnull=False)
        .exclude(kind=ProductEvent.Kind.SEARCH)
        .values("product_id", "kind").annotate(n=Count("id"))
    )
    for r in rows:
        scores[r["product_id"]] += EVENT_WEIGHTS[r["kind"]] * r["n"]
    ranked = sorted(scores, key=scores.get, reverse=True)
    items = _ordered(ranked[: limit * 2])[:limit]
    if len(items) < limit:  # no traffic yet → editorial best sellers, then most reviewed
        seen = {p.id for p in items}
        extra = catalog.public_products().exclude(pk__in=seen).order_by("-is_best_seller", "-rating_count", "-published_at")
        items += list(extra[: limit - len(items)])
    return items


# --------------------------------------------------------------------------- #
# Personalised
# --------------------------------------------------------------------------- #


def _interactions(user, cart_token: str) -> dict:
    """{product_id: weight} for this shopper — the seeds of the taste profile."""
    if user is None and not cart_token:
        return {}
    now = timezone.now()
    since = now - timedelta(days=PROFILE_WINDOW_DAYS)
    who = Q(user=user) if user is not None else Q(cart_token=cart_token)
    events = (
        ProductEvent.objects.filter(who, created_at__gte=since, product__isnull=False)
        .exclude(kind=ProductEvent.Kind.SEARCH)
        .order_by("-created_at").values_list("product_id", "kind", "created_at")[:PROFILE_MAX_EVENTS]
    )
    weights: dict = defaultdict(float)
    for pid, kind, at in events:
        age_days = (now - at).total_seconds() / 86400
        weights[pid] += EVENT_WEIGHTS[kind] * math.pow(0.5, age_days / PROFILE_HALF_LIFE_DAYS)
    if user is not None:  # history that predates event tracking still counts
        from orders.models import Order, OrderItem

        for pid in WishlistItem.objects.filter(user=user).values_list("product_id", flat=True):
            weights[pid] = max(weights[pid], EVENT_WEIGHTS[ProductEvent.Kind.WISHLIST])
        bought = OrderItem.objects.filter(order__user=user, product__isnull=False).exclude(order__status=Order.OrderStatus.CANCELLED)
        for pid in bought.values_list("product_id", flat=True):
            weights[pid] = max(weights[pid], EVENT_WEIGHTS[ProductEvent.Kind.PURCHASE])
    return dict(weights)


def profile_vector(weights: dict) -> list[float] | None:
    rows = ProductEmbedding.objects.filter(product_id__in=list(weights)).values_list("product_id", "vector")
    acc: list[float] | None = None
    for pid, vec in rows:
        w = weights[pid]
        if acc is None:
            acc = [w * x for x in vec]
        else:
            for i, x in enumerate(vec):
                acc[i] += w * x
    return normalise(acc) if acc else None


def for_actor(user, cart_token: str, *, limit: int = 8) -> dict:
    """{"strategy": "personalised" | "trending", "seeds": [Product], "items": [Product]}

    Seeds (anything the shopper already viewed, wishlisted, carted or bought) are
    never recommended back — the rail should always show something new."""
    weights = _interactions(user, cart_token)
    vector = profile_vector(weights) if weights else None
    if vector is None:
        return {"strategy": "trending", "seeds": [], "items": trending(limit)}

    exclude = set(weights)
    hits = nearest(catalog.public_products(), vector, limit=limit, exclude_ids=exclude)
    items = _ordered([pid for pid, _ in hits])
    if len(items) < limit:
        seen = {p.id for p in items} | exclude
        items += [p for p in trending(limit * 2) if p.id not in seen][: limit - len(items)]
    seed_ids = sorted(weights, key=weights.get, reverse=True)[:3]
    return {"strategy": "personalised", "seeds": _ordered(seed_ids), "items": items}
