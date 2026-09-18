"""Seller portal routers (all under /seller/* with StoreAuth) + staff product review.

  /seller/products     list, KPIs, wizard, images, options/variants, content, actions, analytics
  /seller/inventory    stock overview, adjustments, movements, export
  /seller/orders       overview, list, detail, status, cancel, invoice data, export
  /seller/customers    overview, list, detail, notes, export
  /seller/dashboard    KPIs, charts, latest orders
  /seller/search       header "Search anything"
  /admin/products      staff review queue (approve / reject)
"""

from uuid import UUID

from ninja import File, Form, Query, Router
from ninja.files import UploadedFile
from ninja.pagination import paginate

from core.auth import StaffAuth
from core.pagination import PagePagination
from core.schemas import ErrorOut, MessageOut

from . import inventory, orders, products
from .auth import StoreAuth
from .schemas import (
    AdminProductRowOut,
    InventoryKpisOut,
    InventoryQuery,
    InventoryRowOut,
    InventoryVariantOut,
    StockAdjustIn,
    StockMovementOut,
    OrderCancelIn,
    OrderStatusIn,
    OrderTabsOut,
    SellerInvoiceOut,
    SellerOrderDetailOut,
    SellerOrderQuery,
    SellerOrderRowOut,
    SellerOrdersOverviewOut,
    ContentIn,
    ImageOrderIn,
    OptionsIn,
    PeriodLit,
    ProductAnalyticsOut,
    ProductCreateIn,
    ProductPatchIn,
    RejectIn,
    SellerImageOut,
    SellerProductKpisOut,
    SellerProductOut,
    SellerProductQuery,
    SellerProductRowOut,
    SellerVariantOut,
    StatusChangeIn,
    VariantPatchIn,
)

products_router = Router(tags=["seller: products"], auth=StoreAuth())
admin_products_router = Router(tags=["admin: products"], auth=StaffAuth())
inventory_router = Router(tags=["seller: inventory"], auth=StoreAuth())
orders_router = Router(tags=["seller: orders"], auth=StoreAuth())

_NOT_FOUND = {404: ErrorOut}
_EDIT = {200: SellerProductOut, 404: ErrorOut, 409: ErrorOut, 422: ErrorOut}


def _out(store, product_id):
    return products.serialize(products.get_product(store, product_id))


# --------------------------------------------------------------------------- #
# Products
# --------------------------------------------------------------------------- #


@products_router.get("", response=list[SellerProductRowOut], summary="Products table (tabs, filters, sort)")
@paginate(PagePagination)
def product_list(request, filters: SellerProductQuery = Query(...)):
    return [products.row(p) for p in products.list_products(request.store, filters)]


@products_router.get("/kpis", response=SellerProductKpisOut, summary="Products KPI cards + tab counts")
def product_kpis(request, period: PeriodLit = "month"):
    return products.kpis(request.store, period)


@products_router.post("", response={201: SellerProductOut, 422: ErrorOut}, summary="'+ Add New Product' → step 1 'Save & Continue' (creates a draft)")
def product_create(request, payload: ProductCreateIn):
    product = products.create(request.store, payload.model_dump())
    return 201, _out(request.store, product.pk)


@products_router.get("/{product_id}", response={200: SellerProductOut, 404: ErrorOut}, summary="Everything the wizard/edit screen needs")
def product_detail(request, product_id: UUID):
    return _out(request.store, product_id)


@products_router.patch("/{product_id}", response=_EDIT, summary="Autosave any wizard step (basic info, pricing, shipping…)")
def product_patch(request, product_id: UUID, payload: ProductPatchIn):
    product = products.get_product(request.store, product_id)
    products.patch(product, payload.model_dump(exclude_unset=True))
    return _out(request.store, product_id)


@products_router.put("/{product_id}/options", response=_EDIT, summary="Step 3: option types → variants regenerated")
def product_options(request, product_id: UUID, payload: OptionsIn):
    products.set_options(products.get_product(request.store, product_id), payload.options)
    return _out(request.store, product_id)


