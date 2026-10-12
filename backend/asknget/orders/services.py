"""Cart, voucher, checkout and order rules.

Payment provider integration is deliberately absent this sprint: checkout
records `payment_method_type`, leaves the order `pending_payment` / `unpaid`
and the PSP hook will move it to `confirmed` / `paid` later.
"""

import secrets

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import F, Prefetch, Sum
from django.utils import timezone
from ninja.errors import HttpError

from accounts.models import Address, PaymentMethod
from catalog.models import Product, ProductVariant, StockMovement
from catalog.services import adjust_stock, lock_variants
from discovery import events
from markets import services as markets
from markets.context import get_country
from sellers.models import Store

from .models import Cart, CartItem, Order, OrderDelivery, OrderItem, OrderStatusEvent, Voucher, VoucherRedemption

CART_TOKEN_HEADER = "X-Cart-Token"
Status = Order.OrderStatus
Fulfilment = OrderItem.FulfilmentStatus

# --------------------------------------------------------------------------- #
# Cart resolution (members + guests)
# --------------------------------------------------------------------------- #


def _user(request):
    auth = getattr(request, "auth", None)
    return auth if getattr(auth, "pk", None) else None


def _cart_qs():
    return Cart.objects.select_related("voucher").prefetch_related(
        Prefetch(
            "items",
            queryset=CartItem.objects.select_related("variant__product__store", "variant__product__category")
            .prefetch_related("variant__product__delivery_options")
            .prefetch_related("variant__option_values__option_type", "variant__product__images")
            .order_by("created_at"),
        )
    )


def resolve_cart(request, *, create: bool = True) -> tuple[Cart, str | None]:
    """Member → their cart (merging any guest cart named by X-Cart-Token).
    Guest → cart for the token, or a brand-new one whose token the response returns."""
    user = _user(request)
    token = request.headers.get(CART_TOKEN_HEADER) or None
    if user is not None:
        cart = _cart_qs().filter(user=user).first()  # one query in the common case
        if cart is None:
            cart = _cart_qs().get(pk=Cart.objects.create(user=user).pk)
        if token:
            guest = Cart.objects.filter(session_token=token, user__isnull=True).first()
            if guest is not None:
                _merge(guest, into=cart)
                cart = _cart_qs().get(pk=cart.pk)  # prefetched lines changed
        return cart, None
    if token:
        cart = _cart_qs().filter(session_token=token, user__isnull=True).first()
        if cart is not None:
            return cart, token
    if not create:
        raise HttpError(404, "Cart not found")
    token = secrets.token_urlsafe(32)
    cart = Cart.objects.create(session_token=token, expires_at=timezone.now() + timezone.timedelta(days=30))
    return _cart_qs().get(pk=cart.pk), token


def cart_country_id(cart: Cart) -> str | None:
    """A cart belongs to the market of its first line (lines are already prefetched).
    An empty cart belongs to no market yet — the first item decides."""
    first = next(iter(cart.items.all()), None)
    return first.variant.product.country_id if first else None


def market_country_id() -> str | None:
    country = get_country()
    return country.code if country else None


def _cart_target_country(cart: Cart) -> str | None:
    return cart_country_id(cart) or market_country_id()


@transaction.atomic
def _merge(guest: Cart, *, into: Cart) -> None:
    target = _cart_target_country(into)
    for item in guest.items.select_related("variant__product"):
        if target and item.variant.product.country_id != target:
            continue  # a guest line from another market never crosses into this cart
        line, created = CartItem.objects.get_or_create(cart=into, variant=item.variant, defaults={"qty": item.qty})
        if not created:
            line.qty = min(line.qty + item.qty, 99)
            line.save(update_fields=["qty", "updated_at"])
    if guest.order_note and not into.order_note:
        into.order_note = guest.order_note
    if guest.voucher_id and not into.voucher_id:
        into.voucher = guest.voucher
    into.save()
    guest.delete()


# --------------------------------------------------------------------------- #
# Pricing
# --------------------------------------------------------------------------- #


