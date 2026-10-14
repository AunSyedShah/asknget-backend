"""Category requests: the seller's side (/seller/category-requests) and staff's (/admin/category-requests)."""

from typing import Literal
from uuid import UUID

from ninja import File, Form, Query, Router
from ninja.files import UploadedFile
from ninja.pagination import paginate

from core.auth import StaffAuth
from core.pagination import PagePagination
from core.schemas import ErrorOut
from portal.auth import StoreAuth
from team import services as team

from . import category_requests as service
from .models import CategoryRequest
from .schemas import (
    CategoryApproveIn,
    CategoryExistsOut,
    CategoryRejectIn,
    CategoryRequestAdminOut,
    CategoryRequestIn,
    CategoryRequestOut,
)

seller_category_requests_router = Router(tags=["seller: category requests"], auth=StoreAuth(section="products"))
admin_category_requests_router = Router(tags=["admin · category requests"], auth=StaffAuth())

StatusLit = Literal["pending", "approved", "rejected", "withdrawn"]


def _exists(exc: service.CategoryExists):
    c = exc.category
    return 409, {"detail": "category_exists", "category": {"id": c.id, "name": c.name, "slug": c.slug}, "is_active": c.is_active}


# --------------------------------------------------------------------------- #
# Seller
# --------------------------------------------------------------------------- #


@seller_category_requests_router.post(
    "", response={201: CategoryRequestOut, 409: CategoryExistsOut | ErrorOut, 422: ErrorOut, 429: ErrorOut},
    summary="Ask for a category (Category Request Popup). 409 category_exists names the one to use instead",
)
def request_category(request, payload: Form[CategoryRequestIn], document: UploadedFile = File(None)):
    try:
        req = service.submit(request.store, request.auth, name=payload.name, reason=payload.reason, parent_id=payload.parent_id, document=document)
    except service.CategoryExists as exc:
        return _exists(exc)
    team.log(request.store, request.auth, "request_category", f"Asked for the category {req.name}", req)
    return 201, service.out(req)


@seller_category_requests_router.get("", response=list[CategoryRequestOut], summary="My category requests")
@paginate(PagePagination)
def my_category_requests(request, status: StatusLit | None = Query(None)):
    qs = service.requests_qs().filter(store=request.store)
    return [service.out(r) for r in (qs.filter(status=status) if status else qs)]


@seller_category_requests_router.get("/{request_id}", response={200: CategoryRequestOut, 404: ErrorOut})
def my_category_request(request, request_id: UUID):
    req = service.requests_qs().filter(store=request.store, pk=request_id).first()
    if req is None:
        return 404, {"detail": "Category request not found"}
    return service.out(req)


@seller_category_requests_router.delete("/{request_id}", response={200: CategoryRequestOut, 404: ErrorOut, 409: ErrorOut},
                                        summary="Withdraw a request still waiting for review")
def withdraw_category_request(request, request_id: UUID):
    req = service.withdraw(request.store, request_id)
    team.log(request.store, request.auth, "withdraw_category_request", f"Withdrew the request for {req.name}", req)
    return service.out(service.requests_qs().get(pk=req.pk))


# --------------------------------------------------------------------------- #
# Staff
# --------------------------------------------------------------------------- #


@admin_category_requests_router.get("", response=list[CategoryRequestAdminOut], summary="Category requests (default: waiting for review, oldest first)")
@paginate(PagePagination)
def category_requests_queue(request, status: StatusLit = Query("pending"), q: str | None = Query(None, description="Name or store")):
    qs = service.requests_qs().filter(status=status)
    if status == CategoryRequest.Status.PENDING:
        qs = qs.order_by("created_at")
    if q:
        from django.db.models import Q

        qs = qs.filter(Q(name__icontains=q) | Q(store__display_name__icontains=q))
    return [service.out(r, staff=True) for r in qs]


@admin_category_requests_router.get("/{request_id}", response={200: CategoryRequestAdminOut, 404: ErrorOut})
def category_request_detail(request, request_id: UUID):
    req = service.requests_qs().filter(pk=request_id).first()
    if req is None:
        return 404, {"detail": "Category request not found"}
    return service.out(req, staff=True)


@admin_category_requests_router.post(
    "/{request_id}/approve", response={200: CategoryRequestAdminOut, 404: ErrorOut, 409: CategoryExistsOut | ErrorOut, 422: ErrorOut},
    summary="Approve: create the category (optionally renamed or moved), or give the seller an existing one (use_category_id)",
)
def approve_category_request(request, request_id: UUID, payload: CategoryApproveIn):
    given = payload.dict(exclude_unset=True)
    try:
        req = service.approve(
            request_id, request.auth, name=payload.name, use_category_id=payload.use_category_id, note=payload.note,
            **({"parent_id": payload.parent_id} if "parent_id" in given else {}),
        )
    except service.CategoryExists as exc:
        return _exists(exc)
    return service.out(service.requests_qs().get(pk=req.pk), staff=True)


@admin_category_requests_router.post("/{request_id}/reject", response={200: CategoryRequestAdminOut, 404: ErrorOut, 409: ErrorOut},
                                     summary="Reject, with a reason the seller sees")
def reject_category_request(request, request_id: UUID, payload: CategoryRejectIn):
    req = service.reject(request_id, request.auth, reason=payload.reason)
    return service.out(service.requests_qs().get(pk=req.pk), staff=True)
