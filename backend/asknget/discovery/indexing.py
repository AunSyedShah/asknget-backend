"""Build the per-product text card and keep ProductEmbedding rows current.

    embed_products()            # missing + stale rows only (cheap; run at start-up)
    embed_products(force=True)  # everything (after switching provider/model)

Called by the `embed_products` management command, the Docker entrypoint and
seed_demo. Catalog signals flip `is_stale`; nothing here talks to the network
unless a hosted provider is configured.
"""

from __future__ import annotations

import hashlib
import logging

from django.db import transaction
from django.db.models import Prefetch, Q

from catalog.models import Product, ProductContentSection, ProductOptionType

from .embeddings import get_embedder
from .models import ProductEmbedding

log = logging.getLogger(__name__)

DESCRIPTION_CHARS = 1500  # keep the card well inside embedding context limits


def category_path(category) -> str:
    names, node = [], category
    while node is not None:
        names.append(node.name)
        node = node.parent
    return " > ".join(reversed(names))


def product_text(product: Product) -> str:
    """Everything a shopper might type to find this product, as plain lines.
    Option values (shades, sizes) matter a lot for beauty, so they are spelled out."""
    lines = [product.name]
    if product.brand_id:
        lines.append(f"Brand: {product.brand.name}")
    lines.append(f"Category: {category_path(product.category)}")
    if product.tags:
        lines.append("Tags: " + ", ".join(str(t) for t in product.tags))
    if product.short_description:
        lines.append(product.short_description)
    if product.description_title:
        lines.append(product.description_title)
    if product.description:
        lines.append(product.description[:DESCRIPTION_CHARS])
    for ot in product.option_types.all():
        values = ", ".join(v.value for v in ot.values.all())
        if values:
            lines.append(f"{ot.name}: {values}")
    for section in product.content_sections.all():
        if section.section == ProductContentSection.ContentSection.BENEFIT:
            lines.append(f"{section.title}. {section.body}"[:300])
    return "\n".join(line.strip() for line in lines if line and line.strip())


def text_hash(text: str) -> str:
    return hashlib.blake2b(text.encode(), digest_size=32).hexdigest()


def _indexable():
    return (
        Product.objects.select_related("brand", "category__parent__parent")
        .prefetch_related(Prefetch("option_types", queryset=ProductOptionType.objects.prefetch_related("values")), "content_sections")
    )


def embed_products(*, force: bool = False, product_ids=None, batch_size: int = 64) -> dict:
    """Returns {"embedded": n, "unchanged": n, "skipped": n}."""
    embedder = get_embedder()
    qs = _indexable()
    if product_ids is not None:
        qs = qs.filter(pk__in=list(product_ids))
    elif not force:
        qs = qs.filter(Q(embedding__isnull=True) | Q(embedding__is_stale=True) | ~Q(embedding__model=embedder.name))

    existing = {e.product_id: e for e in ProductEmbedding.objects.filter(product__in=qs)}
    stats = {"embedded": 0, "unchanged": 0, "skipped": 0}
    pending: list[tuple[Product, str, str]] = []

    def flush():
        if not pending:
            return
        vectors = embedder.embed_documents([text for _, text, _ in pending])
        with transaction.atomic():
            for (product, text, digest), vector in zip(pending, vectors):
                ProductEmbedding.objects.update_or_create(
                    product=product,
                    defaults={"model": embedder.name, "text": text, "text_hash": digest, "vector": vector, "is_stale": False},
                )
        stats["embedded"] += len(pending)
        pending.clear()

    for product in qs.iterator(chunk_size=batch_size):
        text = product_text(product)
        if not text:
            stats["skipped"] += 1
            continue
        digest = text_hash(text)
        current = existing.get(product.pk)
        if current and not force and current.text_hash == digest and current.model == embedder.name:
            if current.is_stale:
                ProductEmbedding.objects.filter(pk=current.pk).update(is_stale=False)
            stats["unchanged"] += 1
            continue
        pending.append((product, text, digest))
        if len(pending) >= batch_size:
            flush()
    flush()
    if stats["embedded"]:
        log.info("embedded %(embedded)s products (%(unchanged)s unchanged)", stats)
    return stats


def mark_stale(product_ids) -> None:
    ProductEmbedding.objects.filter(product_id__in=list(product_ids)).update(is_stale=True)
