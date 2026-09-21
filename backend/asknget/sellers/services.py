"""Business rules for seller onboarding, KYC review and store activation.

State lives in `rules.py`; this module applies it. Routers stay thin.
"""

import secrets
from datetime import datetime

from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from django.utils.text import slugify
from ninja.errors import HttpError

from accounts.models import TermsAcceptance
from core.models import Bank, coerce_nulls

from . import policy, rules
from .models import (
    SellerApplication,
    SellerApplicationDirector,
    SellerApplicationStatusEvent,
    SellerDocument,
    SellerInfoRequest,
    SellerInfoRequestItem,
    Store,
)

Status = SellerApplication.ApplicationStatus


# --------------------------------------------------------------------------- #
# Lookups / helpers
# --------------------------------------------------------------------------- #


def get_application(user) -> SellerApplication:
    app = (
        SellerApplication.objects.select_related("bank", "user", "reviewer", "country__kyc_policy")
        .prefetch_related("categories", "directors", "documents", "info_requests__items", "status_events")
        .filter(user=user)
        .first()
    )
    if app is None:
        raise HttpError(404, "No seller application. Register as a seller first.")
    return app


def mask(value: str, keep: int = 4) -> str:
    value = str(value or "")
    return ("•" * max(len(value) - keep, 0) + value[-keep:]) if value else ""


def _assert_editable(app: SellerApplication) -> None:
    if app.status not in rules.EDITABLE_STATUSES:
        raise HttpError(409, f"Application cannot be edited while {app.get_status_display().lower()}")


def _field_filled(app: SellerApplication, field: str) -> bool:
    value = getattr(app, f"{field}_id" if field == "bank" else field)
    return value not in (None, "", [])


def latest_documents(app: SellerApplication) -> dict[str, SellerDocument]:
    """Newest version per document type (superseded rows excluded)."""
    latest: dict[str, SellerDocument] = {}
    for doc in sorted(app.documents.all(), key=lambda d: (d.type, d.version)):
        latest[doc.type] = doc
    return latest


def step_states(app: SellerApplication) -> list[dict]:
    docs = latest_documents(app)
    kyc = policy.policy_for(app)
    required_docs = set(kyc.documents_for(app.account_type))
    required_steps = set(rules.REQUIRED_STEPS[app.account_type])
    states = []
    for step, name in rules.STEP_NAMES.items():
        if step == 5:
            continue
        missing_fields = [f for f in rules.required_fields(app.account_type, step) if not _field_filled(app, f)]
        missing_docs = [
            d for d in required_docs
            if kyc.step_for(d, app.account_type) == step and (d not in docs or docs[d].status == SellerDocument.DocumentStatus.REJECTED)
        ]
        required = step in required_steps
        states.append({
            "step": step,
            "name": name,
            "required": required,
            "complete": not required or (not missing_fields and not missing_docs),
            "missing_fields": missing_fields if required else [],
            "missing_documents": missing_docs if required else [],
        })
    return states


def can_submit(app: SellerApplication) -> bool:
    return app.status == Status.DRAFT and all(s["complete"] for s in step_states(app))


def open_info_request(app: SellerApplication) -> SellerInfoRequest | None:
    return next((r for r in app.info_requests.all() if r.resolved_at is None), None)