def options_for_store(store_id):
    from sellers.models import StoreDeliveryOption

    return StoreDeliveryOption.objects.filter(store_id=store_id, is_active=True)


def _product_options(product) -> set:
    """Options a single product supports. Empty selection means *all* of the
    store's active options — a product is never unbuyable for want of a tick."""
    chosen = {o.id for o in product.delivery_options.all() if o.is_active}
    return chosen or {o.id for o in options_for_store(product.store_id)}


def _default_option(options: list):
    """Cheapest *delivery* option, not simply the cheapest: pickup is usually free
    and silently defaulting a buyer to collecting in person would be wrong."""
    shipped = [o for o in options if o.kind != "pickup"]
    return min(shipped or options, key=lambda o: o.price_cents, default=None)


def cart_delivery_groups(cart: Cart) -> list[dict]:
    """One group per store in the cart: the options every line in that group
    supports, and which one is selected.

    Shipping is per seller because each seller sets their own prices; a cart with
    three sellers has three shipping lines (docs/sprint-6-plan.md §3).
    """
    from sellers.models import StoreDeliveryOption

    by_store: dict = {}
    for item in cart.items.all():
        product = item.variant.product
        group = by_store.setdefault(product.store_id, {"store": product.store, "supported": None})
        supported = _product_options(product)
        group["supported"] = supported if group["supported"] is None else (group["supported"] & supported)

    if not by_store:
        return []
    everything = {
        o.id: o for o in StoreDeliveryOption.objects.filter(store_id__in=by_store, is_active=True)
    }
    selections = cart.delivery_selections or {}
    groups = []
    for store_id, group in by_store.items():
        options = sorted(
            (everything[oid] for oid in (group["supported"] or set()) if oid in everything),
            key=lambda o: (o.sort_order, o.price_cents, o.name),
        )
        chosen_id = selections.get(str(store_id))
        chosen = next((o for o in options if str(o.id) == str(chosen_id)), None) or _default_option(options)
        groups.append({
            "store_id": store_id,
            "store_name": group["store"].display_name,
            "options": options,
            "selected": chosen,
        })
    return groups


def set_cart_delivery(cart: Cart, *, store_id, option_id) -> None:
    group = next((g for g in cart_delivery_groups(cart) if str(g["store_id"]) == str(store_id)), None)
    if group is None:
        raise HttpError(404, "That store has nothing in your cart")
    if not any(str(o.id) == str(option_id) for o in group["options"]):
        raise HttpError(422, "That delivery option is not available for these items")
    selections = dict(cart.delivery_selections or {})
    selections[str(store_id)] = str(option_id)
    cart.delivery_selections = selections
    cart.save(update_fields=["delivery_selections", "updated_at"])


def _variant_purchasable(variant: ProductVariant) -> bool:
    p = variant.product
    return variant.is_active and p.status == Product.ProductStatus.ACTIVE and p.deleted_at is None and p.store.status == Store.StoreStatus.ACTIVE


def _option_out(option) -> dict:
    return {
        "id": option.id, "name": option.name, "eta_label": option.eta_label,
        "kind": option.kind, "price_cents": option.price_cents,
    }


def line_out(item: CartItem) -> dict:
    v, p = item.variant, item.variant.product
    images = list(p.images.all())
    primary = next((i for i in images if i.is_primary), images[0] if images else None)
    unit = v.effective_price_cents
    return {
        "id": item.id,
        "variant_id": v.id,
        "product_id": p.id,
        "product_slug": p.slug,
        "product_name": p.name,
        "variant_summary": " | ".join(
            f"{o.option_type.name}: {o.value}" for o in sorted(v.option_values.all(), key=lambda o: o.option_type.sort_order)
        ),
        "image_url": primary.image.url if primary else None,
        "store_name": p.store.display_name,
        "unit_price_cents": unit,
        "compare_at_price_cents": v.compare_at_price_cents if v.price_cents is not None else p.compare_at_price_cents,
        "qty": item.qty,
        "line_total_cents": unit * item.qty,
        "currency": p.currency,
        "available_qty": v.stock_qty,
        "in_stock": _variant_purchasable(v) and v.stock_qty >= item.qty,
    }


