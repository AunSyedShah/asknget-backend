"""Returns routers.

  /returns/policy                    public   the market's rules and reasons (region-scoped)
  /couriers/{slug}/webhook           public   courier callbacks (each courier verifies its own signature)
  /me/orders/{id}/returnable         buyer    which lines can be returned, and until when
  /me/orders/{id}/returns            buyer    ask for a return
  /me/returns/*                      buyer    list, detail, withdraw, dispute, add tracking
  /seller/returns/*                  store    section "orders": list, detail, approve, reject, received
  /admin/returns/*                   staff    queue, decide, mark refund paid
  /admin/returns/{policies,reasons,rules}   staff   the rules, per market
"""

from uuid import UUID

from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpRequest
from django.utils import timezone
from ninja import File, Form, Query, Router
from ninja.errors import HttpError
from ninja.files import UploadedFile
from ninja.pagination import paginate

from catalog.models import Category
from config import services as config
from core.auth import JWTAuth, StaffAuth
from staff.access import check_market, require_every_market, scope
from core.pagination import PagePagination
from core.schemas import ErrorOut
from markets.context import get_country
from markets.models import Country, ReturnPolicy
from orders import services as order_services
from portal.auth import StoreAuth

from . import policy, services
from .models import ReturnReason, ReturnRequest, ReturnRule
from .schemas import (
    AdminReturnOut,
    AdminReturnRowOut,
    DecideIn,
    EscalateIn,
    PolicyAdminIn,
    PolicyAdminOut,
    PolicyOut,
    ReasonAdminIn,
    ReasonAdminOut,
    ReasonAdminPatch,
    ReceivedIn,
    RefundPaidIn,
    ReturnRejectIn,
    ReturnableOut,
    ReturnCreateIn,
    ReturnOut,
    ReturnRowOut,
    ReturnTabsOut,
    RuleAdminIn,
    RuleAdminOut,
    RuleAdminPatch,
    SellerReturnOut,
    SellerReturnRowOut,
    ShipmentOut,
    TrackingIn,
    WebhookOut,
)

returns_public_router = Router(tags=["returns"])
my_returns_router = Router(tags=["me · returns"], auth=JWTAuth())
seller_returns_router = Router(tags=["seller: returns"], auth=StoreAuth(section="orders"))
admin_returns_router = Router(tags=["admin · returns"], auth=StaffAuth())

STAFF_TABS = ("all", "open", "requested", "approved", "rejected", "escalated", "received", "refunded", "cancelled", "overdue")


def _tab_counts(qs) -> dict:
    now = timezone.now()
    per = dict(qs.values_list("status").annotate(n=Count("id")).values_list("status", "n"))
    counts = {s.value: per.get(s.value, 0) for s in ReturnRequest.Status}
    counts["all"] = sum(per.values())
    counts["open"] = sum(per.get(s, 0) for s in ReturnRequest.OPEN)
    counts["overdue"] = qs.filter(status=ReturnRequest.Status.REQUESTED, response_due_at__lt=now).count()
    return counts


# --------------------------------------------------------------------------------------
# Public
# --------------------------------------------------------------------------------------


@returns_public_router.get("/returns/policy", response=PolicyOut, summary="The market's return rules and the reasons on the form")
def return_policy(request: HttpRequest):
    country = get_country() or Country.objects.get(is_default=True)
    pol = country.returns
    active = pol is not None and pol.is_active
    from markets.i18n import pick

    return {
        "country": country.code, "accepts_returns": active,
        "change_of_mind_days": pol.window_days if active else 0, "defect_days": pol.defect_days if active else 0,
        "restocking_fee_bps": pol.restocking_fee_bps if active else 0,
        "summary": pick(pol.summary, country=country) if pol else "", "terms": pick(pol.terms, country=country) if pol else "", "url": pol.url if pol else "",
        "reasons": list(services.reasons_for(country)) if active else [],
        "max_photos": config.get("returns.max_photos"), "allow_escalation": config.get("returns.allow_escalation", country=country),
        "courier": config.get("returns.courier", country=country),
    }


