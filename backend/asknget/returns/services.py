"""The return workflow.

    requested -> approved -> refunded            (seller approves, the buyer ships it back, the seller receives it)
        |            |  \\-> received -> refunded | rejected   (arrived damaged: staff decide)
        |            +-> cancelled (the buyer withdraws before it is sent)
        +-> rejected -> escalated -> approved | rejected   (the buyer disputes: staff decide, and that is final)
        +-> cancelled

Money moves once, in `_complete`, through `ledger.refund_items`. Everything that changes a request goes through a
function here, so the timeline (`ReturnEvent`), the order's lines, stock, the ledger and the notifications cannot
disagree.
"""

import logging
from datetime import timedelta
from io import BytesIO

from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone
from ninja.errors import HttpError
from PIL import Image

from catalog.models import StockMovement
from catalog.services import adjust_stock, lock_variants
from config import services as config
from markets.i18n import pick
from orders import services as order_services
from orders.models import Order, OrderDelivery, OrderItem, OrderStatusEvent

from . import couriers, policy
from .models import ReturnEvent, ReturnItem, ReturnPhoto, ReturnReason, ReturnRequest, ReturnShipment

log = logging.getLogger(__name__)
S = ReturnRequest.Status
Actor = ReturnEvent.Actor
MAX_PHOTO_BYTES = 5 * 1024 * 1024


def _round_div(numerator: int, denominator: int) -> int:
    return (2 * numerator + denominator) // (2 * denominator)


def _event(request, actor, status=None, note="", user=None) -> None:
    ReturnEvent.objects.create(request=request, status=status or request.status, actor=actor, actor_user=user, note=note[:500])


def _mail(request, subject: str, body: str) -> None:
    send_mail(f"asknget return {request.reference}: {subject}", body, None, [request.order.contact_email], fail_silently=True)


def _name(request) -> str:
    return request.order.contact_first_name or "there"


# --------------------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------------------


def reasons_for(country):
    """The reasons offered in a market: those with no market restriction, plus those that include it."""
    from django.db.models import Q

    return ReturnReason.objects.filter(Q(countries__isnull=True) | Q(countries=country), is_active=True).distinct().order_by("sort_order", "code")


def request_qs():
    return ReturnRequest.objects.select_related("order", "store", "reason", "country").prefetch_related(
        "items__order_item", "photos", "events", "shipments"
    )


def list_qs():
    """Lean rows for the list endpoints: no photos, timeline or shipments, and the line count comes from SQL."""
    from django.db.models import Count

    return ReturnRequest.objects.select_related("order", "store", "reason", "country").annotate(items_count=Count("items"))


def my_requests(user, tab: str | None = None):
    return _tab(list_qs().filter(user=user), tab)


def store_requests(store, tab: str | None = None):
    return _tab(list_qs().filter(store=store), tab)


def all_requests(tab: str | None = None):
    """Staff queue: what needs a decision first (escalated disputes, damaged parcels), then newest."""
    from django.db.models import Case, IntegerField, Value, When

    qs = _tab(list_qs(), tab)
    return qs.annotate(_urgency=Case(When(status__in=(S.ESCALATED, S.RECEIVED), then=Value(0)), default=Value(1), output_field=IntegerField())).order_by("_urgency", "-created_at")


def _tab(qs, tab):
    if tab == "open":
        return qs.filter(status__in=(S.REQUESTED, S.APPROVED, S.ESCALATED, S.RECEIVED))
    if tab in {s.value for s in S}:
        return qs.filter(status=tab)
    if tab == "overdue":
        return qs.filter(status=S.REQUESTED, response_due_at__lt=timezone.now())
    return qs


def get_mine(user, request_id) -> ReturnRequest:
    found = request_qs().filter(pk=request_id, user=user).first()
    if found is None:
        raise HttpError(404, "Return not found")
    return found


def get_for_store(store, request_id) -> ReturnRequest:
    found = request_qs().filter(pk=request_id, store=store).first()
    if found is None:
        raise HttpError(404, "Return not found")
    return found


def get_any(request_id) -> ReturnRequest:
    found = request_qs().filter(pk=request_id).first()
    if found is None:
        raise HttpError(404, "Return not found")
    return found


def is_overdue(r: ReturnRequest) -> bool:
    return r.status == S.REQUESTED and r.response_due_at is not None and r.response_due_at < timezone.now()


def has_open_request(order, store_id=None) -> bool:
    qs = ReturnRequest.objects.filter(order=order, status__in=ReturnRequest.OPEN)
    return (qs.filter(store_id=store_id) if store_id else qs).exists()