def voucher_discount(voucher: Voucher | None, subtotal: int) -> int:
    if voucher is None:
        return 0
    if voucher.type == Voucher.VoucherType.PERCENT:
        discount = subtotal * voucher.value // 100
    else:
        discount = voucher.value
    if voucher.max_discount_cents:
        discount = min(discount, voucher.max_discount_cents)
    return min(discount, subtotal)


def store_discount_cents(order, store_id) -> int:
    """This store's share of an order-level voucher, pro-rata on its share of the
    subtotal (Q49). Largest-remainder rounding, so the per-store parts always add
    back up to `order.discount_cents` — no cent appears or vanishes.

    Used by the seller's Sales Analytics today and by payouts later; it is the only
    place that decides what a discount means to one seller in a multi-store order.
    """
    if not order.discount_cents:
        return 0
    totals = dict(
        OrderItem.objects.filter(order=order).values_list("store_id")
        .annotate(t=Sum("line_total_cents")).values_list("store_id", "t")
    )
    subtotal = sum(totals.values())
    if not subtotal:
        return 0
    exact = {sid: order.discount_cents * t / subtotal for sid, t in totals.items()}
    shares = {sid: int(v) for sid, v in exact.items()}
    # hand out the rounding remainder to the largest fractional parts first
    for sid, _ in sorted(exact.items(), key=lambda kv: kv[1] - int(kv[1]), reverse=True)[: order.discount_cents - sum(shares.values())]:
        shares[sid] += 1
    return shares.get(store_id, 0)


def voucher_label(voucher: Voucher) -> str:
    return f"Discount ({voucher.value}%)" if voucher.type == Voucher.VoucherType.PERCENT else "Discount"


def validate_voucher(voucher: Voucher, *, subtotal: int, user, country_id: str | None = None) -> str | None:
    """Return a human reason the voucher can't be used, or None."""
    now = timezone.now()
    if not voucher.is_active:
        return "This voucher is no longer active"
    if voucher.country_id and country_id and voucher.country_id != country_id:
        return "This voucher is not valid in your region"
    if voucher.starts_at and voucher.starts_at > now:
        return "This voucher is not valid yet"
    if voucher.ends_at and voucher.ends_at < now:
        return "This voucher has expired"
    if voucher.usage_limit is not None and voucher.used_count >= voucher.usage_limit:
        return "This voucher has been fully redeemed"
    if voucher.min_subtotal_cents and subtotal < voucher.min_subtotal_cents:
        return f"Add {(voucher.min_subtotal_cents - subtotal) / 100:.2f} more to use this voucher"
    if user is not None and voucher.per_user_limit is not None:
        used = VoucherRedemption.objects.filter(voucher=voucher, user=user).count()
        if used >= voucher.per_user_limit:
            return "You have already used this voucher"
    return None


def cart_out(cart: Cart, token: str | None = None) -> dict:
    lines = [line_out(i) for i in cart.items.all()]
    country_id = _cart_target_country(cart)
    subtotal = sum(l["line_total_cents"] for l in lines)
    groups = cart_delivery_groups(cart)
    shipping = sum(g["selected"].price_cents for g in groups if g["selected"])
    problems: list[str] = []
    if not lines:
        problems.append("Your cart is empty")
    problems += [f"{g['store_name']}: no delivery option covers these items" for g in groups if not g["selected"]]
    problems += [f"{l['product_name']}: only {l['available_qty']} left" for l in lines if not l["in_stock"]]
    voucher, discount = cart.voucher, 0
    if voucher is not None:
        reason = validate_voucher(voucher, subtotal=subtotal, user=cart.user, country_id=country_id)
        if reason:
            problems.append(f"Voucher {voucher.code}: {reason}")
        else:
            discount = voucher_discount(voucher, subtotal)
    currency = lines[0]["currency"] if lines else markets.current_currency()
    return {
        "id": cart.id,
        "cart_token": token,
        "country": country_id,
        "items": lines,
        "order_note": cart.order_note,
        "voucher": {"code": voucher.code, "type": voucher.type, "value": voucher.value, "label": voucher_label(voucher)} if voucher else None,
        "deliveries": [
            {
                "store_id": g["store_id"],
                "store_name": g["store_name"],
                "options": [_option_out(o) for o in g["options"]],
                "selected_option_id": g["selected"].id if g["selected"] else None,
                "price_cents": g["selected"].price_cents if g["selected"] else 0,
            }
            for g in groups
        ],
        "summary": {
            "subtotal_cents": subtotal,
            "shipping_cents": shipping,
            "discount_cents": discount,
            "total_cents": max(subtotal + shipping - discount, 0),
            "currency": currency,
            "item_count": sum(l["qty"] for l in lines),
        },
        "can_checkout": not problems,
        "problems": problems,
    }


