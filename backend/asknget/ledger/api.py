"""Transactions.

  /seller/transactions   section `transactions`: overview, table, one transaction, CSV
  /admin/ledger          staff: a store's balances, signed adjustments
  /admin/payouts         staff: create a payout, complete or fail it

A seller can read their own money and nothing else. Moving it is staff-only: a seller must
not be able to mark their own payout paid.
"""

import csv
from datetime import date
from uuid import UUID

from django.http import HttpResponse
from django.utils import timezone
from ninja import Router
from ninja.errors import HttpError
from ninja.pagination import paginate

from core.auth import StaffAuth
from staff.access import scope
from core.pagination import PagePagination
from core.schemas import ErrorOut
from portal.auth import StoreAuth
from portal.periods import change_pct, period_range
from portal.schemas import PeriodLit
from sellers.models import Store

from . import services
from .models import LedgerEntry, Payout
from .schemas import (
    AdjustmentIn,
    DetailOut,
    EntryOut,
    OverviewOut,
    PayoutIn,
    PayoutOut,
    PayoutStatusIn,
    StoreBalanceOut,
    TabsOut,
)

transactions_router = Router(tags=["seller: transactions"], auth=StoreAuth(section="transactions"))
admin_ledger_router = Router(tags=["admin: ledger"], auth=StaffAuth())
admin_payouts_router = Router(tags=["admin: ledger"], auth=StaffAuth())

T = LedgerEntry.Type


# --------------------------------------------------------------------------- #
# Serialisation
# --------------------------------------------------------------------------- #


def _customer(order) -> str | None:
    return f"{order.contact_first_name} {order.contact_last_name}".strip() if order else None


def _row(entry: LedgerEntry, now=None) -> dict:
    order = entry.order
    return {
        "reference": entry.reference, "type": services.display_type(entry), "amount_cents": entry.amount_cents, "currency": entry.currency,
        "status": services.display_status(entry, now), "created_at": entry.created_at, "available_at": entry.available_at,
        "order_id": order.pk if order else None, "order_number": order.order_number if order else None,
        "customer": _customer(order), "description": entry.description,
    }


def _payout_out(payout: Payout) -> dict:
    return {
        "id": payout.pk, "reference": payout.reference, "store_id": payout.store_id, "store_name": payout.store.display_name,
        "amount_cents": payout.amount_cents, "currency": payout.currency, "status": payout.status, "note": payout.note,
        "created_at": payout.created_at, "completed_at": payout.completed_at,
    }


def _filtered(request, tab, q, status, date_from, date_to, amount_min, amount_max):
    return services.entries(request.store, tab=tab, q=q, status=status, date_from=date_from, date_to=date_to, amount_min=amount_min, amount_max=amount_max)


# --------------------------------------------------------------------------- #
# Seller
# --------------------------------------------------------------------------- #


@transactions_router.get("/overview", response=OverviewOut, summary="Transactions Overview: cards, recent transactions, summary")
def overview(request, period: PeriodLit = "month"):
    store = request.store
    p = period_range(period)
    now = timezone.now()
    cur = services.period_totals(store, p.start, p.end)
    prev = services.period_totals(store, p.prev_start, p.prev_end)
    bal = services.balances(store, now)
    recent = services.entries(store).order_by("-created_at", "-reference")[:5]
    kpi = lambda c, v: {"value": c, "previous": v, "change_pct": change_pct(c, v)}
    return {
        "period": period, "currency": store.country.currency,
        "total_sales": kpi(cur["sales_cents"], prev["sales_cents"]),
        "total_fees": kpi(-cur["fees_cents"], -prev["fees_cents"]),
        "pending_payout": {"value_cents": bal["pending_payout_cents"], "held_cents": bal["held_cents"]},
        "available_balance_cents": bal["available_cents"],
        "recent": [_row(e, now) for e in recent],
        "summary": {
            "sales_revenue_cents": cur["sales_cents"], "platform_fees_cents": cur["fees_cents"], "refunds_cents": cur["refunds_cents"],
            "adjustments_cents": cur["adjustments_cents"], "net_payout_cents": cur["net_cents"],
        },
    }


@transactions_router.get("", response=list[EntryOut], summary="All Transactions (tabs, search, filters)")
@paginate(PagePagination)
def transactions_list(request, tab: str | None = None, q: str | None = None, status: str | None = None, date_from: date | None = None,
                      date_to: date | None = None, amount_min: int | None = None, amount_max: int | None = None):
    now = timezone.now()
    return [_row(e, now) for e in _filtered(request, tab, q, status, date_from, date_to, amount_min, amount_max)]


@transactions_router.get("/tabs", response=TabsOut, summary="Counts for the tab labels: Sales (1,102), Payouts (24)…")
def transactions_tabs(request):
    return services.tab_counts(request.store)


