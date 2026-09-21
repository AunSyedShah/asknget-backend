"""Cart / checkout / orders routers.

  /cart/*             members and guests (guests: X-Cart-Token header, issued by the first call)
  /delivery-methods   public
  /checkout           members and guests
  /me/orders/*        members
  /orders/track       public (order number + email)
  /admin/orders/*     staff
"""

from uuid import UUID

from ninja import Header, Query, Router
from ninja.pagination import paginate

from core.auth import JWTAuth, OptionalJWTAuth, StaffAuth
from core.pagination import PagePagination
from core.schemas import ErrorOut, MessageOut

from . import services
from .models import Order
from .schemas import (
    AdminOrderStatusIn,
    CancelIn,
    CartItemIn,
    CartItemPatchIn,
    CartOut,
    CartPatchIn,
    CheckoutIn,
    DeliveryMethodOut,
    OrderDetailOut,
    OrderPlacedOut,
    OrderSummaryOut,
    OrderTabLit,
    TrackIn,
    TrackOut,
    VoucherApplyIn,
)

cart_router = Router(tags=["cart"], auth=OptionalJWTAuth())
checkout_router = Router(tags=["checkout"], auth=OptionalJWTAuth())
my_orders_router = Router(tags=["me"], auth=JWTAuth())
orders_public_router = Router(tags=["orders"])
admin_orders_router = Router(tags=["admin · orders"], auth=StaffAuth())

_CART = {200: CartOut, 404: ErrorOut, 409: ErrorOut, 422: ErrorOut}
_TOKEN_DOC = "Guest cart token from a previous response (members can omit)"


def _cart_response(request):
    cart, token = services.resolve_cart(request)
    return services.cart_out(cart, token)


# --------------------------------------------------------------------------- #
# /cart
# --------------------------------------------------------------------------- #


@cart_router.get("", response=CartOut, summary="Current cart (creates a guest cart if none)")
def cart_get(request, x_cart_token: str | None = Header(None, description=_TOKEN_DOC)):
    return _cart_response(request)


@cart_router.post("/items", response=_CART, summary="'Add to Cart' (merges qty if the variant is already there)")
def cart_add_item(request, payload: CartItemIn, x_cart_token: str | None = Header(None, description=_TOKEN_DOC)):
    cart, token = services.resolve_cart(request)
    services.add_item(cart, variant_id=payload.variant_id, qty=payload.qty)
    return services.cart_out(services.resolve_cart(request)[0], token)


@cart_router.patch("/items/{item_id}", response=_CART, summary="Change quantity")
def cart_set_qty(request, item_id: UUID, payload: CartItemPatchIn, x_cart_token: str | None = Header(None, description=_TOKEN_DOC)):
    cart, token = services.resolve_cart(request)
    services.set_item_qty(cart, item_id, payload.qty)
    return services.cart_out(services.resolve_cart(request)[0], token)


@cart_router.delete("/items/{item_id}", response=_CART, summary="Remove line")
def cart_remove_item(request, item_id: UUID, x_cart_token: str | None = Header(None, description=_TOKEN_DOC)):
    cart, token = services.resolve_cart(request)
    services.remove_item(cart, item_id)
    return services.cart_out(services.resolve_cart(request)[0], token)


@cart_router.delete("/items", response=_CART, summary="Empty the cart ('clear cart and switch region')")
def cart_clear(request, x_cart_token: str | None = Header(None, description=_TOKEN_DOC)):
    cart, token = services.resolve_cart(request)
    services.clear_items(cart)
    return services.cart_out(services.resolve_cart(request)[0], token)


@cart_router.patch("", response=_CART, summary="Order note / delivery type")
def cart_update(request, payload: CartPatchIn, x_cart_token: str | None = Header(None, description=_TOKEN_DOC)):
    cart, token = services.resolve_cart(request)
    services.update_cart(cart, payload.dict(exclude_unset=True))
    return services.cart_out(services.resolve_cart(request)[0], token)


@cart_router.post("/voucher", response=_CART, summary="'Discount Voucher → Apply'")
def cart_apply_voucher(request, payload: VoucherApplyIn, x_cart_token: str | None = Header(None, description=_TOKEN_DOC)):
    cart, token = services.resolve_cart(request)
    services.apply_voucher(cart, payload.code)
    return services.cart_out(services.resolve_cart(request)[0], token)


@cart_router.delete("/voucher", response=_CART)
def cart_remove_voucher(request, x_cart_token: str | None = Header(None, description=_TOKEN_DOC)):
    cart, token = services.resolve_cart(request)
    services.remove_voucher(cart)
    return services.cart_out(services.resolve_cart(request)[0], token)


