"""Seller routers.

  /seller/application/*   seller (JWT + is_seller): 5-step wizard, documents, submit, status
  /seller/store/*         slug check + activation
  /lookups/*              public dropdown sources for the wizard
  /admin/seller-applications/*   staff: KYC review
"""

from typing import Optional
from uuid import UUID

from django.conf import settings
from django.shortcuts import get_object_or_404
from ninja import File, Form, Query, Router
from ninja.errors import HttpError
from ninja.files import UploadedFile
from ninja.pagination import paginate

from core.pagination import PageOrLimitPagination

from accounts.services import client_ip
from core.auth import SellerAuth, StaffAuth
from staff.access import check_market, scope
from core.models import Bank
from core.schemas import ErrorOut, MessageOut
from markets import services as markets

from . import policy, rules, services
from .models import SellerApplication, SellerApplicationDirector, SellerDocument, Store
from .schemas import (
    AccountTypeLit,
    AdminApplicationListOut,
    AdminApplicationOut,
    ApplicationOut,
    ApplicationStatusOut,
    ApproveIn,
    BankingIn,
    BankOut,
    BasicInfoIn,
    BusinessInfoIn,
    DirectorIn,
    DirectorOut,
    DocumentOut,
    DocumentReviewIn,
    DocumentTypeLit,
    ApplicationRejectIn,
    RepresentativeIn,
    RequestInfoIn,
    ResubmitIn,
    SellerLookupsOut,
    SlugAvailableOut,
    StoreActivateIn,
    StoreOut,
    SubmitIn,
)

seller_router = Router(tags=["seller"], auth=SellerAuth())
lookups_router = Router(tags=["lookups"])
admin_sellers_router = Router(tags=["admin · sellers"], auth=StaffAuth())


def _choices(choices) -> list[dict]:
    return [{"value": v, "label": l} for v, l in choices]


# --------------------------------------------------------------------------- #
# /seller/application
# --------------------------------------------------------------------------- #


@seller_router.get("/application", response={200: ApplicationOut, 404: ErrorOut}, summary="Full application (wizard + review page)")
def application_get(request):
    return services.serialize(services.get_application(request.auth))


def _step(request, step: int, payload) -> dict:
    app = services.get_application(request.auth)
    services.save_step(app, step, payload.dict(exclude_unset=True))
    return services.serialize(services.get_application(request.auth))


_STEP_RESP = {200: ApplicationOut, 404: ErrorOut, 409: ErrorOut, 422: ErrorOut}


@seller_router.patch("/application/steps/basic-info", response=_STEP_RESP, summary="Step 1 autosave")
def step_basic_info(request, payload: BasicInfoIn):
    return _step(request, 1, payload)


@seller_router.patch("/application/steps/business-info", response=_STEP_RESP, summary="Step 2 autosave")
def step_business_info(request, payload: BusinessInfoIn):
    return _step(request, 2, payload)


@seller_router.patch("/application/steps/representative", response=_STEP_RESP, summary="Step 3 autosave")
def step_representative(request, payload: RepresentativeIn):
    return _step(request, 3, payload)


@seller_router.patch("/application/steps/banking", response=_STEP_RESP, summary="Step 4 autosave")
def step_banking(request, payload: BankingIn):
    return _step(request, 4, payload)


# --- directors -----------------------------------------------------------------


@seller_router.post("/application/directors", response={201: DirectorOut, 409: ErrorOut, 422: ErrorOut}, summary="'+ Add Another Director'")
def director_add(request, payload: DirectorIn):
    app = services.get_application(request.auth)
    return 201, services.add_director(app, payload.dict())


@seller_router.patch("/application/directors/{director_id}", response={200: DirectorOut, 404: ErrorOut, 409: ErrorOut})
def director_update(request, director_id: UUID, payload: DirectorIn):
    app = services.get_application(request.auth)
    director = get_object_or_404(SellerApplicationDirector, pk=director_id, application=app)
    return services.update_director(app, director, payload.dict(exclude_unset=True))


@seller_router.delete("/application/directors/{director_id}", response={204: None, 404: ErrorOut, 409: ErrorOut})
def director_delete(request, director_id: UUID):
    app = services.get_application(request.auth)
    services.delete_director(app, get_object_or_404(SellerApplicationDirector, pk=director_id, application=app))
    return 204, None