def assert_no_open_request(order, store_id=None) -> None:
    """Called before an order or store is moved to returned/refunded by hand, so a goodwill return cannot pay a line
    that a customer's return request is already dealing with."""
    if has_open_request(order, store_id):
        raise HttpError(409, "return_in_progress")


# --------------------------------------------------------------------------------------
# Creating
# --------------------------------------------------------------------------------------


def _check_photos(files, reason, country) -> list:
    files = list(files or [])
    limit = config.get("returns.max_photos")
    if len(files) > limit:
        raise HttpError(422, f"At most {limit} photos")
    if reason.requires_photo and not files:
        raise HttpError(422, "photo_required")
    for f in files:
        if f.size > MAX_PHOTO_BYTES:
            raise HttpError(422, "Each photo must be 5 MB or smaller")
        try:
            Image.open(BytesIO(f.read())).verify()
        except Exception:
            raise HttpError(422, f"{f.name} is not an image")
        finally:
            f.seek(0)
    return files


def _refund_amounts(order, store_id, items, reason) -> dict:
    """What a return of `items` (one store's lines) refunds. Whole lines only."""
    gross = sum(i.line_total_cents for i in items)
    store_lines = list(OrderItem.objects.filter(order=order, store_id=store_id))
    store_gross = sum(i.line_total_cents for i in store_lines)
    discount = _round_div(order_services.store_discount_cents(order, store_id) * gross, store_gross) if store_gross else 0
    net = max(gross - discount, 0)
    bps = max((policy.line_terms(i).restocking_fee_bps for i in items), default=0) if reason.is_change_of_mind or reason.kind == ReturnReason.Kind.OTHER else 0
    restocking = _round_div(net * bps, 10000)
    delivery = 0
    if reason.seller_fault:
        taken = {i.pk for i in items} | set(
            ReturnItem.objects.filter(request__order=order, request__store_id=store_id).exclude(request__status__in=(S.CANCELLED, S.REJECTED)).values_list("order_item_id", flat=True)
        )
        live = [i for i in store_lines if i.status != "cancelled"]
        everything_back = all(i.pk in taken or i.status == "returned" for i in live)
        earlier = ReturnRequest.objects.filter(order=order, store_id=store_id, delivery_refund_cents__gt=0).exclude(status=S.CANCELLED).exists()
        if everything_back and not earlier:
            delivery = OrderDelivery.objects.filter(order=order, store_id=store_id).values_list("price_cents", flat=True).first() or 0
    return {"items_cents": gross, "discount_cents": discount, "restocking_fee_cents": restocking, "delivery_refund_cents": delivery,
            "refund_cents": net - restocking + delivery}


@transaction.atomic
def request_return(user, order: Order, *, item_ids, reason_code: str, note: str = "", files=None) -> ReturnRequest:
    order = Order.objects.select_for_update().get(pk=order.pk)        # serialises requests on one order (and its numbering)
    order = order_services.order_qs().get(pk=order.pk)
    country = order.country
    reason = reasons_for(country).filter(code=reason_code).first()
    if reason is None:
        raise HttpError(422, "unknown_reason")
    ids = list(dict.fromkeys(item_ids))
    if not ids:
        raise HttpError(422, "Choose at least one item")
    lines = {i.pk: i for i in order.items.all()}
    if any(pk not in lines for pk in ids):
        raise HttpError(404, "Item not found in this order")
    chosen = [lines[pk] for pk in ids]
    stores = {i.store_id for i in chosen}
    if len(stores) != 1:
        raise HttpError(422, "one_store_per_request")
    store_id = stores.pop()

    checks = {c.item.pk: c for c in policy.eligibility(order, reason.kind, store_id=store_id)}
    for item in chosen:
        if not checks[item.pk].eligible:
            raise HttpError(409, checks[item.pk].why_not)
    files = _check_photos(files, reason, country)

    now = timezone.now()
    amounts = _refund_amounts(order, store_id, chosen, reason)
    due_days = config.get("returns.seller_response_days", country=country)
    seq = ReturnRequest.objects.filter(order=order).count() + 1
    snapshot = {
        "reason_kind": reason.kind,
        "deadlines": {str(pk): (checks[pk].deadline.isoformat() if checks[pk].deadline else None) for pk in ids},
        "restocking_fee_bps": max((policy.line_terms(i).restocking_fee_bps for i in chosen), default=0),
    }
    req = ReturnRequest.objects.create(
        reference=f"RET-{order.order_number.split('-', 1)[-1]}-{seq}", order=order, store_id=store_id, user=user, country=country,
        reason=reason, reason_kind=reason.kind, note=note.strip(), terms=snapshot, currency=order.currency,
        response_due_at=now + timedelta(days=due_days), **amounts,
    )
    ReturnItem.objects.bulk_create([ReturnItem(request=req, order_item=i, qty=i.qty, line_total_cents=i.line_total_cents) for i in chosen])
    for f in files:
        ReturnPhoto.objects.create(request=req, image=f)
    _event(req, Actor.BUYER, note=note, user=user)

    from notifications import services as notifications

    notifications.return_requested(req)
    _mail(req, "received", f"Hi {_name(req)}, we have your return request {req.reference}. The seller will respond within {due_days} days.")
    if config.get("returns.auto_approve", country=country):
        approve(req, actor=Actor.SYSTEM, note="Approved automatically")
    return request_qs().get(pk=req.pk)