# --------------------------------------------------------------------------- #
# Cart mutations
# --------------------------------------------------------------------------- #


def add_item(cart: Cart, *, variant_id, qty: int) -> None:
    variant = ProductVariant.objects.select_related("product__store").filter(pk=variant_id).first()
    if variant is None or not _variant_purchasable(variant):
        raise HttpError(404, "This item is not available")
    if variant.stock_qty <= 0:
        raise HttpError(409, "This item is out of stock")
    current = cart_country_id(cart)
    if current and variant.product.country_id != current:
        # One market per cart: currencies, delivery methods and return rules differ.
        # The UI offers "clear cart and switch" (DELETE /cart/items).
        raise HttpError(409, "cart_region_mismatch")
    line, created = CartItem.objects.get_or_create(cart=cart, variant=variant, defaults={"qty": min(qty, variant.stock_qty)})
    if not created:
        line.qty = min(line.qty + qty, variant.stock_qty, 99)
        line.save(update_fields=["qty", "updated_at"])
    else:
        events.record_for_cart(events.ProductEvent.Kind.ADD_TO_CART, product=variant.product, cart=cart)


def set_item_qty(cart: Cart, item_id, qty: int) -> None:
    line = CartItem.objects.select_related("variant").filter(pk=item_id, cart=cart).first()
    if line is None:
        raise HttpError(404, "Cart item not found")
    if qty > line.variant.stock_qty:
        raise HttpError(409, f"Only {line.variant.stock_qty} left in stock")
    line.qty = qty
    line.save(update_fields=["qty", "updated_at"])


def remove_item(cart: Cart, item_id) -> None:
    if not CartItem.objects.filter(pk=item_id, cart=cart).delete()[0]:
        raise HttpError(404, "Cart item not found")


def clear_items(cart: Cart) -> None:
    """Empty the cart (keeps note / voucher) — the 'clear cart and switch region' action."""
    CartItem.objects.filter(cart=cart).delete()


def update_cart(cart: Cart, data: dict) -> None:
    if "order_note" in data:
        cart.order_note = data["order_note"] or ""
    cart.save()


def apply_voucher(cart: Cart, code: str) -> None:
    voucher = Voucher.objects.filter(code__iexact=code.strip()).first()
    if voucher is None:
        raise HttpError(404, "Voucher code not recognised")
    subtotal = sum(i.variant.effective_price_cents * i.qty for i in cart.items.all())
    reason = validate_voucher(voucher, subtotal=subtotal, user=cart.user, country_id=_cart_target_country(cart))
    if reason:
        raise HttpError(422, reason)
    cart.voucher = voucher
    cart.save(update_fields=["voucher", "updated_at"])


def remove_voucher(cart: Cart) -> None:
    cart.voucher = None
    cart.save(update_fields=["voucher", "updated_at"])


# --------------------------------------------------------------------------- #
# Checkout
# --------------------------------------------------------------------------- #


def _order_number() -> str:
    while True:
        candidate = f"ORD-{secrets.randbelow(10**6):06d}"
        if not Order.objects.filter(order_number=candidate).exists():
            return candidate


def _address_dict(addr: Address) -> dict:
    return {
        "address": " ".join(p for p in (addr.street_address, addr.unit_floor) if p), "city": addr.city,
        "postal_code": addr.postal_code, "province": addr.province, "country": addr.country,
        "delivery_instructions": addr.delivery_instructions,
    }