@products_router.patch("/{product_id}/variants/{variant_id}", response={200: SellerVariantOut, 404: ErrorOut, 409: ErrorOut, 422: ErrorOut}, summary="Step 3: SKU / price / stock per variant")
def variant_patch(request, product_id: UUID, variant_id: UUID, payload: VariantPatchIn):
    product = products.get_product(request.store, product_id)
    v = products.patch_variant(product, variant_id, payload.model_dump(exclude_unset=True), actor=request.auth)
    return next(x for x in products.serialize(products.get_product(request.store, product_id))["variants"] if x["id"] == v.id)


@products_router.post("/{product_id}/images", response={201: SellerImageOut, 404: ErrorOut, 422: ErrorOut}, summary="Step 2: upload an image (multipart)")
def image_add(request, product_id: UUID, file: UploadedFile = File(...), alt: str = Form("")):
    product = products.get_product(request.store, product_id)
    img = products.add_image(product, file, alt)
    return 201, {"id": img.id, "url": img.image.url, "alt": img.alt, "is_primary": img.is_primary, "sort_order": img.sort_order}


@products_router.put("/{product_id}/images/order", response=_EDIT, summary="Step 2: reorder images (first = primary)")
def image_order(request, product_id: UUID, payload: ImageOrderIn):
    products.reorder_images(products.get_product(request.store, product_id), payload.image_ids)
    return _out(request.store, product_id)


@products_router.delete("/{product_id}/images/{image_id}", response={204: None, 404: ErrorOut})
def image_delete(request, product_id: UUID, image_id: UUID):
    products.delete_image(products.get_product(request.store, product_id), image_id)
    return 204, None


@products_router.put("/{product_id}/content", response=_EDIT, summary="Key Benefits / How to Use sections (PDP tabs)")
def content_put(request, product_id: UUID, payload: ContentIn):
    products.set_content(products.get_product(request.store, product_id), payload.sections)
    return _out(request.store, product_id)


@products_router.post("/{product_id}/status", response=_EDIT, summary="Step 5 'Submit for review' / 'Change Status' / Archive")
def status_change(request, product_id: UUID, payload: StatusChangeIn):
    products.change_status(products.get_product(request.store, product_id), payload.action)
    return _out(request.store, product_id)


@products_router.post("/{product_id}/duplicate", response={201: SellerProductOut, 404: ErrorOut}, summary="'Duplicate Product' → new draft")
def product_duplicate(request, product_id: UUID):
    copy = products.duplicate(products.get_product(request.store, product_id))
    return 201, _out(request.store, copy.pk)


@products_router.delete("/{product_id}", response={204: None, 404: ErrorOut, 409: ErrorOut}, summary="'Delete Product' (soft; active products must be archived first)")
def product_delete(request, product_id: UUID):
    products.delete(products.get_product(request.store, product_id))
    return 204, None


@products_router.get("/{product_id}/analytics", response={200: ProductAnalyticsOut, 404: ErrorOut}, summary="'View Analytics'")
def product_analytics(request, product_id: UUID, period: PeriodLit = "month"):
    return products.analytics(request.store, products.get_product(request.store, product_id), period)


# --------------------------------------------------------------------------- #
# Staff review
# --------------------------------------------------------------------------- #


@admin_products_router.get("", response=list[AdminProductRowOut], summary="Review queue (default: pending)")
@paginate(PagePagination)
def admin_product_list(request, status: str = Query("pending", description="pending | active | rejected | archived | all")):
    return [products.admin_row(p) for p in products.review_queue(None if status == "all" else status)]


@admin_products_router.post("/{product_id}/approve", response={200: MessageOut, 404: ErrorOut, 409: ErrorOut})
def admin_product_approve(request, product_id: UUID):
    product = products.Product.objects.filter(pk=product_id).first()
    if product is None:
        return 404, {"detail": "Product not found"}
    products.approve(product)
    return {"detail": f"{product.name} is now live"}


@admin_products_router.post("/{product_id}/reject", response={200: MessageOut, 404: ErrorOut, 409: ErrorOut})
def admin_product_reject(request, product_id: UUID, payload: RejectIn):
    product = products.Product.objects.filter(pk=product_id).first()
    if product is None:
        return 404, {"detail": "Product not found"}
    products.reject(product, payload.reason)
    return {"detail": f"{product.name} rejected"}