def serialize(app: SellerApplication, *, admin: bool = False) -> dict:
    """Build the ApplicationOut / AdminApplicationOut payload."""
    try:
        store = app.store
    except Store.DoesNotExist:
        store = None

    basic = {
        f: getattr(app, f) for f in (
            "first_name", "last_name", "date_of_birth", "phone", "email",
            "residential_address", "residential_postal_code", "residential_city", "residential_province",
        )
    }
    basic["id_passport_number_masked"] = mask(app.id_passport_number)
    business = {
        f: getattr(app, f) for f in (
            "legal_business_name", "store_display_name", "business_type", "company_registration_number", "vat_number",
            "business_email", "business_phone", "business_address", "business_city", "business_province",
            "business_postal_code", "pickup_same_as_business", "pickup_address", "returns_same_as_business",
            "returns_address", "store_description",
        )
    }
    business["categories"] = list(app.categories.all())
    rep = {f: getattr(app, f) for f in ("rep_first_name", "rep_last_name", "rep_role", "rep_mobile", "rep_email")}
    rep["rep_id_passport_number_masked"] = mask(app.rep_id_passport_number)
    banking = {
        "bank": app.bank,
        "bank_account_type": app.bank_account_type,
        "account_holder_name": app.account_holder_name,
        "account_number_last4": app.account_number_last4,
        "branch_code": app.branch_code,
        "tax_reference_number": app.tax_reference_number,
    }
    directors = list(app.directors.all())
    data = {
        "id": app.id,
        "application_number": app.application_number,
        "country_id": app.country_id,
        "policy": policy.policy_for(app).as_payload(app.account_type),
        "account_type": app.account_type,
        "status": app.status,
        "status_headline": rules.STATUS_HEADLINES[app.status],
        "editable": app.status in rules.EDITABLE_STATUSES,
        "current_step": app.current_step,
        "steps": step_states(app),
        "can_submit": can_submit(app),
        "basic_info": basic,
        "business_info": business,
        "representative": rep,
        "banking": banking,
        "directors": directors,
        "documents": list(latest_documents(app).values()),
        "open_info_request": open_info_request(app),
        "reviewer_message": app.reviewer_message,
        "rejection_reason": app.rejection_reason,
        "submitted_at": app.submitted_at,
        "approved_at": app.approved_at,
        "rejected_at": app.rejected_at,
        "activated_at": app.activated_at,
        "store": store,
        "created_at": app.created_at,
        "updated_at": app.updated_at,
    }
    if admin:
        basic["id_passport_number"] = app.id_passport_number
        rep["rep_id_passport_number"] = app.rep_id_passport_number
        banking["account_number"] = app.account_number
        data["applicant"] = app.user
        data["all_documents"] = sorted(app.documents.all(), key=lambda d: d.created_at, reverse=True)
        data["info_requests"] = list(app.info_requests.all())
        data["events"] = list(app.status_events.all())
        data["reviewer_email"] = app.reviewer.email if app.reviewer else None
    return data


def status_payload(app: SellerApplication) -> dict:
    """'Application Progress' timeline: five fixed stages derived from status events."""
    events = list(app.status_events.all())
    reached: dict[str, datetime] = {}
    for ev in events:
        reached.setdefault(ev.to_status, ev.created_at)
    current_idx = next((i for i, (_, statuses) in enumerate(rules.TIMELINE_STAGES) if app.status in statuses), -1)
    if app.status == Status.REJECTED:
        current_idx = len(rules.TIMELINE_STAGES)  # everything before the rejection is history
    timeline = []
    for i, (stage, statuses) in enumerate(rules.TIMELINE_STAGES):
        at = next((reached[s] for s in statuses if s in reached), None)
        if app.status == Status.ACTIVE or i < current_idx:
            state = "done" if at is not None else "skipped"
        elif i == current_idx:
            state = "current"
        else:
            state = "pending"
        timeline.append({"stage": stage, "state": state, "at": at})
    if app.status == Status.REJECTED:
        timeline.append({"stage": "Rejected", "state": "current", "at": app.rejected_at})
    try:
        store = app.store
    except Store.DoesNotExist:
        store = None
    return {
        "application_number": app.application_number,
        "status": app.status,
        "status_headline": rules.STATUS_HEADLINES[app.status],
        "account_type": app.account_type,
        "store_display_name": app.store_display_name,
        "submitted_at": app.submitted_at,
        "timeline": timeline,
        "events": events,
        "open_info_request": open_info_request(app),
        "reviewer_message": app.reviewer_message,
        "rejection_reason": app.rejection_reason,
        "store": store,
    }