def _record_event(order: Order, status: str, *, actor="system", actor_user=None, note: str = "", location: str = "") -> None:
    OrderStatusEvent.objects.create(order=order, status=status, actor=actor, actor_user=actor_user, note=note, location=location)


@transaction.atomic
def checkout(request, cart: Cart, payload) -> Order:
    user = _user(request)
    view = cart_out(cart)
    if not view["can_checkout"]:
        raise HttpError(409, "; ".join(view["problems"]))

    # Addresses
    if payload.address_id:
        if user is None:
            raise HttpError(422, "Sign in to use a saved address")
        saved = Address.objects.filter(pk=payload.address_id, user=user).first()
        if saved is None:
            raise HttpError(404, "Saved address not found")
        primary = _address_dict(saved)
    else:
        primary = payload.address.dict()
    shipping = payload.shipping_address.dict() if payload.ship_to_different_address else primary
    billing = primary if payload.ship_to_different_address else None

    # One market per order: the cart's country decides currency, delivery methods and
    # the return window. A blank address country means "this market"; another country
    # cannot be shipped to from here.
    country_id = view["country"]
    for addr in (primary, shipping):
        addr["country"] = (addr.get("country") or country_id or "").upper()
    if shipping["country"] != country_id:
        raise HttpError(409, "ships_outside_region")
    active = get_country()
    country = active if (active and active.code == country_id) else markets.countries_qs().get(pk=country_id)

    # Shipping is per store: apply any explicit choices, then read back what the
    # cart resolved to. A store with no usable option blocks checkout.
    for choice in (payload.deliveries or []):
        set_cart_delivery(cart, store_id=choice.store_id, option_id=choice.option_id)
    groups = cart_delivery_groups(cart)
    missing = [g["store_name"] for g in groups if not g["selected"]]
    if missing:
        raise HttpError(409, f"No delivery option available for: {', '.join(missing)}")

    if payload.payment_method_id:
        if user is None or not PaymentMethod.objects.filter(pk=payload.payment_method_id, user=user).exists():
            raise HttpError(404, "Saved payment method not found")

    # Lock stock rows, then re-check quantities.
    items = list(cart.items.select_related("variant__product__store"))
    variants = {v.id: v for v in lock_variants(i.variant_id for i in items)}
    for item in items:
        v = variants[item.variant_id]
        if v.stock_qty < item.qty:
            raise HttpError(409, f"{item.variant.product.name}: only {v.stock_qty} left")

    subtotal = view["summary"]["subtotal_cents"]
    discount = view["summary"]["discount_cents"]
    shipping_cents = sum(g["selected"].price_cents for g in groups)
    total = max(subtotal + shipping_cents - discount, 0)

    order = Order.objects.create(
        order_number=_order_number(),
        user=user,
        country=country,
        return_window_days=country.return_window_days,
        contact_first_name=payload.contact.first_name,
        contact_last_name=payload.contact.last_name,
        contact_email=payload.contact.email,
        contact_phone=payload.contact.phone,
        shipping_address=shipping,
        billing_address=billing,
        delivery_method_snapshot={
            "per_store": [
                {"store": g["store_name"], "name": g["selected"].name, "eta_label": g["selected"].eta_label,
                 "price_cents": g["selected"].price_cents}
                for g in groups
            ],
        },
        special_note=payload.special_note or cart.order_note,
        subtotal_cents=subtotal,
        shipping_cents=shipping_cents,
        discount_cents=discount,
        voucher_code=cart.voucher.code if cart.voucher_id else "",
        total_cents=total,
        currency=view["summary"]["currency"],
        payment_method_type=payload.payment_method_type,
        channel=getattr(payload, "channel", "web"),
    )
    for g in groups:
        option = g["selected"]
        OrderDelivery.objects.create(
            order=order, store_id=g["store_id"], option=option, name=option.name,
            eta_label=option.eta_label, kind=option.kind, price_cents=option.price_cents,
        )
    for item in items:
        v, line = variants[item.variant_id], line_out(item)
        OrderItem.objects.create(
            order=order, store=v.product.store, product=v.product, variant=v,
            product_name=line["product_name"], variant_summary=line["variant_summary"], image_url=line["image_url"] or "",
            qty=item.qty, unit_price_cents=line["unit_price_cents"], compare_at_price_cents=line["compare_at_price_cents"],
            line_total_cents=line["line_total_cents"],
        )
        adjust_stock(v, delta=-item.qty, reason=StockMovement.Reason.SALE, order=order, locked=True)
        events.record_for_cart(events.ProductEvent.Kind.PURCHASE, product=v.product, cart=cart)
    if cart.voucher_id:
        VoucherRedemption.objects.create(voucher=cart.voucher, user=user, order=order)
        Voucher.objects.filter(pk=cart.voucher_id).update(used_count=F("used_count") + 1)
    _record_event(order, Status.PENDING_PAYMENT, actor="buyer", actor_user=user, note="Order placed")

    cart.items.all().delete()
    cart.voucher = None
    cart.order_note = ""
    cart.save()

    send_mail(
        f"asknget order {order.order_number} received",
        f"Thank you, {order.contact_first_name} {order.contact_last_name}. Your order {order.order_number} has been placed.",
        None, [order.contact_email], fail_silently=True,
    )
    return order


