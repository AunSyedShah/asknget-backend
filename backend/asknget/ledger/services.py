"""Posting money, and reading balances.

Rules, in one place:

* **Payment confirmed** -> for each store in the order: a `sale` (its items - its share of
  the voucher + its own delivery charge) and a `fee` (the commission, worked out per line).
* **Delivered** -> the sale starts its hold; it becomes available per the store's resolved
  `payouts.hold_mode` (an admin setting). Until then it is *held*, not withdrawable.
* **Cancelled / returned / refunded** -> a `refund` of the sale and a `fee_refund` of the
  commission, once per store per order (whatever has not already gone back).
* **A customer return** -> `refund_items`: a `refund` of the returned value and the matching share of the commission,
  once per return request.
  A refund of money that was never yet available cancels it out of the available balance rather than driving it negative.
* **Staff** add signed `adjustment`s and create / complete `payout`s.

Every posting is idempotent (the table's unique constraint backs it up), so a retried webhook
or a double click cannot pay anyone twice. Balances are computed from the entries, never stored.
"""

from collections import defaultdict
from datetime import datetime, timedelta

from django.db import IntegrityError, transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone
from ninja.errors import HttpError
from zeal import zeal_ignore

from config import services as config
from orders.models import Order, OrderDelivery, OrderItem

from .models import LedgerCounter, LedgerEntry, Payout

T = LedgerEntry.Type
HOLD_KEYS = ["payouts.hold_mode", "payouts.hold_days"]
FEE_TYPES = (T.FEE, T.FEE_REFUND)
STOCK_OF_SALE = (T.SALE, T.FEE, T.FEE_REFUND)       # the entries that are subject to the hold


# --------------------------------------------------------------------------------------
# References
# --------------------------------------------------------------------------------------


def _next_number(name: str) -> int:
    # The N+1 detector sees "the same query again and again" when one operation posts several
    # entries. It is a counter being incremented, not a missed prefetch, and reserving numbers
    # in blocks would leave gaps in the sequence, which a statement must not have.
    with zeal_ignore():
        LedgerCounter.objects.get_or_create(name=name)
        counter = LedgerCounter.objects.select_for_update().get(name=name)
        counter.value += 1
        counter.save(update_fields=["value"])
        return counter.value


def new_reference(prefix: str = "TXN") -> str:
    """TXN-20261012-10482: the date sorts and reads on a statement; the number is unique platform-wide.
    Call inside a transaction (the counter row is locked)."""
    return f"{prefix}-{timezone.localdate():%Y%m%d}-{_next_number(prefix.lower())}"


def _round_div(numerator: int, denominator: int) -> int:
    """Integer division rounding half up, so money never passes through a float."""
    return (2 * numerator + denominator) // (2 * denominator)


def _entry(**fields) -> LedgerEntry:
    return LedgerEntry.objects.create(reference=new_reference(), **fields)


# --------------------------------------------------------------------------------------
# Posting
# --------------------------------------------------------------------------------------


def _hold_available_at(store, now):
    """None when the money must wait for delivery; `now` when the hold is switched off."""
    mode = config.get("payouts.hold_mode", store=store)
    return now if mode == "none" else None