@transactions_router.get("/export.csv", summary="'Export Transactions' — CSV of the current filters")
def transactions_export(request, tab: str | None = None, q: str | None = None, status: str | None = None, date_from: date | None = None,
                        date_to: date | None = None, amount_min: int | None = None, amount_max: int | None = None):
    now = timezone.now()
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="transactions.csv"'
    writer = csv.writer(response)
    writer.writerow(["reference", "date", "type", "status", "order", "customer", "amount", "currency", "description"])
    for entry in _filtered(request, tab, q, status, date_from, date_to, amount_min, amount_max).order_by("-created_at", "-reference"):
        row = _row(entry, now)
        writer.writerow([
            row["reference"], timezone.localtime(entry.created_at).strftime("%Y-%m-%d %H:%M"), row["type"], row["status"],
            row["order_number"] or "", row["customer"] or "", f"{entry.amount_cents / 100:.2f}", entry.currency, entry.description,
        ])
    return response


@transactions_router.get("/{reference}", response={200: DetailOut, 404: ErrorOut}, summary="Transaction Details")
def transaction_detail(request, reference: str):
    entry = services.entries(request.store).filter(reference=reference).first()
    if entry is None:
        raise HttpError(404, "Transaction not found")
    row = _row(entry)
    order = entry.order
    lines = [{"label": entry.get_type_display(), "amount_cents": entry.amount_cents}]
    if entry.type == T.SALE:
        meta = entry.meta
        fee = LedgerEntry.objects.filter(order=order, store=entry.store, type=T.FEE).first()
        lines = [
            {"label": "Items", "amount_cents": meta.get("lines_cents", entry.amount_cents)},
            {"label": "Discount", "amount_cents": -meta.get("discount_cents", 0)},
            {"label": "Delivery", "amount_cents": meta.get("delivery_cents", 0)},
            {"label": "Platform fee", "amount_cents": fee.amount_cents if fee else 0},
            {"label": "Net amount", "amount_cents": entry.amount_cents + (fee.amount_cents if fee else 0)},
        ]
    return {
        **row,
        "order": {"id": order.pk, "order_number": order.order_number, "customer": _customer(order), "order_total_cents": order.total_cents} if order else None,
        "payment": {"method": order.payment_method_type, "status": order.payment_status} if order else None,
        "breakdown": lines, "hold": entry.meta.get("hold"),
    }


# --------------------------------------------------------------------------- #
# Staff
# --------------------------------------------------------------------------- #


def _store(request, store_id: UUID) -> Store:
    """The store, if it is in one of the staff member's markets (otherwise 404)."""
    store = scope(request, Store.objects.select_related("country")).filter(pk=store_id).first()
    if store is None:
        raise HttpError(404, "Store not found")
    return store


@admin_ledger_router.get("/stores/{store_id}", auth=StaffAuth("ledger.view"), response={200: StoreBalanceOut, 404: ErrorOut}, summary="Staff: a store's balances (what a payout can be)")
def admin_balances(request, store_id: UUID):
    store = _store(request, store_id)
    return {"store_id": store.pk, "store_name": store.display_name, "currency": store.country.currency, **services.balances(store)}


@admin_ledger_router.post("/adjustments", auth=StaffAuth("ledger.adjust"), response={201: EntryOut, 404: ErrorOut, 422: ErrorOut}, summary="Staff: credit or debit a store, with a reason")
def admin_adjust(request, payload: AdjustmentIn):
    entry = services.adjust(_store(request, payload.store_id), amount_cents=payload.amount_cents, note=payload.note, by=request.auth)
    return 201, _row(entry)


@admin_payouts_router.get("", auth=StaffAuth("payouts.view"), response=list[PayoutOut], summary="Staff: payouts, newest first")
@paginate(PagePagination)
def admin_payout_list(request, status: str | None = None, store_id: UUID | None = None):
    qs = scope(request, Payout.objects.select_related("store"), "store__country")
    if status:
        qs = qs.filter(status=status)
    if store_id:
        qs = qs.filter(store_id=store_id)
    return [_payout_out(p) for p in qs]


@admin_payouts_router.post("", auth=StaffAuth("payouts.manage"), response={201: PayoutOut, 404: ErrorOut, 409: ErrorOut, 422: ErrorOut}, summary="Staff: pay a store out of its available balance")
def admin_payout_create(request, payload: PayoutIn):
    payout = services.create_payout(_store(request, payload.store_id), amount_cents=payload.amount_cents, note=payload.note, by=request.auth)
    return 201, _payout_out(Payout.objects.select_related("store").get(pk=payout.pk))


@admin_payouts_router.patch("/{payout_id}/status", auth=StaffAuth("payouts.manage"), response={200: PayoutOut, 404: ErrorOut, 409: ErrorOut, 422: ErrorOut}, summary="Staff: mark a payout completed or failed")
def admin_payout_status(request, payout_id: UUID, payload: PayoutStatusIn):
    payout = scope(request, Payout.objects.select_related("store"), "store__country").filter(pk=payout_id).first()
    if payout is None:
        raise HttpError(404, "Payout not found")
    payout = services.set_payout_status(payout, payload.status, request.auth)
    return _payout_out(Payout.objects.select_related("store").get(pk=payout.pk))
