"""Root django-ninja API, mounted at /api/v1/.

Each app exposes Router(s) in `<app>/api.py`; mount them here so Swagger
(/api/v1/docs) shows the whole surface. Add new routers at the bottom.
"""

from django.http import Http404
from ninja.errors import ValidationError

from core.openapi import DocumentedAPI

from accounts.api import auth_router, me_router
from catalog.api import catalog_router, wishlist_router
from catalog.category_requests import category_files_router
from catalog.request_api import admin_category_requests_router, seller_category_requests_router
from staff.api import admin_me_router
from discovery.api import discovery_router
from marketing.api import marketing_router
from chat.api import admin_chat_router, customer_chat_router, seller_chat_router
from markets.api import regions_router
from portal.api import (
    admin_products_router,
    admin_stores_router,
    analytics_router,
    delivery_router,
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
from notifications.api import notifications_router
from ledger.api import admin_ledger_router, admin_payouts_router, transactions_router
from returns.api import admin_returns_router, my_returns_router, returns_public_router, seller_returns_router
from sellers.files import files_router
from team.api import accept_router, me_router as seller_me_router, team_router

api = DocumentedAPI(
    title="asknget API",
    version="1.0.0",
    description=(
        "Multi-vendor marketplace: buyer storefront, seller portal, admin.\n\n"
        "**Base URL: every path in this document already starts with `/api/v1`, so a client's base URL is the origin only** "
        "(for example `https://asknget-backend.vercel.app`), never `.../api/v1`.\n\n"
        "Auth is `Authorization: Bearer <access_token>`. Money is integer minor units (`*_cents`). Lists return `{items, total, page, per_page, pages}`. "
        "Every error is `{\"detail\": \"...\"}` (422 adds `errors[]`). `X-Region`, `X-Language`, `X-Timezone` (all calls), `X-Store` (seller calls) and "
        "`X-Cart-Token` (cart and checkout) are declared on the operations that use them."
    ),
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
me_router.add_router("", my_returns_router)
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
api.add_router("/seller/delivery-options", delivery_router)
api.add_router("/seller/analytics", analytics_router)
api.add_router("/admin/stores", admin_stores_router)
api.add_router("/seller/chat", seller_chat_router)
api.add_router("/admin/chat", admin_chat_router)
api.add_router("/seller/search", search_router)
api.add_router("/seller/team", team_router)
api.add_router("/seller/notifications", notifications_router)
api.add_router("/seller/transactions", transactions_router)
api.add_router("/admin/ledger", admin_ledger_router)
api.add_router("/admin/payouts", admin_payouts_router)
api.add_router("/seller/me", seller_me_router)
api.add_router("/auth/team", accept_router)
api.add_router("/admin/products", admin_products_router)
api.add_router("", returns_public_router)
api.add_router("/seller/returns", seller_returns_router)
api.add_router("/admin/returns", admin_returns_router)
api.add_router("/files", files_router)
api.add_router("/files", category_files_router)
api.add_router("/seller/category-requests", seller_category_requests_router)
api.add_router("/admin/category-requests", admin_category_requests_router)
api.add_router("/admin/me", admin_me_router)