@transaction.atomic
def post_sales(order: Order, *, include_refunded: bool = False) -> list[LedgerEntry]:
    """Called once an order's payment is confirmed. Safe to call again.

    `include_refunded` is for history only (`backfill_ledger`): lines already cancelled or returned are
    posted too, so the caller can post their refund and the books show both halves. A live confirmation
    never has such lines."""
    lines = OrderItem.objects.filter(order=order).select_related("store__country", "product")
    items = list(lines if include_refunded else lines.exclude(status__in=["cancelled", "returned"]))
    by_store = defaultdict(list)
    for item in items:
        by_store[item.store_id].append(item)
    delivery = dict(OrderDelivery.objects.filter(order=order).values_list("store_id", "price_cents"))
    now = timezone.now()
    from orders.services import store_discount_cents

    created = []
    for store_id, lines in by_store.items():
        store = lines[0].store
        if LedgerEntry.objects.filter(order=order, store=store, type=T.SALE).exists():
            continue
        gross = sum(line.line_total_cents for line in lines)
        discount = store_discount_cents(order, store_id)
        net = max(gross - discount, 0)
        shipping = delivery.get(store_id, 0)
        sale_cents = net + shipping
        if sale_cents <= 0:
            continue

        rates = config.commission_rates(store, {line.product.category_id if line.product_id else None for line in lines})
        weighted = sum(rates[line.product.category_id if line.product_id else None].bps * line.line_total_cents for line in lines)
        fee_cents = _round_div(weighted * net, 10000 * gross) if gross else 0     # voucher reduces the commission proportionally

        per_category = defaultdict(int)
        for line in lines:
            per_category[line.product.category_id if line.product_id else None] += line.line_total_cents
        available_at = _hold_available_at(store, now)
        common = dict(store=store, order=order, currency=order.currency, available_at=available_at)
        try:
            with transaction.atomic():
                sale = _entry(
                    type=T.SALE, amount_cents=sale_cents, description=f"Order {order.order_number}",
                    meta={"lines_cents": gross, "discount_cents": discount, "delivery_cents": shipping}, **common,
                )
                created.append(sale)
                if fee_cents:
                    created.append(_entry(
                        type=T.FEE, amount_cents=-fee_cents, description=f"Commission on order {order.order_number}",
                        meta={
                            "effective_bps": _round_div(fee_cents * 10000, net) if net else 0,
                            "lines": [
                                {"category_id": str(cid) if cid else None, "line_cents": cents, "bps": rates[cid].bps,
                                 "rule_id": str(rates[cid].rule_id) if rates[cid].rule_id else None}
                                for cid, cents in per_category.items()
                            ],
                        },
                        **common,
                    ))
        except IntegrityError:                    # a concurrent confirmation posted first: that one stands
            continue
    return created


def _given_back(order: Order) -> dict:
    """{store_id: (sale refunded so far, commission refunded so far)}, all positive, across every kind of refund."""
    totals = defaultdict(lambda: [0, 0])
    for store_id, kind, amount in LedgerEntry.objects.filter(order=order, type__in=(T.REFUND, T.FEE_REFUND)).values_list("store_id", "type", "amount_cents"):
        totals[store_id][0 if kind == T.REFUND else 1] += abs(amount)
    return totals


def _fully_refunded_stores(order: Order) -> set:
    given = _given_back(order)
    sales = dict(LedgerEntry.objects.filter(order=order, type=T.SALE).values_list("store_id", "amount_cents"))
    return {store_id for store_id, amount in sales.items() if given.get(store_id, (0, 0))[0] >= amount}


@transaction.atomic
def release(order: Order, store_id=None) -> int:
    """The store's goods were delivered: start the hold on what they earned. Returns entries released."""
    now = timezone.now()
    held = LedgerEntry.objects.filter(order=order, type__in=STOCK_OF_SALE, available_at__isnull=True)
    if store_id:
        held = held.filter(store_id=store_id)
    refunded = _fully_refunded_stores(order)
    count = 0
    for entry in held.select_related("store"):
        if entry.store_id in refunded:
            continue
        policy = config.get_many(HOLD_KEYS, store=entry.store)
        mode, days = policy["payouts.hold_mode"], policy["payouts.hold_days"]
        if mode == "return_window":
            at, days = now + timedelta(days=order.return_window_days), order.return_window_days
        elif mode == "fixed_days":
            at = now + timedelta(days=days)
        else:
            at, days = now, 0
        entry.available_at = at
        entry.meta = {**entry.meta, "hold": {"mode": mode, "days": days, "started": now.isoformat()}}
        entry.save(update_fields=["available_at", "meta", "updated_at"])
        count += 1
    return count