# --------------------------------------------------------------------------------------
# Moving a request along
# --------------------------------------------------------------------------------------


def _need(request, *allowed, message="This return can no longer be changed that way"):
    if request.status not in allowed:
        raise HttpError(409, message)


def _store_address(store) -> dict:
    app = store.application
    text = app.business_address if app.returns_same_as_business else app.returns_address
    return {"name": store.display_name, "address": text or app.business_address, "city": app.business_city, "postal_code": app.business_postal_code,
            "phone": app.business_phone, "country": store.country_id}


def _book(request) -> ReturnShipment:
    """Ask the market's courier to collect the parcel. A courier failing must never block an approval: the shipment
    is created as a manual one and staff can see the error in `raw`."""
    courier = couriers.for_country(request.country)
    pickup, dropoff = dict(request.order.shipping_address), _store_address(request.store)
    shipment = ReturnShipment(request=request, courier=courier.slug, pickup_address=pickup, dropoff_address=dropoff)
    try:
        booking = courier.book_return(request, pickup, dropoff)
    except Exception as exc:                                                       # noqa: BLE001 - any courier fault is survivable
        log.exception("courier %s could not book return %s", courier.slug, request.reference)
        shipment.courier, shipment.raw = "manual", {"error": str(exc), "courier": courier.slug}
    else:
        shipment.tracking_number, shipment.tracking_url = booking.tracking_number, booking.tracking_url
        shipment.label_url, shipment.external_id, shipment.raw = booking.label_url, booking.external_id, booking.raw
        shipment.status = booking.status or ReturnShipment.Status.PENDING
        if shipment.status == ReturnShipment.Status.BOOKED:
            shipment.booked_at = timezone.now()
    shipment.save()
    return shipment


@transaction.atomic
def approve(request: ReturnRequest, *, actor: str, user=None, note: str = "") -> ReturnRequest:
    _need(request, S.REQUESTED, S.ESCALATED)
    request.status, request.approved_at = S.APPROVED, timezone.now()
    if actor == Actor.STAFF:
        request.decided_by, request.staff_note = user, note[:500]
    request.save()
    _book(request)
    _event(request, actor, note=note or "Approved", user=user)
    _mail(request, "approved", f"Hi {_name(request)}, your return {request.reference} was approved. Please send the items back"
                               f"{' using the tracking details in your account' if request.shipments.exists() else ''}.")
    return request


@transaction.atomic
def reject(request: ReturnRequest, *, actor: str, user=None, reason: str) -> ReturnRequest:
    _need(request, S.REQUESTED, S.ESCALATED, S.RECEIVED)
    if not reason.strip():
        raise HttpError(422, "Say why: the buyer sees the reason")
    by_staff = actor == Actor.STAFF
    request.status = S.REJECTED
    request.is_final = by_staff or not config.get("returns.allow_escalation", country=request.country)
    if by_staff:
        request.decided_by, request.staff_note = user, reason[:500]
    else:
        request.seller_note = reason[:500]
    request.save()
    _cancel_shipments(request)
    _event(request, actor, note=reason, user=user)
    _mail(request, "rejected", f"Hi {_name(request)}, your return {request.reference} was not approved: {reason}")
    return request


@transaction.atomic
def cancel(request: ReturnRequest, *, user) -> ReturnRequest:
    _need(request, S.REQUESTED, S.APPROVED, message="This return can no longer be withdrawn")
    if request.shipments.filter(status__in=(ReturnShipment.Status.IN_TRANSIT, ReturnShipment.Status.DELIVERED)).exists():
        raise HttpError(409, "Already sent: wait for the seller to receive it")
    request.status, request.cancelled_at = S.CANCELLED, timezone.now()
    request.save()
    _cancel_shipments(request)
    _event(request, Actor.BUYER, note="Withdrawn by the buyer", user=user)
    from notifications import services as notifications

    notifications.return_cancelled(request)
    return request


