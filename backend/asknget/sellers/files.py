"""Seller documents (identity and company papers) are never served from /media/: only through a signed, expiring
link that the API hands to the applicant and to staff. Anyone holding a link can open it until it expires, the same
model as a cloud "pre-signed URL", so it works in an <img> or <a> without an Authorization header."""

from uuid import UUID

from django.conf import settings
from django.core import signing
from django.http import FileResponse
from ninja import Router
from ninja.errors import HttpError

from .models import SellerDocument

SALT = "seller-document"
files_router = Router(tags=["files"])


def document_url(doc: SellerDocument) -> str:
    return f"/api/v1/files/seller-documents/{doc.pk}?t={signing.dumps(str(doc.pk), salt=SALT)}"


@files_router.get("/seller-documents/{doc_id}", summary="A seller document, through a signed link from the API (expires)")
def seller_document_file(request, doc_id: UUID, t: str):
    try:
        signed_for = signing.loads(t, salt=SALT, max_age=settings.SIGNED_FILE_TTL_SECONDS)
    except signing.BadSignature:              # also SignatureExpired
        raise HttpError(404, "This link is invalid or has expired") from None
    doc = SellerDocument.objects.filter(pk=doc_id).first() if signed_for == str(doc_id) else None
    if doc is None or not doc.file:
        raise HttpError(404, "This link is invalid or has expired")
    response = FileResponse(doc.file.open("rb"), content_type=doc.mime_type, filename=doc.file_name, as_attachment=False)
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    return response