# --- documents -----------------------------------------------------------------


@seller_router.post(
    "/application/documents",
    response={201: DocumentOut, 409: ErrorOut, 422: ErrorOut},
    summary="Upload or replace a KYC document (PDF/JPG/PNG ≤ 10 MB)",
)
def document_upload(request, type: DocumentTypeLit = Form(...), file: UploadedFile = File(...)):
    app = services.get_application(request.auth)
    return 201, services.upload_document(app, doc_type=type, file=file)


@seller_router.get("/application/documents", response=list[DocumentOut], summary="Latest version of each document")
def document_list(request):
    return list(services.latest_documents(services.get_application(request.auth)).values())


# --- submit / status -------------------------------------------------------------


@seller_router.post("/application/submit", response={200: ApplicationStatusOut, 409: ErrorOut, 422: ErrorOut}, summary="Review & Submit → Submit")
def application_submit(request, payload: SubmitIn):
    app = services.get_application(request.auth)
    services.submit(app, ip=client_ip(request))
    return services.status_payload(services.get_application(request.auth))


@seller_router.post("/application/resubmit", response={200: ApplicationStatusOut, 409: ErrorOut, 422: ErrorOut}, summary="Update Application → Resubmit")
def application_resubmit(request, payload: ResubmitIn):
    app = services.get_application(request.auth)
    services.resubmit(app)
    return services.status_payload(services.get_application(request.auth))


@seller_router.get("/application/status", response={200: ApplicationStatusOut, 404: ErrorOut}, summary="Application Status / progress timeline")
def application_status(request):
    return services.status_payload(services.get_application(request.auth))


# --------------------------------------------------------------------------- #
# /seller/store
# --------------------------------------------------------------------------- #


@seller_router.get("/store/slug-available", response=SlugAvailableOut, summary="Check a store URL")
def store_slug_available(request, slug: str = Query(..., min_length=1, max_length=140)):
    normalised = services.slug_for(slug)
    try:
        current = services.get_application(request.auth).store
    except Store.DoesNotExist:
        current = None
    available = services.slug_available(normalised, exclude_store=current)
    return {"slug": normalised, "available": available, "suggestion": normalised if available else services.suggest_slug(normalised)}


@seller_router.post("/store/activate", response={200: StoreOut, 404: ErrorOut, 409: ErrorOut, 422: ErrorOut}, summary="'Activate & Start Selling'")
def store_activate(request, payload: StoreActivateIn):
    app = services.get_application(request.auth)
    return services.activate_store(app, display_name=payload.display_name, slug=payload.slug)


@seller_router.get("/store", response={200: StoreOut, 404: ErrorOut}, summary="My store")
def store_get(request):
    app = services.get_application(request.auth)
    try:
        return app.store
    except Store.DoesNotExist:
        return 404, {"detail": "Store not created yet"}


# --------------------------------------------------------------------------- #
# /lookups
# --------------------------------------------------------------------------- #


@lookups_router.get("/seller", response=SellerLookupsOut, summary="Dropdown options for the seller wizard (+ the market's KYC policy)")
def lookups_seller(request, country: Optional[str] = Query(None, min_length=2, max_length=2),
                   account_type: AccountTypeLit = Query("registered_company")):
    market = markets.countries_qs().filter(pk=country.upper(), is_active=True).first() if country else getattr(request, "country", None)
    if country and market is None:
        raise HttpError(404, "Unknown country")
    return {
        "policy": policy.for_country(market, language=getattr(request, "language", None)).as_payload(account_type),
        "account_types": _choices(SellerApplication.AccountType.choices),
        "business_types": _choices(SellerApplication.BusinessType.choices),
        "representative_roles": _choices(SellerApplication.RepresentativeRole.choices),
        "bank_account_types": _choices(SellerApplication.BankAccountType.choices),
        "document_types": _choices(SellerDocument.DocumentType.choices),
        "field_groups": [{"value": k, "label": k.replace("_", " ").title()} for k in rules.FIELD_GROUPS],
        "document_max_bytes": settings.SELLER_DOCUMENT_MAX_BYTES,
        "document_allowed_types": sorted(rules.DOCUMENT_ALLOWED_TYPES),
    }