@transaction.atomic
def escalate(request: ReturnRequest, *, user, note: str = "") -> ReturnRequest:
    _need(request, S.REJECTED, message="Only a rejected return can be disputed")
    if request.is_final or not config.get("returns.allow_escalation", country=request.country):
        raise HttpError(409, "not_escalatable")
    request.status, request.escalated_at = S.ESCALATED, timezone.now()
    request.save()
    _event(request, Actor.BUYER, note=note or "Disputed the rejection", user=user)
    from notifications import services as notifications

    notifications.return_escalated(request)
    return request


@transaction.atomic
def add_tracking(request: ReturnRequest, *, user, tracking_number: str, courier: str = "") -> ReturnShipment:
    """The buyer says how they sent it (the manual courier; a real courier fills this itself)."""
    _need(request, S.APPROVED, message="Tracking can only be added to an approved return")
    shipment = request.shipments.order_by("-created_at").first() or _book(request)
    shipment.tracking_number = tracking_number.strip()
    shipment.status = ReturnShipment.Status.IN_TRANSIT
    if courier:
        shipment.raw = {**shipment.raw, "carrier_name": courier[:60]}
    shipment.save()
    _event(request, Actor.BUYER, note=f"Sent back, tracking {shipment.tracking_number}", user=user)
    return shipment


def _cancel_shipments(request) -> None:
    for shipment in request.shipments.exclude(status__in=(ReturnShipment.Status.DELIVERED, ReturnShipment.Status.CANCELLED)):
        courier = couriers.get(shipment.courier)
        try:
            if courier is not None:
                courier.cancel(shipment)
        except Exception:                                                          # noqa: BLE001
            log.exception("courier %s could not cancel shipment %s", shipment.courier, shipment.pk)
        shipment.status = ReturnShipment.Status.CANCELLED
        shipment.save(update_fields=["status", "updated_at"])


@transaction.atomic
def mark_received(request: ReturnRequest, *, user, condition: str, restock: bool = True) -> ReturnRequest:
    """The seller has the parcel. In good condition the refund is paid at once; damaged goes to staff."""
    _need(request, S.APPROVED, message="Only an approved return can be received")
    request.received_at, request.received_condition = timezone.now(), condition
    _event(request, Actor.SELLER, status=S.RECEIVED, note=f"Received, condition: {condition}", user=user)
    shipment = request.shipments.order_by("-created_at").first()
    if shipment and shipment.status != ReturnShipment.Status.DELIVERED:
        shipment.status, shipment.delivered_at = ReturnShipment.Status.DELIVERED, timezone.now()
        shipment.save(update_fields=["status", "delivered_at", "updated_at"])
    if condition == "good":
        return _complete(request, actor=Actor.SELLER, user=user, restock=restock)
    request.status = S.RECEIVED
    request.save()
    return request


@transaction.atomic
def decide(request: ReturnRequest, *, user, outcome: str, note: str = "") -> ReturnRequest:
    """Staff settle an escalated rejection or a parcel that arrived damaged. Final."""
    from notifications import services as notifications

    if request.status == S.ESCALATED:
        if outcome == "approve":
            approve(request, actor=Actor.STAFF, user=user, note=note or "Approved by asknget")
        else:
            reject(request, actor=Actor.STAFF, user=user, reason=note)
    elif request.status == S.RECEIVED:
        if outcome == "approve":
            _complete(request, actor=Actor.STAFF, user=user, restock=False, note=note)
        else:
            reject(request, actor=Actor.STAFF, user=user, reason=note)
    else:
        raise HttpError(409, "Only an escalated or damaged return needs a decision")
    notifications.return_decided(request, "approved" if outcome == "approve" else "rejected")
    return request