def transition(app: SellerApplication, to_status: str, *, actor=None, note: str = "") -> None:
    allowed = rules.TRANSITIONS[app.status]
    if to_status not in allowed:
        raise HttpError(409, f"Cannot move application from {app.status} to {to_status}")
    SellerApplicationStatusEvent.objects.create(application=app, from_status=app.status, to_status=to_status, actor=actor, note=note)
    app.status = to_status


# --------------------------------------------------------------------------- #
# Steps (seller)
# --------------------------------------------------------------------------- #


def _mark_field_groups_touched(app: SellerApplication, changed: set[str]) -> None:
    """Resolve open info-request items whose field group was edited."""
    req = open_info_request(app)
    if not req:
        return
    now = timezone.now()
    for item in req.items.all():
        if item.kind == SellerInfoRequestItem.InfoRequestKind.FIELD_GROUP and item.resolved_at is None:
            if changed & set(rules.FIELD_GROUPS.get(item.target, ())):
                item.resolved_at = now
                item.save(update_fields=["resolved_at", "updated_at"])


@transaction.atomic
def save_step(app: SellerApplication, step: int, data: dict) -> SellerApplication:
    """Autosave one step. `data` is the unset-excluded payload dict."""
    _assert_editable(app)
    category_ids = data.pop("category_ids", None)
    bank_id = data.pop("bank_id", None) if "bank_id" in data else ...
    data = coerce_nulls(SellerApplication, data)

    if step == 4 and "account_number" in data:
        app.account_number_last4 = str(data["account_number"])[-4:] if data["account_number"] else ""

    kyc = policy.policy_for(app)
    for field in ("id_passport_number", "rep_id_passport_number"):
        if data.get(field):
            error = kyc.validate_id_number(data[field])
            if error:
                raise HttpError(422, error)

    for field, value in data.items():
        setattr(app, field, value)

    if bank_id is not ...:
        if bank_id is None:
            app.bank = None
        else:
            bank = Bank.objects.filter(pk=bank_id, is_active=True).first()
            if bank is None:
                raise HttpError(422, "Unknown bank")
            app.bank = bank

    if category_ids is not None:
        from catalog.models import Category

        cats = list(Category.objects.filter(pk__in=category_ids, is_active=True))
        if len(cats) != len(set(category_ids)):
            raise HttpError(422, "One or more categories are unknown or inactive")
        app.categories.set(cats)

    if app.status == Status.DRAFT and step >= app.current_step:
        app.current_step = min(step + 1, 5)
    app.save()
    _mark_field_groups_touched(app, set(data.keys()) | ({"bank"} if bank_id is not ... else set()))
    return app


def add_director(app: SellerApplication, data: dict) -> SellerApplicationDirector:
    _assert_editable(app)
    if app.directors.count() >= 10:
        raise HttpError(422, "At most 10 additional directors")
    next_order = (app.directors.aggregate(m=Max("sort_order"))["m"] or 0) + 1
    return SellerApplicationDirector.objects.create(application=app, sort_order=next_order, **coerce_nulls(SellerApplicationDirector, data))


def update_director(app: SellerApplication, director: SellerApplicationDirector, data: dict) -> SellerApplicationDirector:
    _assert_editable(app)
    for k, v in coerce_nulls(SellerApplicationDirector, data).items():
        setattr(director, k, v)
    director.save()
    return director


def delete_director(app: SellerApplication, director: SellerApplicationDirector) -> None:
    _assert_editable(app)
    director.delete()


# --------------------------------------------------------------------------- #
# Documents
# --------------------------------------------------------------------------- #