# --------------------------------------------------------------------------- #
# Inventory
# --------------------------------------------------------------------------- #


@inventory_router.get("", response=list[InventoryRowOut], summary="Inventory table (per product, variants nested)")
@paginate(PagePagination)
def inventory_list(request, filters: InventoryQuery = Query(...)):
    return [inventory.row(p) for p in inventory.list_inventory(request.store, tab=filters.tab, q=filters.q, brand=filters.brand, category=filters.category, sort=filters.sort)]


@inventory_router.get("/kpis", response=InventoryKpisOut)
def inventory_kpis(request):
    return inventory.kpis(request.store)


@inventory_router.get("/export.csv", summary="'Export' — CSV, one line per variant")
def inventory_export(request):
    return inventory.export(request.store)


@inventory_router.post("/{variant_id}/adjust", response={200: InventoryVariantOut, 404: ErrorOut, 409: ErrorOut, 422: ErrorOut}, summary="Adjust stock (logged as a manual movement)")
def inventory_adjust(request, variant_id: UUID, payload: StockAdjustIn):
    inventory.adjust(request.store, variant_id, set_to=payload.set, delta=payload.delta, note=payload.note, actor=request.auth)
    return inventory.variant_row(inventory.get_variant(request.store, variant_id))


@inventory_router.get("/{variant_id}/movements", response={200: list[StockMovementOut], 404: ErrorOut}, summary="Inventory movements for one variant")
def inventory_movements(request, variant_id: UUID, limit: int = Query(50, ge=1, le=200)):
    return [inventory.movement_out(m) for m in inventory.movements(request.store, variant_id, limit)]


# --------------------------------------------------------------------------- #
# Orders
# --------------------------------------------------------------------------- #

_ORDER = {200: SellerOrderDetailOut, 404: ErrorOut, 409: ErrorOut}


@orders_router.get("/overview", response=SellerOrdersOverviewOut, summary="Orders Overview: KPIs, monthly chart, status donut, latest")
def orders_overview(request, period: PeriodLit = "month"):
    return orders.overview(request.store, period)


@orders_router.get("", response=list[SellerOrderRowOut], summary="Orders table (tabs, search, date range)")
@paginate(PagePagination)
def orders_list(request, filters: SellerOrderQuery = Query(...)):
    return [orders.row(o) for o in orders.list_orders(request.store, tab=filters.tab, q=filters.q, date_from=filters.date_from, date_to=filters.date_to, sort=filters.sort)]


@orders_router.get("/tabs", response=OrderTabsOut)
def orders_tabs(request):
    return orders.tabs(request.store)


@orders_router.get("/export.csv", summary="'Export' — CSV of this store's orders")
def orders_export(request):
    return orders.export(request.store)


@orders_router.get("/{order_id}", response={200: SellerOrderDetailOut, 404: ErrorOut}, summary="View Order")
def order_detail(request, order_id: UUID):
    return orders.detail(orders.get_order(request.store, order_id), request.store)


@orders_router.post("/{order_id}/status", response=_ORDER, summary="'Update Status' (processing → packed → shipped → delivered; returned after delivery)")
def order_status(request, order_id: UUID, payload: OrderStatusIn):
    order = orders.update_status(request.store, order_id, status=payload.status, note=payload.note, actor=request.auth)
    return orders.detail(order, request.store)


@orders_router.post("/{order_id}/cancel", response=_ORDER, summary="'Cancel Order' (this store's lines; stock restored)")
def order_cancel(request, order_id: UUID, payload: OrderCancelIn):
    order = orders.cancel(request.store, order_id, reason=payload.reason, actor=request.auth)
    return orders.detail(order, request.store)


@orders_router.get("/{order_id}/invoice", response={200: SellerInvoiceOut, 404: ErrorOut}, summary="'Print / Download Invoice' — invoice data (PDF layout is UI-side)")
def order_invoice(request, order_id: UUID):
    return orders.invoice(orders.get_order(request.store, order_id), request.store)