@returns_public_router.post("/couriers/{slug}/webhook", response={200: WebhookOut, 404: ErrorOut}, summary="Courier callback (404 for couriers without one)")
def courier_webhook(request: HttpRequest, slug: str):
    return {"applied": services.handle_webhook(slug, request.body, request.headers)}


# --------------------------------------------------------------------------------------
# Buyer
# --------------------------------------------------------------------------------------


@my_returns_router.get("/orders/{order_id}/returnable", response={200: ReturnableOut, 404: ErrorOut, 422: ErrorOut}, summary="Which lines can be returned (optionally for a reason)")
def returnable(request, order_id: UUID, reason: str | None = None):
    order = order_services.get_my_order(request.auth, order_id)
    chosen = services.reasons_for(order.country).filter(code=reason).first() if reason else None
    if reason and chosen is None:
        raise HttpError(422, "unknown_reason")
    checks = policy.eligibility(order, chosen.kind if chosen else None)
    lines = [
        {"order_item_id": c.item.pk, "product_name": c.item.product_name, "variant_summary": c.item.variant_summary, "image_url": c.item.image_url,
         "qty": c.item.qty, "line_total_cents": c.item.line_total_cents, "eligible": c.eligible, "why_not": c.why_not, "deadline": c.deadline,
         "change_of_mind_until": c.change_of_mind_until, "defect_until": c.defect_until, "store_id": c.item.store_id, "store_name": c.item.store.display_name}
        for c in checks
    ]
    return {"order_number": order.order_number, "currency": order.currency, "lines": lines,
            "reasons": list(services.reasons_for(order.country)), "can_request": any(c.eligible for c in checks)}


@my_returns_router.post(
    "/orders/{order_id}/returns", response={201: ReturnOut, 404: ErrorOut, 409: ErrorOut, 422: ErrorOut},
    summary="Ask to return items (multipart: reason, note, item_ids[], photos[])",
)
def create_return(request, order_id: UUID, payload: Form[ReturnCreateIn], photos: list[UploadedFile] = File(None)):
    order = order_services.get_my_order(request.auth, order_id)
    created = services.request_return(request.auth, order, item_ids=payload.item_ids, reason_code=payload.reason, note=payload.note, files=photos)
    return 201, created


@my_returns_router.get("/returns", response=list[ReturnRowOut], summary="My returns (tabs: all, open, requested, approved, rejected, refunded, cancelled)")
@paginate(PagePagination)
def my_returns(request, tab: str = Query("all")):
    return services.my_requests(request.auth, tab)


@my_returns_router.get("/returns/{return_id}", response={200: ReturnOut, 404: ErrorOut}, summary="Return status and timeline")
def my_return(request, return_id: UUID):
    return services.get_mine(request.auth, return_id)


@my_returns_router.post("/returns/{return_id}/cancel", response={200: ReturnOut, 404: ErrorOut, 409: ErrorOut}, summary="Withdraw before it is sent")
def my_return_cancel(request, return_id: UUID):
    services.cancel(services.get_mine(request.auth, return_id), user=request.auth)
    return services.get_mine(request.auth, return_id)


@my_returns_router.post("/returns/{return_id}/escalate", response={200: ReturnOut, 404: ErrorOut, 409: ErrorOut}, summary="Dispute a rejection: staff decide")
def my_return_escalate(request, return_id: UUID, payload: EscalateIn = None):
    services.escalate(services.get_mine(request.auth, return_id), user=request.auth, note=payload.note if payload else "")
    return services.get_mine(request.auth, return_id)


@my_returns_router.post("/returns/{return_id}/tracking", response={200: ShipmentOut, 404: ErrorOut, 409: ErrorOut}, summary="'I sent it': add the tracking number")
def my_return_tracking(request, return_id: UUID, payload: TrackingIn):
    return services.add_tracking(services.get_mine(request.auth, return_id), user=request.auth, tracking_number=payload.tracking_number, courier=payload.carrier)


