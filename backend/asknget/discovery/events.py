"""Record shopper behaviour. Called from the catalog / cart / checkout code paths,
so the frontend never has to send tracking calls.

Actor resolution mirrors the cart: the signed-in user, else the `X-Cart-Token`
header (guest). Anonymous requests without a token are still logged for
aggregate signals (trending, search analytics) but can't be personalised.
Failures here must never break the calling request.
"""

from __future__ import annotations

import logging

from .models import ProductEvent

log = logging.getLogger(__name__)

CART_TOKEN_HEADER = "X-Cart-Token"


def actor_from_request(request) -> tuple:
    """(user | None, cart_token) — user is only set for authenticated callers."""
    user = getattr(request, "auth", None) if request is not None else None
    if user is not None and not getattr(user, "pk", None):
        user = None
    token = (request.headers.get(CART_TOKEN_HEADER, "") if request is not None else "")[:64]
    return user, token


def record(kind: str, *, product=None, user=None, cart_token: str = "", request=None, query: str = "", result_count=None) -> None:
    if request is not None and user is None and not cart_token:
        user, cart_token = actor_from_request(request)
    try:
        ProductEvent.objects.create(
            kind=kind, product=product, user=user, cart_token=cart_token or "",
            query=query[:200], result_count=result_count,
        )
    except Exception:  # pragma: no cover - defensive; tracking is best-effort
        log.exception("failed to record %s event", kind)


def record_for_cart(kind: str, *, product, cart) -> None:
    """Cart-driven events (add to cart, purchase) know the cart, not the request."""
    record(kind, product=product, user=cart.user, cart_token=cart.session_token or "")


def record_search(request, query: str, result_count: int) -> None:
    record(ProductEvent.Kind.SEARCH, request=request, query=query, result_count=result_count)
