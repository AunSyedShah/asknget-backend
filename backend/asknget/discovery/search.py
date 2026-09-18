"""Hybrid product search and nearest-neighbour helpers.

    ranked = hybrid_search(candidates_qs, "tinted moisturiser dry skin")
    → [(product_id, score), ...] best first, at most SEARCH_MAX_CANDIDATES

Two rankers run over the same pre-filtered candidate set (category, price,
colour… are applied *before* ranking, so filters stay exact):

  keyword  Postgres: websearch full-text (stemming, phrases, -exclusions) over
           the product card + trigram similarity on the name for typos.
           SQLite: every token must appear in the card; ranked by name hits.
  vector   cosine nearest neighbours of the query embedding.
           Postgres: pgvector `<=>` (HNSW index). SQLite: brute force in Python.

Their rank lists are fused with Reciprocal Rank Fusion, so a product found by
both rises to the top, an exact brand/SKU match still wins on keywords alone,
and a "meaning" match with no shared words still surfaces via the vector side.
Nonsense queries return nothing because each ranker has a relevance floor.
"""

from __future__ import annotations

import logging
import re

from django.conf import settings
from django.db import connection
from django.db.models import F, Q

from .embeddings import dot, get_embedder
from .models import ProductEmbedding

log = logging.getLogger(__name__)

RRF_K = 60
_TOKEN = re.compile(r"[\w]+", re.UNICODE)
TRIGRAM_MIN = 0.3


def _pg() -> bool:
    return connection.vendor == "postgresql"


# --------------------------------------------------------------------------- #
# Vector side
# --------------------------------------------------------------------------- #


def nearest(candidates, vector: list[float], *, limit: int, exclude_ids=(), min_similarity: float | None = None) -> list[tuple]:
    """[(product_id, similarity)] for products in `candidates` (a Product queryset),
    most similar first. Similarity is cosine in [−1, 1]."""
    if min_similarity is None:
        min_similarity = get_embedder().min_similarity
    qs = ProductEmbedding.objects.filter(product__in=candidates)
    if exclude_ids:
        qs = qs.exclude(product_id__in=list(exclude_ids))

    if _pg():
        from pgvector.django import CosineDistance

        rows = (
            qs.annotate(distance=CosineDistance("vector", vector))
            .order_by("distance")
            .values_list("product_id", "distance")[: limit * 2]
        )
        scored = [(pid, 1.0 - float(d)) for pid, d in rows]
    else:
        scored = [(pid, dot(vec, vector)) for pid, vec in qs.values_list("product_id", "vector")]
        scored.sort(key=lambda r: r[1], reverse=True)
    return [(pid, s) for pid, s in scored if s >= min_similarity][:limit]


# --------------------------------------------------------------------------- #
# Keyword side
# --------------------------------------------------------------------------- #


def keyword_candidates(candidates, q: str, *, limit: int) -> list[tuple]:
    """[(product_id, score)] best first."""
    qs = ProductEmbedding.objects.filter(product__in=candidates)
    if _pg():
        from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector, TrigramSimilarity

        query = SearchQuery(q, search_type="websearch", config="english")
        rows = (
            qs.annotate(rank=SearchRank(SearchVector("text", config="english"), query), sim=TrigramSimilarity("product__name", q))
            .annotate(score=F("rank") + F("sim"))
            .filter(Q(text__search=query) | Q(sim__gte=TRIGRAM_MIN))  # full-text hit, or a close-enough name (typos)
            .order_by("-score")
            .values_list("product_id", "score")[:limit]
        )
        return [(pid, float(score)) for pid, score in rows]

    tokens = [t.lower() for t in _TOKEN.findall(q)]
    if not tokens:
        return []
    for t in tokens:
        qs = qs.filter(text__icontains=t)
    scored = []
    for pid, name, text in qs.values_list("product_id", "product__name", "text"):
        name_l, first_line = name.lower(), text.split("\n", 1)[0].lower()
        score = sum(2.0 if t in name_l else 1.0 for t in tokens) + (1.0 if q.lower() in first_line else 0.0)
        scored.append((pid, score))
    scored.sort(key=lambda r: r[1], reverse=True)
    return scored[:limit]


# --------------------------------------------------------------------------- #
# Fusion
# --------------------------------------------------------------------------- #


def rrf(*rankings: list[tuple], k: int = RRF_K) -> list[tuple]:
    scores: dict = {}
    for ranking in rankings:
        for rank, (pid, _) in enumerate(ranking, start=1):
            scores[pid] = scores.get(pid, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda r: r[1], reverse=True)


def hybrid_search(candidates, q: str, *, limit: int | None = None) -> list[tuple]:
    limit = limit or settings.SEARCH_MAX_CANDIDATES
    q = q.strip()
    if not q:
        return []
    keyword = keyword_candidates(candidates, q, limit=limit)
    try:
        vector = nearest(candidates, get_embedder().embed_query(q), limit=limit)
    except Exception:  # hosted embedder down → keyword-only rather than a 500
        log.exception("query embedding failed; keyword-only results for %r", q)
        vector = []
    fused = rrf(keyword, vector)[:limit]
    # Products not embedded yet (just created, worker not run) still match on plain substrings.
    seen = {pid for pid, _ in fused}
    unindexed = (
        candidates.filter(embedding__isnull=True)
        .filter(Q(name__icontains=q) | Q(short_description__icontains=q) | Q(brand__name__icontains=q))
        .values_list("pk", flat=True)
    )
    fused += [(pid, 0.0) for pid in unindexed if pid not in seen]
    return fused[:limit]