# --------------------------------------------------------------------------------------
# Seller
# --------------------------------------------------------------------------------------


@seller_returns_router.get("", response=list[SellerReturnRowOut], summary="Returns for this store (tabs: all, open, requested, approved, rejected, escalated, received, refunded, cancelled, overdue)")
@paginate(PagePagination)
def seller_returns(request, tab: str = Query("all"), q: str | None = None):
    qs = services.store_requests(request.store, tab)
    if q:
        qs = qs.filter(Q(reference__icontains=q) | Q(order__order_number__icontains=q) | Q(order__contact_last_name__icontains=q) | Q(order__contact_first_name__icontains=q))
    return qs


@seller_returns_router.get("/summary", response=ReturnTabsOut, summary="Counts for the tabs")
def seller_returns_summary(request):
    return _tab_counts(ReturnRequest.objects.filter(store=request.store))


@seller_returns_router.get("/{return_id}", response={200: SellerReturnOut, 404: ErrorOut})
def seller_return(request, return_id: UUID):
    return services.get_for_store(request.store, return_id)


@seller_returns_router.post("/{return_id}/approve", response={200: SellerReturnOut, 404: ErrorOut, 409: ErrorOut}, summary="Approve: the buyer may send it back")
def seller_return_approve(request, return_id: UUID):
    services.approve(services.get_for_store(request.store, return_id), actor="seller", user=request.auth)
    return services.get_for_store(request.store, return_id)


@seller_returns_router.post("/{return_id}/reject", response={200: SellerReturnOut, 404: ErrorOut, 409: ErrorOut, 422: ErrorOut}, summary="Reject with a reason the buyer sees")
def seller_return_reject(request, return_id: UUID, payload: ReturnRejectIn):
    services.reject(services.get_for_store(request.store, return_id), actor="seller", user=request.auth, reason=payload.reason)
    return services.get_for_store(request.store, return_id)


@seller_returns_router.post("/{return_id}/received", response={200: SellerReturnOut, 404: ErrorOut, 409: ErrorOut}, summary="The parcel arrived: good = refund now, damaged = staff decide")
def seller_return_received(request, return_id: UUID, payload: ReceivedIn):
    services.mark_received(services.get_for_store(request.store, return_id), user=request.auth, condition=payload.condition, restock=payload.restock)
    return services.get_for_store(request.store, return_id)


# --------------------------------------------------------------------------------------
# Staff: the queue
# --------------------------------------------------------------------------------------


@admin_returns_router.get("", auth=StaffAuth("returns.view"), response=list[AdminReturnRowOut], summary="All returns (default: escalated and damaged first)")
@paginate(PagePagination)
def admin_returns(request, tab: str = Query("all"), country: str | None = None, store_id: UUID | None = None, q: str | None = None):
    qs = scope(request, services.all_requests(tab))
    if country:
        qs = qs.filter(country_id=country.upper())
    if store_id:
        qs = qs.filter(store_id=store_id)
    if q:
        qs = qs.filter(Q(reference__icontains=q) | Q(order__order_number__icontains=q) | Q(order__contact_email__icontains=q))
    return qs


@admin_returns_router.get("/summary", auth=StaffAuth("returns.view"), response=ReturnTabsOut)
def admin_returns_summary(request, country: str | None = None):
    qs = scope(request, ReturnRequest.objects.all())
    return _tab_counts(qs.filter(country_id=country.upper()) if country else qs)


# --- the rules (declared before the {return_id} routes so "policies" is never read as an id) ---