# --------------------------------------------------------------------------- #
# Orders
# --------------------------------------------------------------------------- #


def order_qs():
    return Order.objects.select_related("delivery_method").prefetch_related(
        Prefetch("items", queryset=OrderItem.objects.select_related("store", "product", "order", "review")), "status_events"
    )


def my_orders(user, tab: str):
    qs = order_qs().filter(user=user)
    if tab == "ongoing":
        qs = qs.filter(status__in=Order.ONGOING_STATUSES)
    elif tab == "completed":
        qs = qs.filter(status__in=Order.COMPLETED_STATUSES)
    elif tab == "cancelled":
        qs = qs.filter(status__in=Order.CANCELLED_STATUSES)
    return qs


def get_my_order(user, order_id) -> Order:
    order = order_qs().filter(pk=order_id, user=user).first()
    if order is None:
        raise HttpError(404, "Order not found")
    return order


@transaction.atomic
def cancel(order: Order, *, user, reason: str) -> Order:
    if not order.can_cancel:
        raise HttpError(409, f"Orders that are {order.get_status_display().lower()} can no longer be cancelled")
    _restock(order, order.items.all(), reason=StockMovement.Reason.CANCEL, actor_user=user)
    now = timezone.now()
    order.status = Status.CANCELLED
    order.cancelled_at = now
    order.cancel_reason = reason
    order.cancelled_by = user
    order.save()
    order.items.update(status=Fulfilment.CANCELLED)
    _record_event(order, Status.CANCELLED, actor="buyer", actor_user=user, note=reason or "Cancelled by customer")
    on_order_money_returned(order)
    on_order_cancelled(order, by="customer")
    return order


def _restock(order: Order, items, *, reason: str, actor_user=None) -> None:
    items = [i for i in items if i.variant_id and i.status not in (Fulfilment.CANCELLED, Fulfilment.RETURNED)]
    locked = {v.id: v for v in lock_variants(i.variant_id for i in items)}
    for item in items:
        adjust_stock(locked[item.variant_id], delta=item.qty, reason=reason, order=order, actor_user=actor_user, locked=True)


def reorder(order: Order, cart: Cart) -> list[str]:
    """'Re-Order': put every still-available line back in the cart. Returns skipped names."""
    skipped = []
    for item in order.items.all():
        try:
            add_item(cart, variant_id=item.variant_id, qty=item.qty)
        except HttpError:
            skipped.append(item.product_name)
    return skipped


def track(*, order_number: str, email: str) -> Order:
    order = order_qs().filter(order_number__iexact=order_number.strip(), contact_email__iexact=email).first()
    if order is None:
        raise HttpError(404, "No order matches that order ID and email")
    return order