@transaction.atomic
def refund(order: Order, store_id=None) -> list[LedgerEntry]:
    """Give back to the buyer whatever of a store's sale has not already gone back, and the same share of the
    commission to the store. Idempotent: once a store is fully refunded, a second call posts nothing. Earlier partial
    refunds (customer returns) are respected, so a later whole-order refund cannot pay the same item twice."""
    now = timezone.now()
    sales = LedgerEntry.objects.filter(order=order, type=T.SALE).select_related("store")
    if store_id:
        sales = sales.filter(store_id=store_id)
    given = _given_back(order)
    created = []
    for sale in sales:
        sale_back, fee_back = given.get(sale.store_id, (0, 0))
        remaining = sale.amount_cents - sale_back
        if remaining <= 0:
            continue
        # Money that is not yet available cancels out of the held amount; money that is comes off the balance now.
        when = sale.available_at if (sale.available_at is None or sale.available_at > now) else now
        common = dict(store=sale.store, order=order, currency=sale.currency, available_at=when, status=LedgerEntry.Status.COMPLETED)
        created.append(_entry(type=T.REFUND, amount_cents=-remaining, description=f"Refund for order {order.order_number}",
                              meta={"sale": sale.reference}, **common))
        fee = LedgerEntry.objects.filter(order=order, store=sale.store, type=T.FEE).first()
        fee_remaining = abs(fee.amount_cents) - fee_back if fee else 0
        if fee_remaining > 0:
            created.append(_entry(type=T.FEE_REFUND, amount_cents=fee_remaining, description=f"Commission returned for order {order.order_number}",
                                  meta={"fee": fee.reference}, **common))
    return created


def _commission_on(request, sale: LedgerEntry, fee: LedgerEntry) -> int:
    """The commission that was charged on the returned lines: each line at its own category's rate (recorded on the fee
    entry), reduced by the voucher exactly as `post_sales` reduced it. Fees posted without that breakdown fall back to
    a share of the whole commission by value."""
    gross = sale.meta.get("lines_cents", 0)
    net = gross - sale.meta.get("discount_cents", 0)
    if gross <= 0 or net <= 0:
        return 0
    rates = {line.get("category_id"): line.get("bps", 0) for line in fee.meta.get("lines", [])}
    if not rates:
        return _round_div(abs(fee.amount_cents) * (request.items_cents - request.discount_cents), net)
    fallback = fee.meta.get("effective_bps", 0)
    weighted = 0
    for cents, category_id in request.items.values_list("order_item__line_total_cents", "order_item__product__category_id"):
        key = str(category_id) if category_id else None
        weighted += rates.get(key, fallback) * cents
    return _round_div(weighted * net, 10000 * gross)


def has_sale(order: Order, store) -> bool:
    return LedgerEntry.objects.filter(order=order, store=store, type=T.SALE).exists()


@transaction.atomic
def refund_items(request) -> list[LedgerEntry]:
    """A customer return was accepted: give back `request.refund_cents` of the store's sale, and the matching
    share of the commission (pro-rata to the returned value; capped at what is left). Once per request."""
    order, store = request.order, request.store
    if LedgerEntry.objects.filter(return_request=request).exists():
        return []
    sale = LedgerEntry.objects.filter(order=order, store=store, type=T.SALE).first()
    if sale is None or request.refund_cents <= 0:
        return []
    sale_back, fee_back = _given_back(order).get(store.pk, (0, 0))
    amount = min(request.refund_cents, sale.amount_cents - sale_back)
    if amount <= 0:
        return []
    now = timezone.now()
    when = sale.available_at if (sale.available_at is None or sale.available_at > now) else now
    common = dict(store=store, order=order, currency=sale.currency, available_at=when, status=LedgerEntry.Status.COMPLETED, return_request=request)
    created = [_entry(type=T.REFUND, amount_cents=-amount, description=f"Return {request.reference} (order {order.order_number})",
                      meta={"sale": sale.reference, "return": request.reference, "items_cents": request.items_cents,
                            "restocking_fee_cents": request.restocking_fee_cents, "delivery_refund_cents": request.delivery_refund_cents}, **common)]
    fee = LedgerEntry.objects.filter(order=order, store=store, type=T.FEE).first()
    if fee:
        share = min(_commission_on(request, sale, fee), abs(fee.amount_cents) - fee_back)
        if share > 0:
            created.append(_entry(type=T.FEE_REFUND, amount_cents=share, description=f"Commission returned for {request.reference}",
                                  meta={"fee": fee.reference, "return": request.reference}, **common))
    return created


