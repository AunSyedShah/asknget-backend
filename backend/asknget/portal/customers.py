"""Seller Customers — derived from the store's paid orders (docs/sprint-3-plan.md §5).

Customer key = buyer user id, else lower-cased contact email (guest checkout).
Definitions (all per store):
  orders_count / total_spent  non-cancelled, non-returned lines of paid orders
  type   returning = ≥ 2 orders · new = 1 order placed in the last NEW_DAYS · one_time = 1 older order
  status active = ordered in the last ACTIVE_DAYS

Aggregation is done in Python over the store's orders and cached briefly; a
store with tens of thousands of orders should move this to a nightly rollup.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from django.core.cache import cache
from django.db.models import Count, Max
from django.utils import timezone
from ninja.errors import HttpError

from orders.models import Order, OrderItem

from .inventory import csv_response
from .models import CustomerNote
from .orders import customer_name, row as order_row, store_orders
from .periods import change_pct, month_buckets, period_range

Fulfilment = OrderItem.FulfilmentStatus
NEW_DAYS = 30
ACTIVE_DAYS = 90
CACHE_SECONDS = 60


@dataclass
class Customer:
    key: str
    user_id: str | None
    name: str
    email: str
    phone: str
    orders_count: int = 0
    total_spent_cents: int = 0
    first_order_at: datetime | None = None
    last_order_at: datetime | None = None
    last_order_number: str = ""
    last_order_id: str = ""
    currency: str = "USD"
    order_months: set = field(default_factory=set)

    def type(self, now) -> str:
        if self.orders_count >= 2:
            return "returning"
        return "new" if self.first_order_at and self.first_order_at >= now - timedelta(days=NEW_DAYS) else "one_time"

    def status(self, now) -> str:
        return "active" if self.last_order_at and self.last_order_at >= now - timedelta(days=ACTIVE_DAYS) else "inactive"


def key_for(order: Order) -> str:
    return str(order.user_id) if order.user_id else order.contact_email.lower()


def _build(store) -> dict[str, Customer]:
    customers: dict[str, Customer] = {}
    qs = store_orders(store).exclude(store_status__in=(Fulfilment.CANCELLED, Fulfilment.RETURNED)).order_by("placed_at")
    for o in qs:
        k = key_for(o)
        c = customers.get(k)
        if c is None:
            c = customers[k] = Customer(key=k, user_id=str(o.user_id) if o.user_id else None, name=customer_name(o), email=o.contact_email, phone=o.contact_phone, currency=o.currency)
        c.orders_count += 1
        c.total_spent_cents += o.store_total_cents
        c.first_order_at = c.first_order_at or o.placed_at
        c.last_order_at, c.last_order_number, c.last_order_id = o.placed_at, o.order_number, str(o.id)
        c.name, c.email, c.phone = customer_name(o), o.contact_email, o.contact_phone  # latest contact details win
        c.order_months.add((o.placed_at.year, o.placed_at.month))
    return customers


def all_customers(store) -> dict[str, Customer]:
    """Cached per store; the key changes whenever any of the store's order lines change,
    so a status update or new order is reflected immediately."""
    sig = OrderItem.objects.filter(store=store).aggregate(n=Count("id"), m=Max("updated_at"))
    key = f"portal:customers:{store.pk}:{sig['n']}:{sig['m'].timestamp() if sig['m'] else 0}"
    data = cache.get(key)
    if data is None:
        data = _build(store)
        cache.set(key, data, CACHE_SECONDS)
    return data


def serialize(c: Customer, now) -> dict:
    return {
        "key": c.key, "user_id": c.user_id, "name": c.name, "email": c.email, "phone": c.phone,
        "orders_count": c.orders_count, "total_spent_cents": c.total_spent_cents, "currency": c.currency,
        "first_order_at": c.first_order_at, "last_order_at": c.last_order_at, "last_order_number": c.last_order_number,
        "last_order_id": c.last_order_id, "type": c.type(now), "status": c.status(now),
    }


SORTS = {
    "recent": lambda c: c.last_order_at, "spent": lambda c: c.total_spent_cents, "orders": lambda c: c.orders_count,
    "name": lambda c: c.name.lower(),
}


def list_customers(store, *, tab: str = "all", q: str | None = None, status: str | None = None, sort: str = "recent") -> list[dict]:
    now = timezone.now()
    rows = list(all_customers(store).values())
    if tab != "all":
        rows = [c for c in rows if c.type(now) == tab]
    if status:
        rows = [c for c in rows if c.status(now) == status]
    if q:
        ql = q.lower()
        rows = [c for c in rows if ql in c.name.lower() or ql in c.email.lower()]
    rows.sort(key=SORTS[sort], reverse=sort != "name")
    return [serialize(c, now) for c in rows]


def tabs(store) -> dict:
    now = timezone.now()
    counts = defaultdict(int)
    for c in all_customers(store).values():
        counts[c.type(now)] += 1
    return {"all": sum(counts.values()), "new": counts["new"], "returning": counts["returning"], "one_time": counts["one_time"]}


def _kpi(cur, prev):
    return {"value": cur, "previous": prev, "change_pct": change_pct(cur, prev)}


def overview(store, period_name: str) -> dict:
    now = timezone.now()
    p = period_range(period_name)
    customers = list(all_customers(store).values())

    def total_by(end):
        return sum(1 for c in customers if c.first_order_at and c.first_order_at < end)

    def new_in(a, b):
        return sum(1 for c in customers if c.first_order_at and a <= c.first_order_at < b)

    def returning_in(a, b):
        return sum(1 for c in customers if c.first_order_at and c.first_order_at < a and c.last_order_at and a <= c.last_order_at < b)

    def avg_value(end):
        pool = [c for c in customers if c.first_order_at and c.first_order_at < end]
        return round(sum(c.total_spent_cents for c in pool) / len(pool)) if pool else 0

    year = now.year
    months = {m: {"new": 0.0, "returning": 0.0, "one_time": 0.0} for m in range(1, 13)}
    for c in customers:
        for (y, m) in c.order_months:
            if y != year:
                continue
            if c.first_order_at.year == y and c.first_order_at.month == m:
                months[m]["one_time" if c.orders_count == 1 else "new"] += 1
            else:
                months[m]["returning"] += 1

    breakdown = tabs(store)
    total = breakdown["all"] or 1
    return {
        "period": period_name,
        "total_customers": _kpi(total_by(p.end), total_by(p.start)),
        "new_customers": _kpi(new_in(p.start, p.end), new_in(p.prev_start, p.prev_end)),
        "returning_customers": _kpi(returning_in(p.start, p.end), returning_in(p.prev_start, p.prev_end)),
        "avg_customer_value_cents": _kpi(avg_value(p.end), avg_value(p.start)),
        "monthly": [{"label": b.strftime("%b"), "values": months[b.month]} for b in month_buckets(year)],
        "breakdown": {k: {"count": breakdown[k], "pct": round(breakdown[k] / total * 100, 1)} for k in ("new", "returning", "one_time")},
        "recent": list_customers(store)[:5],
        "tabs": breakdown,
    }


def get_customer(store, key: str) -> Customer:
    c = all_customers(store).get(key)
    if c is None:
        raise HttpError(404, "Customer not found")
    return c


def detail(store, key: str) -> dict:
    now = timezone.now()
    c = get_customer(store, key)
    qs = store_orders(store)
    qs = qs.filter(user_id=c.user_id) if c.user_id else qs.filter(user__isnull=True, contact_email__iexact=c.email)
    return {
        **serialize(c, now),
        "orders": [order_row(o) for o in qs.order_by("-placed_at")],
        "notes": [note_out(n) for n in CustomerNote.objects.filter(store=store, customer_key=key).select_related("author").order_by("-created_at")],
    }


def add_note(store, key: str, *, body: str, author) -> CustomerNote:
    get_customer(store, key)
    return CustomerNote.objects.create(store=store, customer_key=key, author=author, body=body)


def delete_note(store, key: str, note_id) -> None:
    deleted, _ = CustomerNote.objects.filter(store=store, customer_key=key, pk=note_id).delete()
    if not deleted:
        raise HttpError(404, "Note not found")


def note_out(n: CustomerNote) -> dict:
    return {"id": n.id, "body": n.body, "author": (n.author.full_name or n.author.email) if n.author_id else None, "created_at": n.created_at}


def export(store):
    def rows():
        for c in list_customers(store):
            yield [c["name"], c["email"], c["phone"], c["orders_count"], c["total_spent_cents"] / 100, c["currency"],
                   c["first_order_at"].isoformat() if c["first_order_at"] else "", c["last_order_at"].isoformat() if c["last_order_at"] else "",
                   c["last_order_number"], c["type"], c["status"]]

    return csv_response("customers.csv", ["name", "email", "phone", "orders", "total_spent", "currency", "first_order", "last_order", "last_order_number", "type", "status"], rows())