@lookups_router.get("/banks", response=list[BankOut], summary="'Select your bank'")
def lookups_banks(request, country: Optional[str] = Query(None, min_length=2, max_length=2)):
    qs = Bank.objects.filter(is_active=True)
    if country:
        qs = qs.filter(country=country.upper())
    return qs


# --------------------------------------------------------------------------- #
# /admin/seller-applications
# --------------------------------------------------------------------------- #


def _admin_get(request, application_id):
    """The application, if it is in one of the staff member's markets (otherwise 404, as if it did not exist)."""
    app = services.admin_get(application_id)
    check_market(request, app.country_id, "Application not found")
    return app


@admin_sellers_router.get("", auth=StaffAuth("sellers.view"), response=list[AdminApplicationListOut], summary="Review queue")
@paginate(PageOrLimitPagination)
def admin_list(request, status: Optional[str] = None, account_type: Optional[str] = None, q: Optional[str] = None,
               country: Optional[str] = Query(None, min_length=2, max_length=2)):
    qs = scope(request, SellerApplication.objects.select_related("user").exclude(status=SellerApplication.ApplicationStatus.DRAFT))
    if status:
        qs = qs.filter(status=status)
    if country:
        qs = qs.filter(country_id=country.upper())
    if account_type:
        qs = qs.filter(account_type=account_type)
    if q:
        from django.db.models import Q

        qs = qs.filter(
            Q(application_number__icontains=q) | Q(store_display_name__icontains=q)
            | Q(legal_business_name__icontains=q) | Q(user__email__icontains=q)
        )
    return [dict(
        id=a.id, application_number=a.application_number, country=a.country_id, account_type=a.account_type, status=a.status,
        store_display_name=a.store_display_name, legal_business_name=a.legal_business_name,
        applicant=a.user, submitted_at=a.submitted_at, updated_at=a.updated_at,
    ) for a in qs.order_by("submitted_at")]


@admin_sellers_router.get("/{application_id}", auth=StaffAuth("sellers.view"), response={200: AdminApplicationOut, 404: ErrorOut})
def admin_detail(request, application_id: UUID):
    return services.serialize(_admin_get(request, application_id), admin=True)


@admin_sellers_router.post("/{application_id}/start-review", auth=StaffAuth("sellers.review"), response={200: AdminApplicationOut, 404: ErrorOut, 409: ErrorOut})
def admin_start_review(request, application_id: UUID):
    services.start_review(_admin_get(request, application_id), reviewer=request.auth)
    return services.serialize(_admin_get(request, application_id), admin=True)


@admin_sellers_router.post("/{application_id}/request-info", auth=StaffAuth("sellers.review"), response={200: AdminApplicationOut, 404: ErrorOut, 409: ErrorOut, 422: ErrorOut}, summary="'We Need More Information'")
def admin_request_info(request, application_id: UUID, payload: RequestInfoIn):
    services.request_info(
        _admin_get(request, application_id), reviewer=request.auth, message=payload.message, items=[i.dict() for i in payload.items]
    )
    return services.serialize(_admin_get(request, application_id), admin=True)


@admin_sellers_router.post("/{application_id}/approve", auth=StaffAuth("sellers.review"), response={200: AdminApplicationOut, 404: ErrorOut, 409: ErrorOut})
def admin_approve(request, application_id: UUID, payload: ApproveIn = None):
    services.approve(_admin_get(request, application_id), reviewer=request.auth, note=payload.note if payload else "")
    return services.serialize(_admin_get(request, application_id), admin=True)


@admin_sellers_router.post("/{application_id}/reject", auth=StaffAuth("sellers.review"), response={200: AdminApplicationOut, 404: ErrorOut, 409: ErrorOut})
def admin_reject(request, application_id: UUID, payload: ApplicationRejectIn):
    services.reject(_admin_get(request, application_id), reviewer=request.auth, reason=payload.reason)
    return services.serialize(_admin_get(request, application_id), admin=True)


@admin_sellers_router.post("/{application_id}/documents/{document_id}/review", auth=StaffAuth("sellers.review"), response={200: DocumentOut, 404: ErrorOut})
def admin_review_document(request, application_id: UUID, document_id: UUID, payload: DocumentReviewIn):
    doc = get_object_or_404(SellerDocument.objects.select_related("application"), pk=document_id, application_id=application_id)
    check_market(request, doc.application.country_id, "Application not found")
    return services.review_document(doc, status=payload.status, note=payload.note)