# --------------------------------------------------------------------------- #
# Money events. One place each, so the ledger (and later notifications) hang off the
# same moments no matter who triggered them: the buyer, staff, a seller or a PSP webhook.
# Imports are local: the ledger imports orders, not the other way round.
# --------------------------------------------------------------------------- #


def on_payment_confirmed(order: Order) -> None:
    from ledger import services as ledger
    from notifications import services as notifications

    ledger.post_sales(order)
    notifications.order_paid(order)


def on_order_cancelled(order: Order, *, by: str) -> None:
    """Tell the sellers who had already seen this order that it is gone."""
    from notifications import services as notifications

    notifications.order_cancelled(order, by=by)


def on_goods_delivered(order: Order, store_id=None) -> None:
    from ledger import services as ledger

    ledger.release(order, store_id)


def on_order_money_returned(order: Order, store_id=None) -> None:
    """Cancelled, returned or refunded: whatever was paid for these lines goes back."""
    from ledger import services as ledger

    ledger.refund(order, store_id)


# --------------------------------------------------------------------------- #
# Admin (order-level) and seller (per-store) status changes
# --------------------------------------------------------------------------- #

# Ops override of the whole buyer order — keeps the timeline honest.
ALLOWED_NEXT = {
    Status.PENDING_PAYMENT: {Status.CONFIRMED, Status.CANCELLED},
    Status.CONFIRMED: {Status.PROCESSING, Status.CANCELLED},
    Status.PROCESSING: {Status.PACKED, Status.SHIPPED, Status.CANCELLED},
    Status.PACKED: {Status.SHIPPED, Status.CANCELLED},
    Status.SHIPPED: {Status.OUT_FOR_DELIVERY, Status.DELIVERED},
    Status.OUT_FOR_DELIVERY: {Status.DELIVERED},
    Status.DELIVERED: {Status.RETURNED, Status.REFUNDED},
    Status.CANCELLED: {Status.REFUNDED},
    Status.RETURNED: {Status.REFUNDED},
    Status.REFUNDED: set(),
}

# Order status → what every store's lines become when ops move the whole order.
ORDER_TO_FULFILMENT = {
    Status.PENDING_PAYMENT: Fulfilment.PENDING, Status.CONFIRMED: Fulfilment.PENDING,
    Status.PROCESSING: Fulfilment.PROCESSING, Status.PACKED: Fulfilment.PACKED,
    Status.SHIPPED: Fulfilment.SHIPPED, Status.OUT_FOR_DELIVERY: Fulfilment.SHIPPED,
    Status.DELIVERED: Fulfilment.DELIVERED, Status.CANCELLED: Fulfilment.CANCELLED,
    Status.RETURNED: Fulfilment.RETURNED, Status.REFUNDED: None,  # refund doesn't change fulfilment
}

# Seller moves of one store's lines (docs/sprint-3-plan.md §3).
FULFILMENT_NEXT = {
    Fulfilment.PENDING: {Fulfilment.PROCESSING, Fulfilment.CANCELLED},
    Fulfilment.PROCESSING: {Fulfilment.PACKED, Fulfilment.SHIPPED, Fulfilment.CANCELLED},
    Fulfilment.PACKED: {Fulfilment.SHIPPED, Fulfilment.CANCELLED},
    Fulfilment.SHIPPED: {Fulfilment.DELIVERED},
    Fulfilment.DELIVERED: {Fulfilment.RETURNED},
    Fulfilment.CANCELLED: set(),
    Fulfilment.RETURNED: set(),
}

# Fulfilment → Order.OrderStatus vocabulary for the per-store timeline events.
FULFILMENT_TO_ORDER = {
    Fulfilment.PENDING: Status.CONFIRMED, Fulfilment.PROCESSING: Status.PROCESSING, Fulfilment.PACKED: Status.PACKED,
    Fulfilment.SHIPPED: Status.SHIPPED, Fulfilment.DELIVERED: Status.DELIVERED,
    Fulfilment.CANCELLED: Status.CANCELLED, Fulfilment.RETURNED: Status.RETURNED,
}


