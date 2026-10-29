"""What a reviewer approved, and whether the product still says it.

A product is approved as it stands that day. `reviewed_hash` is a fingerprint of the parts of it that a review reads (the words, the
category and brand, the options, the pictures, the price band). Every seller edit of a live or archived product goes through
`tracked_edit`: when the fingerprint no longer matches, `edited_since_review` is set, so the change cannot go live, or come back from
the archive, as if it had been reviewed. Stock, SKUs, shipping, delivery options and a price change inside the same band are not
part of the fingerprint: editing them never raises the flag.

`settings.PRODUCT_EDIT_POLICY` says what happens to a live product that was edited:
    flag  it stays live and shows in the staff queue (`status=edited`) for a person to approve or take down   (default)
    hold  it goes back to `pending` (hidden) until a person approves it
"""

from __future__ import annotations

import hashlib
import math
import unicodedata
from functools import wraps

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import Product, ProductContentSection, ProductImage, ProductOptionType, ProductVariant

PStatus = Product.ProductStatus


def _norm(value) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).casefold().split())


def price_signature(product: Product) -> str:
    """The price band (it doubles or halves) and the discount in steps of 10: the numbers a review reads, without a flag for a typo fix."""
    prices = [v.price_cents if v.price_cents is not None else product.price_cents
              for v in ProductVariant.objects.filter(product=product, is_active=True)] or [product.price_cents]
    lowest = max(min(prices), 1)
    compare = product.compare_at_price_cents
    discount = round((1 - lowest / compare) * 100) if compare and compare > lowest else 0
    return f"{math.floor(math.log2(lowest))}:{discount // 10}"


def content_hash(product: Product) -> str:
    """Fresh queries throughout: a prefetched copy on `product` may predate the edit being judged."""
    options = ProductOptionType.objects.filter(product=product).prefetch_related("values")
    option_text = sorted(f"{_norm(ot.name)}={','.join(sorted(_norm(v.value) for v in ot.values.all()))}" for ot in options)
    sections = ProductContentSection.objects.filter(product=product).order_by("section", "sort_order")
    parts = [
        _norm(product.name), _norm(product.short_description), _norm(product.description_title), _norm(product.description),
        ",".join(sorted(_norm(t) for t in (product.tags or []))),
        str(product.category_id or ""), str(product.brand_id or ""), _norm(product.barcode),
        "|".join(option_text),
        "|".join(f"{s.section}:{_norm(s.title)}:{_norm(s.body)}" for s in sections),
        ",".join(sorted(str(name) for name in ProductImage.objects.filter(product=product).values_list("image", flat=True))),
        price_signature(product),
    ]
    return hashlib.blake2b("\x1f".join(parts).encode(), digest_size=32).hexdigest()


def is_edited_live(product: Product) -> bool:
    return product.status == PStatus.ACTIVE and product.edited_since_review


def stamp(product: Product, *, actor=None) -> None:
    """Record that a person (`actor`) or the system has just approved the product as it stands. The caller saves."""
    product.reviewed_hash = content_hash(product)
    product.edited_since_review = False
    product.review_source = Product.ReviewSource.STAFF if actor is not None else Product.ReviewSource.SYSTEM
    product.reviewed_by = actor
    product.reviewed_at = timezone.now()


def _tracks(product: Product) -> bool:
    """Only a product that was published has approved content to compare against."""
    return product.status == PStatus.ACTIVE or (product.status == PStatus.ARCHIVED and product.published_at is not None)


def note_edit(product: Product, before: str) -> None:
    after = content_hash(product)
    changed = []
    if not product.reviewed_hash:
        # Published before this record existed: what it said before this edit is what was approved.
        product.reviewed_hash = before
        changed.append("reviewed_hash")
    edited = after != product.reviewed_hash
    if edited != product.edited_since_review:
        product.edited_since_review = edited
        changed.append("edited_since_review")
    held = edited and product.status == PStatus.ACTIVE and settings.PRODUCT_EDIT_POLICY == "hold"
    if held:
        product.status, product.submitted_at, product.rejection_reason = PStatus.PENDING, timezone.now(), ""
        changed += ["status", "submitted_at", "rejection_reason"]
    if changed:
        product.save(update_fields=[*changed, "updated_at"])
    if held:
        from . import services

        services.refresh_category_counts()


def tracked_edit(fn):
    """Decorator for a service function whose first argument is the product a seller is editing."""

    @wraps(fn)
    def wrapper(product, *args, **kwargs):
        if not _tracks(product):
            return fn(product, *args, **kwargs)
        with transaction.atomic():
            before = content_hash(product)
            result = fn(product, *args, **kwargs)
            note_edit(product, before)
            return result

    return wrapper