def upload_document(app: SellerApplication, *, doc_type: str, file) -> SellerDocument:
    """Upload or replace. A replacement gets version+1 and supersedes the old row."""
    _assert_editable(app)
    if file.content_type not in rules.DOCUMENT_ALLOWED_TYPES:
        raise HttpError(422, "Document must be PDF, JPG or PNG")
    if file.size > settings.SELLER_DOCUMENT_MAX_BYTES:
        raise HttpError(422, "Document must be 10 MB or smaller")

    with transaction.atomic():
        previous = latest_documents(app).get(doc_type)
        doc = SellerDocument(
            application=app,
            type=doc_type,
            file_name=file.name[:255],
            mime_type=file.content_type,
            size_bytes=file.size,
            version=(previous.version + 1) if previous else 1,
        )
        doc.file.save(file.name, file, save=True)
        if previous:
            previous.superseded_by = doc
            previous.save(update_fields=["superseded_by", "updated_at"])

        req = open_info_request(app)
        if req:
            now = timezone.now()
            req.items.filter(kind=SellerInfoRequestItem.InfoRequestKind.DOCUMENT, target=doc_type, resolved_at__isnull=True).update(resolved_at=now)
    return doc


# --------------------------------------------------------------------------- #
# Submit / resubmit / activate (seller)
# --------------------------------------------------------------------------- #


def _application_number() -> str:
    while True:
        candidate = f"ASK-SLR-{secrets.randbelow(10**6):06d}"
        if not SellerApplication.objects.filter(application_number=candidate).exists():
            return candidate


@transaction.atomic
def submit(app: SellerApplication, *, ip: str | None) -> SellerApplication:
    if app.status != Status.DRAFT:
        raise HttpError(409, "Application has already been submitted")
    states = step_states(app)
    incomplete = [s for s in states if not s["complete"]]
    if incomplete:
        parts = "; ".join(
            f"{s['name']}: " + ", ".join(s["missing_fields"] + [f"document:{d}" for d in s["missing_documents"]])
            for s in incomplete
        )
        raise HttpError(422, f"Application is incomplete — {parts}")
    now = timezone.now()
    if not app.application_number:
        app.application_number = _application_number()
    app.info_accuracy_confirmed_at = now
    app.terms_accepted_at = now
    app.submitted_at = now
    app.current_step = 5
    transition(app, Status.SUBMITTED, actor=app.user)
    app.save()
    TermsAcceptance.objects.create(user=app.user, document=TermsAcceptance.TermsDocument.SELLER_TERMS, ip=ip)
    return app


@transaction.atomic
def resubmit(app: SellerApplication) -> SellerApplication:
    if app.status != Status.MORE_INFO_REQUIRED:
        raise HttpError(409, "Application is not awaiting more information")
    req = open_info_request(app)
    if req:
        unresolved = [i for i in req.items.all() if i.resolved_at is None]
        if unresolved:
            raise HttpError(422, "Please complete every required update: " + "; ".join(i.instruction for i in unresolved))
        req.resolved_at = timezone.now()
        req.save(update_fields=["resolved_at", "updated_at"])
    app.info_accuracy_confirmed_at = timezone.now()
    app.reviewer_message = ""
    transition(app, Status.RESUBMITTED, actor=app.user)
    app.save()
    return app


def slug_for(name: str) -> str:
    base = slugify(name)[:120] or "store"
    return base


def slug_available(slug: str, *, exclude_store: Store | None = None) -> bool:
    qs = Store.objects.filter(slug=slug)
    if exclude_store:
        qs = qs.exclude(pk=exclude_store.pk)
    return not qs.exists()


def suggest_slug(base: str) -> str:
    if slug_available(base):
        return base
    for i in range(2, 1000):
        candidate = f"{base}-{i}"
        if slug_available(candidate):
            return candidate
    return f"{base}-{secrets.token_hex(3)}"