@transaction.atomic
def set_status(order: Order, *, status: str, actor_user, note: str = "", location: str = "") -> Order:
    if status not in ALLOWED_NEXT[order.status]:
        raise HttpError(409, f"Cannot move order from {order.status} to {status}")
    if status == Status.CANCELLED:
        _restock(order, order.items.all(), reason=StockMovement.Reason.CANCEL, actor_user=actor_user)
        order.cancelled_at = timezone.now()
        order.cancelled_by = actor_user
        order.cancel_reason = note
    elif status == Status.RETURNED:
        _restock(order, order.items.all(), reason=StockMovement.Reason.RETURN, actor_user=actor_user)
    just_paid = False
    if status == Status.CONFIRMED and order.payment_status == Order.OrderPaymentStatus.UNPAID:
        order.payment_status = Order.OrderPaymentStatus.PAID  # manual confirmation (bank transfer) until the PSP lands
        just_paid = True
    order.status = status
    order.save()
    if ORDER_TO_FULFILMENT[status] is not None:
        order.items.update(status=ORDER_TO_FULFILMENT[status])
    _record_event(order, status, actor="admin", actor_user=actor_user, note=note, location=location)
    if just_paid:
        on_payment_confirmed(order)
    if status == Status.DELIVERED:
        on_goods_delivered(order)
    elif status in (Status.CANCELLED, Status.RETURNED, Status.REFUNDED):
        on_order_money_returned(order)
        if status == Status.CANCELLED:
            on_order_cancelled(order, by="staff")
    return order


def derive_order_status(order: Order) -> str:
    """Buyer-facing status from the per-store parts. Unpaid and refunded orders are left alone.
    Queries OrderItem directly: `order.items` may be a store-filtered prefetch."""
    if order.status in (Status.PENDING_PAYMENT, Status.REFUNDED):
        return order.status
    parts = set(OrderItem.objects.filter(order=order).values_list("status", flat=True))
    live = parts - {Fulfilment.CANCELLED}
    if not live:
        status = Status.CANCELLED
    elif live <= {Fulfilment.DELIVERED, Fulfilment.RETURNED}:
        status = Status.RETURNED if live == {Fulfilment.RETURNED} else Status.DELIVERED
    elif Fulfilment.SHIPPED in live:
        status = Status.SHIPPED
    elif live - {Fulfilment.PENDING}:  # some store has started (or finished) its part
        status = Status.PROCESSING
    else:
        status = Status.CONFIRMED
    if status != order.status:
        if status == Status.CANCELLED and not order.cancelled_at:
            order.cancelled_at = timezone.now()
        order.status = status
        order.save(update_fields=["status", "cancelled_at", "updated_at"])
    return order.status


@transaction.atomic
def set_store_status(order: Order, store, *, status: str, actor_user, note: str = "") -> Order:
    """A seller moves *their* lines of an order. Records a per-store timeline event,
    restocks on cancel/return and re-derives the buyer-facing order status."""
    items = list(OrderItem.objects.filter(order=order, store=store).select_related("variant"))
    if not items:
        raise HttpError(404, "Order not found")
    current = items[0].status
    if status not in FULFILMENT_NEXT[current]:
        raise HttpError(409, f"Cannot move this order from {current} to {status}")
    if status == Fulfilment.CANCELLED:
        _restock(order, items, reason=StockMovement.Reason.CANCEL, actor_user=actor_user)
    elif status == Fulfilment.RETURNED:
        _restock(order, items, reason=StockMovement.Reason.RETURN, actor_user=actor_user)
    OrderItem.objects.filter(order=order, store=store).update(status=status)
    OrderStatusEvent.objects.create(
        order=order, store=store, status=FULFILMENT_TO_ORDER[status], note=note[:255],
        actor=OrderStatusEvent.OrderEventActor.SELLER, actor_user=actor_user,
    )
    derive_order_status(order)
    if status == Fulfilment.DELIVERED:
        on_goods_delivered(order, store.pk)
    elif status in (Fulfilment.CANCELLED, Fulfilment.RETURNED):
        on_order_money_returned(order, store.pk)
    return order
