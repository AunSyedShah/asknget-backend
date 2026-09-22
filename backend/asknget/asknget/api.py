"""Root django-ninja API, mounted at /api/v1/.

Each app exposes Router(s) in `<app>/api.py`; mount them here so Swagger
(/api/v1/docs) shows the whole surface. Add new routers at the bottom.
"""

from django.http import Http404
from ninja import NinjaAPI
from ninja.errors import ValidationError

from accounts.api import auth_router, me_router
from catalog.api import catalog_router, wishlist_router
from discovery.api import discovery_router
from marketing.api import marketing_router
from chat.api import admin_chat_router, customer_chat_router, seller_chat_router
from markets.api import regions_router
from portal.api import (
    admin_products_router,
    admin_stores_router,
    analytics_router,
    customers_router,
    dashboard_router,
    inventory_router,
    orders_router,
    products_router,
    plan_router,
    search_router,
)
from orders.api import admin_orders_router, cart_router, checkout_router, my_orders_router, orders_public_router
from sellers.api import admin_sellers_router, lookups_router, seller_router

api = NinjaAPI(
    title="asknget API",
    version="1.0.0",
    description="Multi-vendor marketplace: buyer storefront + seller onboarding.",
    urls_namespace="api_v1",
)


@api.exception_handler(Http404)
def on_404(request, exc):
    return api.create_response(request, {"detail": "Not found"}, status=404)


@api.exception_handler(ValidationError)
def on_validation_error(request, exc):
    """Flatten pydantic errors to {"detail": "...", "errors": [...]} so clients
    can show one message and still highlight fields."""
    def field_path(loc):
        parts = [str(p) for p in loc[1:]]  # loc[0] is body/query/path/form
        if parts and parts[0] in ("payload", "data"):
            parts = parts[1:]
        return ".".join(parts) or None

    errors = [{"field": field_path(e["loc"]), "message": e["msg"].removeprefix("Value error, ")} for e in exc.errors]
    summary = "; ".join(f"{e['field']}: {e['message']}" if e["field"] else e["message"] for e in errors)
    return api.create_response(request, {"detail": summary, "errors": errors}, status=422)


@api.get("/health", tags=["meta"], summary="Liveness probe")
def health(request):
    return {"status": "ok"}


api.add_router("/regions", regions_router)
api.add_router("/auth", auth_router)
me_router.add_router("", wishlist_router)
me_router.add_router("", my_orders_router)
me_router.add_router("", customer_chat_router)
api.add_router("/me", me_router)
api.add_router("", catalog_router)
api.add_router("", discovery_router)
api.add_router("/cart", cart_router)
api.add_router("/checkout", checkout_router)
api.add_router("", orders_public_router)
api.add_router("/admin/orders", admin_orders_router)
api.add_router("", marketing_router)
api.add_router("/seller", seller_router)
api.add_router("/lookups", lookups_router)
api.add_router("/admin/seller-applications", admin_sellers_router)
api.add_router("/seller/products", products_router)
api.add_router("/seller/inventory", inventory_router)
api.add_router("/seller/orders", orders_router)
api.add_router("/seller/customers", customers_router)
api.add_router("/seller/dashboard", dashboard_router)
api.add_router("/seller/plan", plan_router)
api.add_router("/seller/analytics", analytics_router)
api.add_router("/admin/stores", admin_stores_router)
api.add_router("/seller/chat", seller_chat_router)
api.add_router("/admin/chat", admin_chat_router)
api.add_router("/seller/search", search_router)
api.add_router("/admin/products", admin_products_router)