@transaction.atomic
def activate_store(app: SellerApplication, *, display_name: str | None, slug: str | None) -> Store:
    if app.status != Status.APPROVED:
        raise HttpError(409, "Application must be approved before the store can be activated")
    try:
        store = app.store
    except Store.DoesNotExist:  # approve() always creates one
        raise HttpError(500, "Approved application has no store")
    if display_name:
        store.display_name = display_name
    slug = slug or store.slug or slug_for(store.display_name)
    if not slug_available(slug, exclude_store=store):
        raise HttpError(409, f"Store URL '{slug}' is already taken")
    now = timezone.now()
    store.slug = slug
    store.status = Store.StoreStatus.ACTIVE
    store.seller_agreement_accepted_at = now
    store.info_reconfirmed_at = now
    store.activated_at = now
    store.save()
    app.activated_at = now
    app.store_display_name = store.display_name
    transition(app, Status.ACTIVE, actor=app.user)
    app.save()
    return store


# --------------------------------------------------------------------------- #
# Admin review
# --------------------------------------------------------------------------- #


def admin_get(application_id) -> SellerApplication:
    app = (
        SellerApplication.objects.select_related("bank", "user", "reviewer", "country__kyc_policy")
        .prefetch_related("categories", "directors", "documents", "info_requests__items", "status_events")
        .filter(pk=application_id)
        .first()
    )
    if app is None:
        raise HttpError(404, "Application not found")
    return app


@transaction.atomic
def start_review(app: SellerApplication, *, reviewer) -> SellerApplication:
    transition(app, Status.UNDER_REVIEW, actor=reviewer)
    app.reviewer = reviewer
    app.reviewed_at = timezone.now()
    app.save()
    return app


@transaction.atomic
def request_info(app: SellerApplication, *, reviewer, message: str, items: list[dict]) -> SellerApplication:
    if open_info_request(app):
        raise HttpError(409, "There is already an open information request")
    transition(app, Status.MORE_INFO_REQUIRED, actor=reviewer, note=message)
    req = SellerInfoRequest.objects.create(application=app, reviewer=reviewer, message=message)
    SellerInfoRequestItem.objects.bulk_create([
        SellerInfoRequestItem(request=req, sort_order=i, **item) for i, item in enumerate(items, start=1)
    ])
    docs = latest_documents(app)
    for item in items:
        if item["kind"] == "document" and item["target"] in docs:
            d = docs[item["target"]]
            d.status = SellerDocument.DocumentStatus.REPLACEMENT_REQUESTED
            d.reviewer_note = item["instruction"]
            d.save(update_fields=["status", "reviewer_note", "updated_at"])
    app.reviewer = reviewer
    app.reviewer_message = message
    app.reviewed_at = timezone.now()
    app.save()
    return app


@transaction.atomic
def approve(app: SellerApplication, *, reviewer, note: str = "") -> SellerApplication:
    transition(app, Status.APPROVED, actor=reviewer, note=note)
    now = timezone.now()
    app.reviewer = reviewer
    app.reviewed_at = now
    app.approved_at = now
    app.save()
    if not Store.objects.filter(application=app).exists():
        name = app.store_display_name or app.legal_business_name or f"{app.first_name} {app.last_name}".strip() or "Store"
        Store.objects.create(
            owner=app.user,
            application=app,
            country_id=app.country_id,  # the market is fixed from here on
            display_name=name,
            slug=suggest_slug(slug_for(name)),
            description=app.store_description,
        )
    return app


@transaction.atomic
def reject(app: SellerApplication, *, reviewer, reason: str) -> SellerApplication:
    transition(app, Status.REJECTED, actor=reviewer, note=reason)
    now = timezone.now()
    app.reviewer = reviewer
    app.reviewed_at = now
    app.rejected_at = now
    app.rejection_reason = reason
    app.save()
    return app


def review_document(doc: SellerDocument, *, status: str, note: str) -> SellerDocument:
    doc.status = status
    doc.reviewer_note = note
    doc.save(update_fields=["status", "reviewer_note", "updated_at"])
    return doc
