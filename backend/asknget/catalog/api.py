"""Catalog routers.

  /categories, /brands, /products/*   public (wishlist flag appears when signed in)
  /products/{slug}/reviews            list public; create works for guests and members
  /me/wishlist                        authenticated
"""

from uuid import UUID

from ninja import File, Form, Query, Router
from ninja.files import UploadedFile
from ninja.pagination import paginate

from core.auth import JWTAuth, OptionalJWTAuth
from core.pagination import PagePagination
from core.schemas import ErrorOut

from . import services
from .models import Brand
from .schemas import (
    BrandOut,
    CategoryOut,
    ProductCardOut,
    ProductDetailOut,
    ProductFiltersOut,
    ProductQuery,
    RatingBreakdownOut,
    ReviewCreatedOut,
    ReviewIn,
    ReviewOut,
    ReviewSortLit,
    WishlistItemOut,
)

catalog_router = Router(tags=["catalog"])
wishlist_router = Router(tags=["me"], auth=JWTAuth())


def _user(request):
    return request.auth if getattr(request.auth, "pk", None) else None


# --------------------------------------------------------------------------- #
# Lookups
# --------------------------------------------------------------------------- #


@catalog_router.get("/categories", response=list[CategoryOut], summary="Category tree with item counts")
def categories(request):
    return services.category_tree()


@catalog_router.get("/brands", response=list[BrandOut])
def brands(request):
    return [{"id": b.id, "name": b.name, "slug": b.slug, "logo_url": b.logo.url if b.logo else None} for b in Brand.objects.filter(is_active=True)]


@catalog_router.get("/products/filters", response=ProductFiltersOut, summary="Sidebar filter options (categories, brands, colours, price range)")
def product_filters(request):
    return services.filters_payload()


# --------------------------------------------------------------------------- #
# Products
# --------------------------------------------------------------------------- #


@catalog_router.get("/products", response=list[ProductCardOut], auth=OptionalJWTAuth(), summary="Search / browse products")
@paginate(PagePagination)
def product_list(request, filters: ProductQuery = Query(...)):
    return services.filter_products(services.public_products(), filters)


@catalog_router.get("/products/featured", response=list[ProductCardOut], auth=OptionalJWTAuth(), summary="Landing: 'Featured Products'")
def product_featured(request, limit: int = Query(8, ge=1, le=24)):
    return services.public_products().filter(is_featured=True).order_by("-published_at")[:limit]


@catalog_router.get("/products/best-sellers", response=list[ProductCardOut], auth=OptionalJWTAuth(), summary="Landing: 'Best Sellers'")
def product_best_sellers(request, limit: int = Query(8, ge=1, le=24)):
    return services.public_products().filter(is_best_seller=True).order_by("-rating_count")[:limit]


@catalog_router.get("/products/new-arrivals", response=list[ProductCardOut], auth=OptionalJWTAuth())
def product_new_arrivals(request, limit: int = Query(8, ge=1, le=24)):
    return services.public_products().filter(is_new_arrival=True).order_by("-published_at")[:limit]


@catalog_router.get("/products/top-discounted", response=list[ProductCardOut], auth=OptionalJWTAuth(), summary="Landing: 'Top Discounted'")
def product_top_discounted(request, limit: int = Query(8, ge=1, le=24)):
    qs = services.public_products().filter(compare_at_price_cents__isnull=False)
    return sorted(qs, key=lambda p: p.discount_percent or 0, reverse=True)[:limit]


@catalog_router.get("/products/{slug}", response={200: ProductDetailOut, 404: ErrorOut}, auth=OptionalJWTAuth(), summary="Product Details")
def product_detail(request, slug: str):
    return services.get_product(slug)


@catalog_router.get("/products/{slug}/related", response=list[ProductCardOut], auth=OptionalJWTAuth(), summary="'You may also like'")
def product_related(request, slug: str, limit: int = Query(6, ge=1, le=12)):
    return services.related(services.get_product(slug), limit)


# --------------------------------------------------------------------------- #
# Reviews
# --------------------------------------------------------------------------- #


@catalog_router.get("/products/{slug}/reviews", response=list[ReviewOut], summary="Reviews tab list")
@paginate(PagePagination)
def review_list(request, slug: str, sort: ReviewSortLit = "new"):
    return services.public_reviews(services.get_product(slug), sort)


@catalog_router.get("/products/{slug}/reviews/summary", response=RatingBreakdownOut, summary="'4.9 out of 5 (205 Reviews)' + star bars")
def review_summary(request, slug: str):
    return services.rating_breakdown(services.get_product(slug))


@catalog_router.post(
    "/products/{slug}/reviews",
    response={201: ReviewCreatedOut, 404: ErrorOut, 409: ErrorOut, 422: ErrorOut},
    auth=OptionalJWTAuth(),
    summary="'Add your review' (multipart; guests must send name + email)",
)
def review_create(request, slug: str, payload: Form[ReviewIn], files: list[UploadedFile] = File(None)):
    product = services.get_product(slug)
    review = services.create_review(
        product, user=_user(request), name=payload.name, email=payload.email, rating=payload.rating, body=payload.body, files=files or []
    )
    return 201, {"id": review.id, "status": review.status, "detail": "Thanks! Your review will appear once it has been approved."}


# --------------------------------------------------------------------------- #
# Wishlist
# --------------------------------------------------------------------------- #


@wishlist_router.get("/wishlist", response=list[WishlistItemOut], summary="My Account – Wishlist")
def wishlist_list(request):
    return [{"product": i.product, "added_at": i.created_at} for i in services.wishlist(request.auth)]


@wishlist_router.put("/wishlist/{product_id}", response={201: WishlistItemOut, 404: ErrorOut}, summary="Add to wishlist (idempotent)")
def wishlist_add(request, product_id: UUID):
    item = services.wishlist_add(request.auth, product_id)
    return 201, {"product": item.product, "added_at": item.created_at}


@wishlist_router.delete("/wishlist/{product_id}", response={204: None})
def wishlist_remove(request, product_id: UUID):
    services.wishlist_remove(request.auth, product_id)
    return 204, None