@transaction.atomic
def adjust(store, *, amount_cents: int, note: str, by) -> LedgerEntry:
    if not amount_cents:
        raise HttpError(422, "An adjustment cannot be zero")
    if not note.strip():
        raise HttpError(422, "Say why: every adjustment needs a note")
    return _entry(store=store, type=T.ADJUSTMENT, amount_cents=amount_cents, currency=store.country.currency, description=note.strip()[:255],
                  available_at=timezone.now(), status=LedgerEntry.Status.COMPLETED, created_by=by)


# --------------------------------------------------------------------------------------
# Balances
# --------------------------------------------------------------------------------------


def _live(store):
    return LedgerEntry.objects.filter(store=store).exclude(status=LedgerEntry.Status.FAILED)


def balances(store, now: datetime | None = None) -> dict:
    now = now or timezone.now()
    live = _live(store)
    total = lambda qs: qs.aggregate(s=Sum("amount_cents"))["s"] or 0
    return {
        "available_cents": total(live.filter(available_at__lte=now)),
        # earned, but waiting for delivery or the return window; refunds of held money net it out
        "held_cents": total(live.filter(Q(available_at__isnull=True) | Q(available_at__gt=now))),
        "pending_payout_cents": -total(live.filter(type=T.PAYOUT, status=LedgerEntry.Status.PROCESSING)),
    }


def period_totals(store, start: datetime, end: datetime) -> dict:
    rows = _live(store).filter(created_at__gte=start, created_at__lt=end).values_list("type").annotate(s=Sum("amount_cents")).values_list("type", "s")
    by = dict(rows)
    sales = by.get(T.SALE, 0)
    fees = by.get(T.FEE, 0) + by.get(T.FEE_REFUND, 0)
    refunds = by.get(T.REFUND, 0)
    adjustments = by.get(T.ADJUSTMENT, 0)
    return {"sales_cents": sales, "fees_cents": fees, "refunds_cents": refunds, "adjustments_cents": adjustments,
            "net_cents": sales + fees + refunds + adjustments, "payouts_cents": by.get(T.PAYOUT, 0)}


# --------------------------------------------------------------------------------------
# Reading entries
# --------------------------------------------------------------------------------------

TABS = {"sale": (T.SALE,), "payout": (T.PAYOUT,), "fee": (T.FEE, T.FEE_REFUND), "refund": (T.REFUND,), "adjustment": (T.ADJUSTMENT,)}


def display_type(entry: LedgerEntry) -> str:
    return "fee" if entry.type == T.FEE_REFUND else entry.type


def display_status(entry: LedgerEntry, now: datetime | None = None) -> str:
    """The word the design shows: completed / processing / refunded, plus pending and failed."""
    now = now or timezone.now()
    if entry.status == LedgerEntry.Status.FAILED:
        return "failed"
    if entry.type == T.REFUND:
        return "refunded"
    if entry.type == T.PAYOUT:
        return entry.status
    if entry.type == T.ADJUSTMENT:
        return "completed"
    return "completed" if entry.available_at and entry.available_at <= now else "pending"


