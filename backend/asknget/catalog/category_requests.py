"""Sellers ask for a category; staff decide (Category Request Popup).

A seller never creates a category. They send a name, an optional parent, why they need it and an optional supporting
image; staff approve it (creating the category, possibly renamed or moved, or pointing at one that already exists) or
reject it with a reason. Either way the seller is told in the portal's notifications. The supporting image is private:
it is served only through a signed, expiring link, like seller documents.
"""

from uuid import UUID

from django.conf import settings
from django.core import signing
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.http import FileResponse
from django.utils import timezone
from django.utils.text import slugify
from ninja import Router
from ninja.errors import HttpError

from core.uploads import safe_image

from .models import Category, CategoryRequest

MAX_PENDING_PER_STORE = 5
DOCUMENT_MAX_BYTES = 1 * 1024 * 1024        # design: "PNG, JPG upto 1-MB"
SALT = "category-request-document"


class CategoryExists(Exception):
    """The name is already a category: the seller should use it, staff should point the request at it."""

    def __init__(self, category: Category):
        super().__init__(category.name)
        self.category = category


def existing_category(name: str) -> Category | None:
    """A category with this name (any case) or this slug anywhere in the tree; slugs are unique across the tree."""
    return (Category.objects.filter(name__iexact=name.strip()).first()
            or Category.objects.filter(slug=slugify(name)).first())


def _parent(parent_id) -> Category | None:
    if parent_id is None:
        return None
    parent = Category.objects.filter(pk=parent_id).first()
    if parent is None:
        raise HttpError(422, "unknown_parent")
    if parent.parent_id is not None:
        raise HttpError(422, "parent_must_be_top_level")      # the tree is two levels: category, subcategory
    return parent


# --------------------------------------------------------------------------- #
# Seller
# --------------------------------------------------------------------------- #


def submit(store, user, *, name: str, reason: str, parent_id=None, document=None) -> CategoryRequest:
    name, reason = name.strip(), reason.strip()
    parent = _parent(parent_id)
    found = existing_category(name)
    if found is not None:
        raise CategoryExists(found)
    pending = CategoryRequest.objects.filter(store=store, status=CategoryRequest.Status.PENDING)
    if pending.filter(name__iexact=name).exists():
        raise HttpError(409, "already_requested")
    if pending.count() >= MAX_PENDING_PER_STORE:
        raise HttpError(429, f"You can have at most {MAX_PENDING_PER_STORE} category requests waiting for review")
    stored = safe_image(document, max_bytes=DOCUMENT_MAX_BYTES, formats=("JPEG", "PNG"), label="The supporting file") if document else None
    try:
        with transaction.atomic():
            return CategoryRequest.objects.create(
                store=store, requested_by=user, parent=parent, name=name, reason=reason,
                document=stored, document_name=(document.name or "")[:255] if document else "",
            )
    except IntegrityError:                                    # two submissions racing for the same name
        raise HttpError(409, "already_requested") from None


def withdraw(store, request_id) -> CategoryRequest:
    with transaction.atomic():
        req = CategoryRequest.objects.select_for_update().filter(pk=request_id, store=store).first()
        if req is None:
            raise HttpError(404, "Category request not found")
        if req.status != CategoryRequest.Status.PENDING:
            raise HttpError(409, "not_pending")
        req.status = CategoryRequest.Status.WITHDRAWN
        req.save(update_fields=["status", "updated_at"])
    return req


# --------------------------------------------------------------------------- #
# Staff
# --------------------------------------------------------------------------- #

_KEEP = object()


def _locked_pending(request_id) -> CategoryRequest:
    req = CategoryRequest.objects.select_for_update(of=("self",)).select_related("store").filter(pk=request_id).first()
    if req is None:
        raise HttpError(404, "Category request not found")
    if req.status != CategoryRequest.Status.PENDING:
        raise HttpError(409, "not_pending")
    return req