@admin_returns_router.get("/policies", auth=StaffAuth("returns.view"), response=list[PolicyAdminOut], summary="Every market's return policy")
def admin_policies(request):
    rows = []
    for country in scope(request, Country.objects.select_related("return_policy").order_by("sort_order", "name"), "code"):
        pol = country.returns
        rows.append({"country": country.code, "country_name": country.name, "is_active": bool(pol and pol.is_active),
                     "window_days": pol.window_days if pol else 0, "defect_days": pol.defect_days if pol else 0,
                     "restocking_fee_bps": pol.restocking_fee_bps if pol else 0, "summary": pol.summary if pol else {},
                     "terms": pol.terms if pol else {}, "url": pol.url if pol else ""})
    return rows


@admin_returns_router.put("/policies/{code}", auth=StaffAuth("returns.configure"), response={200: PolicyAdminOut, 404: ErrorOut, 422: ErrorOut}, summary="Set a market's return policy (creates it if the market has none)")
def admin_policy_put(request, code: str, payload: PolicyAdminIn):
    country = Country.objects.filter(pk=code.upper()).first()
    if country is None:
        raise HttpError(404, "Unknown market")
    check_market(request, country.code, "Unknown market")
    pol = country.returns or ReturnPolicy(country=country)
    for field, value in payload.dict().items():
        setattr(pol, field, value)
    _validate(pol)
    pol.save()
    return {"country": country.code, "country_name": country.name, "is_active": pol.is_active, "window_days": pol.window_days, "defect_days": pol.defect_days,
            "restocking_fee_bps": pol.restocking_fee_bps, "summary": pol.summary, "terms": pol.terms, "url": pol.url}


def _validate(instance) -> None:
    from django.core.exceptions import ValidationError

    try:
        instance.full_clean()
    except ValidationError as exc:
        raise HttpError(422, "; ".join(f"{k}: {' '.join(v)}" for k, v in exc.message_dict.items()))


@admin_returns_router.get("/reasons", auth=StaffAuth("returns.view"), response=list[ReasonAdminOut])
def admin_reasons(request):
    return ReturnReason.objects.prefetch_related("countries").order_by("sort_order", "code")


def _countries(codes) -> list:
    codes = [c.upper() for c in codes]
    found = list(Country.objects.filter(pk__in=codes))
    if len(found) != len(set(codes)):
        raise HttpError(422, "Unknown market in countries")
    return found


@admin_returns_router.post("/reasons", auth=StaffAuth("returns.configure"), response={201: ReasonAdminOut, 409: ErrorOut, 422: ErrorOut})
@transaction.atomic
def admin_reason_create(request, payload: ReasonAdminIn):
    require_every_market(request)                 # reasons are shared by every market
    if ReturnReason.objects.filter(code=payload.code).exists():
        raise HttpError(409, "A reason with that code exists")
    data = payload.dict()
    countries = _countries(data.pop("countries"))
    reason = ReturnReason(**data)
    _validate(reason)
    reason.save()
    reason.countries.set(countries)
    return 201, ReturnReason.objects.prefetch_related("countries").get(pk=reason.pk)


@admin_returns_router.patch("/reasons/{reason_id}", auth=StaffAuth("returns.configure"), response={200: ReasonAdminOut, 404: ErrorOut, 422: ErrorOut})
@transaction.atomic
def admin_reason_patch(request, reason_id: UUID, payload: ReasonAdminPatch):
    require_every_market(request)
    reason = ReturnReason.objects.filter(pk=reason_id).first()
    if reason is None:
        raise HttpError(404, "Reason not found")
    data = payload.dict(exclude_unset=True)
    countries = _countries(data.pop("countries")) if "countries" in data else None
    for field, value in data.items():
        setattr(reason, field, value)
    _validate(reason)
    reason.save()
    if countries is not None:
        reason.countries.set(countries)
    return ReturnReason.objects.prefetch_related("countries").get(pk=reason.pk)


@admin_returns_router.get("/rules", auth=StaffAuth("returns.view"), response=list[RuleAdminOut], summary="Per-category overrides of the market policy")
def admin_rules(request, country: str | None = None):
    qs = ReturnRule.objects.select_related("category")
    if request.staff.markets is not None:
        qs = qs.filter(Q(country_id__in=request.staff.markets) | Q(country__isnull=True))
    return qs.filter(Q(country_id=country.upper()) | Q(country__isnull=True)) if country else qs