def _status_q(status: str, now: datetime) -> Q:
    held_types = Q(type__in=STOCK_OF_SALE)
    return {
        "completed": (held_types & Q(available_at__lte=now)) | Q(type=T.ADJUSTMENT) | Q(type=T.PAYOUT, status="completed"),
        "pending": held_types & (Q(available_at__isnull=True) | Q(available_at__gt=now)),
        "processing": Q(type=T.PAYOUT, status="processing"),
        "refunded": Q(type=T.REFUND),
        "failed": Q(status="failed"),
    }.get(status, Q(pk__in=[]))


def entries(store, *, tab: str | None = None, q: str | None = None, status: str | None = None, date_from=None, date_to=None,
            amount_min: int | None = None, amount_max: int | None = None):
    now = timezone.now()
    qs = LedgerEntry.objects.filter(store=store).select_related("order")
    if tab and tab != "all":
        qs = qs.filter(type__in=TABS.get(tab, ()))
    if q:
        needle = q.strip()
        qs = qs.filter(
            Q(reference__icontains=needle) | Q(order__order_number__icontains=needle)
            | Q(order__contact_first_name__icontains=needle) | Q(order__contact_last_name__icontains=needle)
        )
    if status:
        qs = qs.filter(_status_q(status, now))
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)
    if amount_min is not None:
        qs = qs.filter(Q(amount_cents__gte=amount_min) | Q(amount_cents__lte=-amount_min))      # by size, whichever way the money moved
    if amount_max is not None:
        qs = qs.filter(amount_cents__lte=amount_max, amount_cents__gte=-amount_max)
    return qs


def tab_counts(store) -> dict:
    by_type = dict(LedgerEntry.objects.filter(store=store).values_list("type").annotate(n=Count("id")).values_list("type", "n"))
    counts = {name: sum(by_type.get(t, 0) for t in types) for name, types in TABS.items()}
    counts["all"] = sum(by_type.values())
    return counts


# --------------------------------------------------------------------------------------
# Payouts (staff)
# --------------------------------------------------------------------------------------


def validate_payout(store, amount_cents: int) -> None:
    """Raises HttpError if a payout of this size is not allowed. Shared by the API and the admin form."""
    if amount_cents <= 0:
        raise HttpError(422, "A payout must be more than zero")
    minimum = config.get("payouts.minimum_cents", store=store)
    if amount_cents < minimum:
        raise HttpError(422, f"The smallest payout is {minimum} (a platform setting)")
    available = balances(store)["available_cents"]
    if amount_cents > available:
        raise HttpError(409, f"Only {available} is available to pay out")


@transaction.atomic
def create_payout(store, *, amount_cents: int, note: str, by) -> Payout:
    # Lock the store's row first: two payouts at the same moment would otherwise both see the same available balance.
    list(type(store).objects.select_for_update().filter(pk=store.pk).values_list("pk", flat=True))
    validate_payout(store, amount_cents)
    payout = Payout.objects.create(
        store=store, amount_cents=amount_cents, currency=store.country.currency, reference=new_reference("PAY"), note=note[:255], created_by=by,
    )
    _entry(store=store, type=T.PAYOUT, amount_cents=-amount_cents, currency=payout.currency, payout=payout, description=note[:255] or "Payout",
           available_at=timezone.now(), status=LedgerEntry.Status.PROCESSING, created_by=by)
    return payout


@transaction.atomic
def set_payout_status(payout: Payout, status: str, by) -> Payout:
    payout = Payout.objects.select_for_update().get(pk=payout.pk)
    if payout.status != Payout.Status.PROCESSING:
        raise HttpError(409, f"This payout is already {payout.status}")
    if status not in (Payout.Status.COMPLETED, Payout.Status.FAILED):
        raise HttpError(422, "A payout can be completed or failed")
    payout.status = status
    payout.completed_at = timezone.now() if status == Payout.Status.COMPLETED else None
    payout.save(update_fields=["status", "completed_at", "updated_at"])
    # A failed payout drops out of every balance (its entry is excluded), so the money is available again.
    payout.entries.update(status=status)
    if status == Payout.Status.COMPLETED:
        from notifications import services as notifications

        notifications.payout_processed(payout)
    return payout