def approve(request_id, staff, *, name: str | None = None, parent_id=_KEEP, use_category_id=None, note: str = "") -> CategoryRequest:
    """Create the category (staff may rename it or change its parent), or point the request at an existing one."""
    with transaction.atomic():
        req = _locked_pending(request_id)
        if use_category_id is not None:
            category = Category.objects.filter(pk=use_category_id).first()
            if category is None:
                raise HttpError(422, "unknown_category")
            created = False
        else:
            final_name = (name or req.name).strip()
            parent = req.parent if parent_id is _KEEP else _parent(parent_id)
            found = existing_category(final_name)
            if found is not None:
                raise CategoryExists(found)
            last = Category.objects.filter(parent=parent).aggregate(n=Max("sort_order"))["n"]
            category = Category.objects.create(
                name=final_name, slug=slugify(final_name), parent=parent, is_active=True, sort_order=(last or 0) + 1,
            )
            created = True
        req.status = CategoryRequest.Status.APPROVED
        req.category, req.created_category = category, created
        req.reviewed_by, req.reviewed_at, req.review_note = staff, timezone.now(), note.strip()
        req.save(update_fields=["status", "category", "created_category", "reviewed_by", "reviewed_at", "review_note", "updated_at"])
        transaction.on_commit(lambda: _tell(req))
    return req


def reject(request_id, staff, *, reason: str) -> CategoryRequest:
    with transaction.atomic():
        req = _locked_pending(request_id)
        req.status = CategoryRequest.Status.REJECTED
        req.reviewed_by, req.reviewed_at, req.review_note = staff, timezone.now(), reason.strip()
        req.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_note", "updated_at"])
        transaction.on_commit(lambda: _tell(req))
    return req


def _tell(req: CategoryRequest) -> None:
    from notifications import services as notifications      # local: notifications reads catalog

    if req.status == CategoryRequest.Status.APPROVED:
        notifications.notify(req.store_id, "category_request_approved", {"name": req.name, "category": req.category.name},
                             link=("category", req.category_id))
    else:
        notifications.notify(req.store_id, "category_request_rejected", {"name": req.name, "reason": req.review_note},
                             link=("category_request", req.pk))


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #


def requests_qs():
    return CategoryRequest.objects.select_related("store", "parent", "category", "requested_by").order_by("-created_at")


def document_url(req: CategoryRequest) -> str | None:
    if not req.document:
        return None
    return f"/api/v1/files/category-requests/{req.pk}?t={signing.dumps(str(req.pk), salt=SALT)}"


def out(req: CategoryRequest, *, staff: bool = False) -> dict:
    body = {
        "id": req.id, "name": req.name, "reason": req.reason, "status": req.status,
        "parent": {"id": req.parent.id, "name": req.parent.name} if req.parent_id else None,
        "category": {"id": req.category.id, "name": req.category.name, "slug": req.category.slug} if req.category_id else None,
        "created_category": req.created_category, "review_note": req.review_note,
        "document_url": document_url(req), "document_name": req.document_name,
        "created_at": req.created_at, "reviewed_at": req.reviewed_at,
    }
    if staff:
        body["store"] = {"id": req.store.id, "name": req.store.display_name}
        body["requested_by"] = req.requested_by.email if req.requested_by_id else ""
        body["existing_category"] = _match(req) if req.status == CategoryRequest.Status.PENDING else None
    return body


def _match(req: CategoryRequest):
    found = existing_category(req.name)
    return {"id": found.id, "name": found.name, "slug": found.slug} if found else None


# --------------------------------------------------------------------------- #
# The private supporting image
# --------------------------------------------------------------------------- #

category_files_router = Router(tags=["files"])


@category_files_router.get("/category-requests/{request_id}", summary="A category request's supporting image, through a signed link (expires)")
def category_request_file(request, request_id: UUID, t: str):
    try:
        signed_for = signing.loads(t, salt=SALT, max_age=settings.SIGNED_FILE_TTL_SECONDS)
    except signing.BadSignature:              # also SignatureExpired
        raise HttpError(404, "This link is invalid or has expired") from None
    req = CategoryRequest.objects.filter(pk=request_id).first() if signed_for == str(request_id) else None
    if req is None or not req.document:
        raise HttpError(404, "This link is invalid or has expired")
    content_type = "image/png" if req.document.name.lower().endswith(".png") else "image/jpeg"
    response = FileResponse(req.document.open("rb"), content_type=content_type, as_attachment=False)
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    return response

