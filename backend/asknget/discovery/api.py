"""Discovery routers (search bar + recommendation rails).

  GET /search/suggest?q=            typeahead for the header search bar
  GET /recommendations              "Recommended for you" (guest via X-Cart-Token)
  GET /products/{slug}/similar      embedding neighbours (PDP rail)
  GET /products/{slug}/frequently-bought-together

Full search lives on GET /products?q= (catalog) — it ranks with the hybrid
engine here and keeps every sidebar filter exact.
"""

from django.db import connection
from django.db.models import Q
from ninja import Query, Router

from catalog import services as catalog
from catalog.models import Brand, Category
from catalog.schemas import ProductCardOut
from core.auth import OptionalJWTAuth
from core.schemas import ErrorOut

from . import recommendations
from .events import actor_from_request
from .schemas import RecommendationsOut, SuggestOut

discovery_router = Router(tags=["discovery"])


@discovery_router.get("/search/suggest", response=SuggestOut, summary="Search-bar suggestions")
def suggest(request, q: str = Query(..., min_length=1, max_length=80), limit: int = Query(5, ge=1, le=10)):
    q = q.strip()
    products = catalog.public_products()
    if connection.vendor == "postgresql":
        from django.contrib.postgres.search import TrigramSimilarity

        products = (
            products.annotate(sim=TrigramSimilarity("name", q))
            .filter(Q(name__icontains=q) | Q(sim__gte=0.3))
            .order_by("-sim", "-rating_count")
        )
    else:
        products = products.filter(name__icontains=q).order_by("-rating_count")
    brands = Brand.objects.filter(is_active=True, name__icontains=q, products__in=catalog.public_products()).distinct()[:limit]
    categories = Category.objects.filter(is_active=True, name__icontains=q)[:limit]
    return {
        "products": [{"label": p.name, "slug": p.slug, "image_url": ProductCardOut.resolve_image_url(p)} for p in products[:limit]],
        "brands": [{"label": b.name, "slug": b.slug, "image_url": b.logo.url if b.logo else None} for b in brands],
        "categories": [{"label": c.name, "slug": c.slug, "image_url": c.image.url if c.image else None} for c in categories],
    }


@discovery_router.get("/recommendations", response=RecommendationsOut, auth=OptionalJWTAuth(), summary="'Recommended for you'")
def recommended(request, limit: int = Query(8, ge=1, le=24)):
    user, token = actor_from_request(request)
    return recommendations.for_actor(user, token, limit=limit)


@discovery_router.get("/products/{slug}/similar", response={200: list[ProductCardOut], 404: ErrorOut}, auth=OptionalJWTAuth(), summary="'You may also like' (semantic)")
def similar(request, slug: str, limit: int = Query(6, ge=1, le=12)):
    return recommendations.similar(catalog.get_product(slug, full=False), limit)


@discovery_router.get("/products/{slug}/frequently-bought-together", response={200: list[ProductCardOut], 404: ErrorOut}, auth=OptionalJWTAuth())
def frequently_bought_together(request, slug: str, limit: int = Query(4, ge=1, le=12)):
    return recommendations.frequently_bought_together(catalog.get_product(slug, full=False), limit)