@admin_returns_router.post("/rules", auth=StaffAuth("returns.configure"), response={201: RuleAdminOut, 409: ErrorOut, 422: ErrorOut})
def admin_rule_create(request, payload: RuleAdminIn):
    country = _countries([payload.country])[0] if payload.country else None
    _may_edit_rule(request, country.code if country else None)
    category = Category.objects.filter(pk=payload.category_id).first()
    if category is None:
        raise HttpError(422, "Unknown category")
    if ReturnRule.objects.filter(country=country, category=category).exists():
        raise HttpError(409, "That category already has a rule for this market: edit it instead")
    rule = ReturnRule.objects.create(country=country, category=category, change_of_mind_days=payload.change_of_mind_days,
                                     defect_days=payload.defect_days, note=payload.note, is_active=payload.is_active)
    return 201, ReturnRule.objects.select_related("category").get(pk=rule.pk)


@admin_returns_router.patch("/rules/{rule_id}", auth=StaffAuth("returns.configure"), response={200: RuleAdminOut, 404: ErrorOut})
def admin_rule_patch(request, rule_id: UUID, payload: RuleAdminPatch):
    rule = ReturnRule.objects.select_related("category").filter(pk=rule_id).first()
    if rule is None:
        raise HttpError(404, "Rule not found")
    _may_edit_rule(request, rule.country_id)
    data = payload.dict(exclude_unset=True)
    if data.pop("clear_change_of_mind", False):
        rule.change_of_mind_days = None
        data.pop("change_of_mind_days", None)
    if data.pop("clear_defect", False):
        rule.defect_days = None
        data.pop("defect_days", None)
    for field, value in data.items():
        setattr(rule, field, value)
    rule.save()
    return rule


@admin_returns_router.delete("/rules/{rule_id}", auth=StaffAuth("returns.configure"), response={204: None, 404: ErrorOut})
def admin_rule_delete(request, rule_id: UUID):
    rule = ReturnRule.objects.filter(pk=rule_id).first()
    if rule is None:
        raise HttpError(404, "Rule not found")
    _may_edit_rule(request, rule.country_id)
    rule.delete()
    return 204, None


# --- one request ---------------------------------------------------------------------------


def _get_any(request, return_id):
    """The return, if it is in one of the staff member's markets (otherwise 404)."""
    r = services.get_any(return_id)
    check_market(request, r.country_id, "Return not found")
    return r


def _may_edit_rule(request, country_code):
    """A rule for every market (no country) is a platform-wide setting; a market's rule needs that market."""
    if country_code is None:
        require_every_market(request)
    else:
        check_market(request, country_code, "Rule not found")


@admin_returns_router.get("/{return_id}", auth=StaffAuth("returns.view"), response={200: AdminReturnOut, 404: ErrorOut})
def admin_return(request, return_id: UUID):
    return _get_any(request, return_id)


@admin_returns_router.post("/{return_id}/decide", auth=StaffAuth("returns.decide"), response={200: AdminReturnOut, 404: ErrorOut, 409: ErrorOut, 422: ErrorOut}, summary="Settle an escalated rejection or a damaged parcel (final)")
def admin_return_decide(request, return_id: UUID, payload: DecideIn):
    services.decide(_get_any(request, return_id), user=request.auth, outcome=payload.outcome, note=payload.note)
    return _get_any(request, return_id)


@admin_returns_router.post("/{return_id}/refund-paid", auth=StaffAuth("returns.refund"), response={200: AdminReturnOut, 404: ErrorOut, 409: ErrorOut}, summary="Staff paid the buyer")
def admin_return_refund_paid(request, return_id: UUID, payload: RefundPaidIn = None):
    services.mark_refund_paid(_get_any(request, return_id), user=request.auth, reference=payload.reference if payload else "")
    return _get_any(request, return_id)