@transaction.atomic
def _complete(request: ReturnRequest, *, actor: str, user=None, restock: bool = True, note: str = "") -> ReturnRequest:
    """Pay the return: the lines become returned, stock comes back if wanted, the ledger gives the money back."""
    from ledger import services as ledger

    order, store = request.order, request.store
    items = list(OrderItem.objects.filter(pk__in=request.items.values_list("order_item_id", flat=True)).select_related("variant"))
    if restock:
        live = [i for i in items if i.variant_id]
        locked = {v.id: v for v in lock_variants(i.variant_id for i in live)}
        for item in live:
            adjust_stock(locked[item.variant_id], delta=item.qty, reason=StockMovement.Reason.RETURN, order=order, actor_user=user, locked=True,
                         note=f"Return {request.reference}")
        request.restocked = True
    OrderItem.objects.filter(pk__in=[i.pk for i in items]).update(status=OrderItem.FulfilmentStatus.RETURNED)
    OrderStatusEvent.objects.create(order=order, store=store, status=Order.OrderStatus.RETURNED, note=f"Return {request.reference}",
                                    actor=OrderStatusEvent.OrderEventActor.SELLER if actor == Actor.SELLER else OrderStatusEvent.OrderEventActor.ADMIN, actor_user=user)
    status = order_services.derive_order_status(order)
    order.payment_status = Order.OrderPaymentStatus.REFUNDED if status == Order.OrderStatus.RETURNED else Order.OrderPaymentStatus.PARTIALLY_REFUNDED
    order.save(update_fields=["payment_status", "updated_at"])

    request.status, request.refunded_at = S.REFUNDED, timezone.now()
    request.refund_status = ReturnRequest.RefundStatus.PENDING if request.refund_cents else ReturnRequest.RefundStatus.NONE
    if actor == Actor.STAFF:
        request.decided_by, request.staff_note = user, note[:500]
    request.save()
    ledger.refund_items(request)
    _event(request, actor, status=S.REFUNDED, note=note or "Refund issued", user=user)
    _mail(request, "refund issued", f"Hi {_name(request)}, your return {request.reference} is complete. A refund of {request.refund_cents / 100:,.2f} {request.currency} "
                                    "is on its way to your original payment method.")
    return request


@transaction.atomic
def mark_refund_paid(request: ReturnRequest, *, user, reference: str = "") -> ReturnRequest:
    """Staff paid the buyer (until a payment provider does it)."""
    if request.refund_status != ReturnRequest.RefundStatus.PENDING:
        raise HttpError(409, "Nothing is owed on this return")
    request.refund_status, request.refund_note = ReturnRequest.RefundStatus.COMPLETED, reference[:255]
    request.save(update_fields=["refund_status", "refund_note", "updated_at"])
    _event(request, Actor.STAFF, note=f"Refund paid {reference}".strip(), user=user)
    return request


# --------------------------------------------------------------------------------------
# Courier callbacks
# --------------------------------------------------------------------------------------


@transaction.atomic
def apply_update(shipment: ReturnShipment, update: couriers.TrackingUpdate) -> ReturnShipment:
    """A courier reported progress (polled or pushed). Moves the shipment and writes the timeline."""
    valid = {s.value for s in ReturnShipment.Status}
    if update.status not in valid or shipment.status in (ReturnShipment.Status.DELIVERED, ReturnShipment.Status.CANCELLED):
        return shipment
    shipment.status = update.status
    shipment.tracking_number = shipment.tracking_number or update.tracking_number
    shipment.external_id = shipment.external_id or update.external_id
    shipment.raw = update.raw or shipment.raw
    if update.status == ReturnShipment.Status.DELIVERED:
        shipment.delivered_at = update.at or timezone.now()
    shipment.save()
    _event(shipment.request, Actor.SYSTEM, note=update.note or f"Courier: {shipment.get_status_display()}")
    return shipment


def handle_webhook(courier_slug: str, body: bytes, headers) -> int:
    courier = couriers.get(courier_slug)
    if courier is None:
        raise HttpError(404, "Unknown courier")
    try:
        updates = courier.parse_webhook(body, headers)
    except NotImplementedError:
        raise HttpError(404, "This courier has no webhook")
    applied = 0
    for update in updates:
        shipments = ReturnShipment.objects.filter(courier=courier_slug)
        shipment = (shipments.filter(external_id=update.external_id) if update.external_id else shipments.filter(tracking_number=update.tracking_number)).first()
        if shipment is not None:
            apply_update(shipment, update)
            applied += 1
    return applied


def sync_tracking() -> int:
    """Poll couriers that support it for every parcel still on its way. Run from a scheduler (`manage.py sync_return_tracking`)."""
    count = 0
    for shipment in ReturnShipment.objects.filter(status__in=(ReturnShipment.Status.BOOKED, ReturnShipment.Status.IN_TRANSIT)).select_related("request"):
        courier = couriers.get(shipment.courier)
        update = courier.track(shipment) if courier else None
        if update is not None:
            apply_update(shipment, update)
            count += 1
    return count