# --------------------------------------------------------------------------- #
# /delivery-methods, /checkout
# --------------------------------------------------------------------------- #


@orders_public_router.get("/delivery-methods", response=list[DeliveryMethodOut], summary="'Delivery Type' options")
def delivery_methods(request):
    return services.delivery_methods_for(services.market_country_id())


@checkout_router.post(
    "",
    response={201: OrderPlacedOut, 404: ErrorOut, 409: ErrorOut, 422: ErrorOut},
    summary="'Place Order' (no payment capture yet — order stays pending_payment)",
)
def checkout(request, payload: CheckoutIn, x_cart_token: str | None = Header(None, description=_TOKEN_DOC)):
    cart, _ = services.resolve_cart(request, create=False)
    order = services.checkout(request, cart, payload)
    order = services.order_qs().get(pk=order.pk)
    return 201, {
        "order": order,
        "detail": f"Thank You, {order.contact_first_name} {order.contact_last_name} your order has been placed. "
                  "We will keep you updated as it moves through preparation and delivery.",
    }


# --------------------------------------------------------------------------- #
# /me/orders
# --------------------------------------------------------------------------- #


@my_orders_router.get("/orders", response=list[OrderSummaryOut], summary="My Orders (All / Ongoing / Completed / Cancelled)")
@paginate(PagePagination)
def my_orders(request, tab: OrderTabLit = Query("all")):
    return services.my_orders(request.auth, tab)


@my_orders_router.get("/orders/{order_id}", response={200: OrderDetailOut, 404: ErrorOut}, summary="'View Detail' / 'Track Order'")
def my_order_detail(request, order_id: UUID):
    return services.get_my_order(request.auth, order_id)


@my_orders_router.post("/orders/{order_id}/cancel", response={200: OrderDetailOut, 404: ErrorOut, 409: ErrorOut})
def my_order_cancel(request, order_id: UUID, payload: CancelIn = None):
    order = services.get_my_order(request.auth, order_id)
    services.cancel(order, user=request.auth, reason=payload.reason if payload else "")
    return services.get_my_order(request.auth, order_id)


@my_orders_router.post("/orders/{order_id}/reorder", response={200: CartOut, 404: ErrorOut}, summary="'Re-Order' → items back in the cart")
def my_order_reorder(request, order_id: UUID):
    order = services.get_my_order(request.auth, order_id)
    cart, _ = services.resolve_cart(request)
    skipped = services.reorder(order, cart)
    body = services.cart_out(services.resolve_cart(request)[0])
    body["problems"] = [f"{n} is no longer available" for n in skipped] + body["problems"]
    return body


# --------------------------------------------------------------------------- #
# /orders/track (public)
# --------------------------------------------------------------------------- #


@orders_public_router.post("/orders/track", response={200: TrackOut, 404: ErrorOut}, summary="Track Order by ID + email")
def track_order(request, payload: TrackIn):
    order = services.track(order_number=payload.order_number, email=payload.email)
    return {
        "order_number": order.order_number, "status": order.status, "status_label": order.get_status_display(),
        "placed_at": order.placed_at, "delivery_method": order.delivery_method_snapshot,
        "items": order.items.all(), "events": order.status_events.all(),
    }


# --------------------------------------------------------------------------- #
# /admin/orders
# --------------------------------------------------------------------------- #


@admin_orders_router.get("", response=list[OrderSummaryOut])
@paginate(PagePagination)
def admin_orders(request, status: str | None = None, q: str | None = None):
    qs = services.order_qs()
    if status:
        qs = qs.filter(status=status)
    if q:
        qs = qs.filter(order_number__icontains=q) | qs.filter(contact_email__icontains=q)
    return qs


@admin_orders_router.get("/{order_id}", response={200: OrderDetailOut, 404: ErrorOut})
def admin_order_detail(request, order_id: UUID):
    order = services.order_qs().filter(pk=order_id).first()
    if order is None:
        return 404, {"detail": "Order not found"}
    return order


@admin_orders_router.post("/{order_id}/status", response={200: OrderDetailOut, 404: ErrorOut, 409: ErrorOut}, summary="Advance an order (writes the tracking timeline)")
def admin_order_status(request, order_id: UUID, payload: AdminOrderStatusIn):
    order = services.order_qs().filter(pk=order_id).first()
    if order is None:
        return 404, {"detail": "Order not found"}
    services.set_status(order, status=payload.status, actor_user=request.auth, note=payload.note, location=payload.location)
    return services.order_qs().get(pk=order_id)
