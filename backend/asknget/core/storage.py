"""Where uploaded files live, chosen by one setting (`FILE_STORAGE`) so the same code runs on a laptop, on Vercel and on AWS.

    filesystem   a folder (MEDIA_ROOT): local development and the Docker/nginx setup
    database     rows in the `core_storedfile` table: Vercel, whose disk is read-only. Served by `media_from_db` below.
    s3           an S3-compatible bucket (AWS S3, Cloudflare R2, MinIO) through django-storages: AWS, and Vercel when wanted

Every file field reads `.url` / `.open()`, so none of the code that handles uploads knows which one is in use. `STORAGES` is built
in `core.storage_config`; fields holding private papers (seller documents, category-request files) use `private_storage`, which is
the same backend as the default except on S3, where it can be a second, private bucket with expiring links.

A stored name that is already a full web address is returned as it is (`url()`): demo pictures are links, not uploads.
"""

import hashlib
import mimetypes
from datetime import timezone

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage, Storage, storages
from django.http import Http404, HttpResponse, HttpResponseNotModified
from django.utils.deconstruct import deconstructible
from django.utils.encoding import filepath_to_uri

# Never served by /media/: reached only through signed links from the API (sellers.files, catalog.category_requests), as nginx does.
PRIVATE_PREFIXES = ("seller_documents/", "category_requests/")

# What `media_from_db` may show in the browser as itself; anything else is sent as an opaque download.
INLINE_TYPES = ("image/", "video/", "application/pdf")


def private_storage():
    """The storage for private papers. A callable (not an instance) so migrations refer to it by name and it follows settings."""
    return storages["private"]


class UrlFriendlyMixin:
    def url(self, name):
        if name and name.startswith(("http://", "https://")):
            return name
        return super().url(name)


class UrlFriendlyStorage(UrlFriendlyMixin, FileSystemStorage):
    """The folder on disk (development, Docker)."""


@deconstructible
class DatabaseStorage(UrlFriendlyMixin, Storage):
    """Files as rows in Postgres/SQLite, for hosts with no writable disk (Vercel).

    Meant for demo and test data, and for production until a bucket is set up: a file is read whole into memory, so it is limited
    to `DB_STORAGE_MAX_BYTES`, and it grows the database. On Vercel the request itself is already limited (UPLOAD_CAP_BYTES)."""

    def _model(self):
        from core.models import StoredFile

        return StoredFile

    def _open(self, name, mode="rb"):
        row = self._model().objects.filter(name=name).first()
        if row is None:
            raise FileNotFoundError(name)
        return ContentFile(bytes(row.content), name=name)

    def _save(self, name, content):
        name = name.replace("\\", "/")                  # Storage.save has already refused ../ and absolute names
        data = b"".join(content.chunks()) if hasattr(content, "chunks") else content.read()
        if len(data) > settings.DB_STORAGE_MAX_BYTES:
            raise ValueError(f"{name}: {len(data)} bytes is over the {settings.DB_STORAGE_MAX_BYTES // (1024 * 1024)} MB limit for files kept in the database")
        content_type = getattr(content, "content_type", None) or mimetypes.guess_type(name)[0] or "application/octet-stream"
        self._model().objects.create(name=name, content=data, content_type=content_type, size=len(data), etag=hashlib.md5(data).hexdigest())
        return name

    def exists(self, name):
        return self._model().objects.filter(name=name).exists()

    def delete(self, name):
        self._model().objects.filter(name=name).delete()

    def size(self, name):
        row = self._model().objects.filter(name=name).values_list("size", flat=True).first()
        if row is None:
            raise FileNotFoundError(name)
        return row

    def get_created_time(self, name):
        created = self._model().objects.filter(name=name).values_list("created_at", flat=True).first()
        if created is None:
            raise FileNotFoundError(name)
        return created if created.tzinfo else created.replace(tzinfo=timezone.utc)

    get_modified_time = get_accessed_time = get_created_time

    def url(self, name):
        if name and name.startswith(("http://", "https://")):
            return name
        return f"{settings.MEDIA_URL}{filepath_to_uri(name)}"


def media_from_db(request, name):
    """`GET /media/<name>` when files live in the database. Public files only; private ones are refused (404)."""
    if name.startswith(PRIVATE_PREFIXES):
        raise Http404
    from core.models import StoredFile

    row = StoredFile.objects.filter(name=name).first()
    if row is None:
        raise Http404
    etag = f'"{row.etag}"'
    if request.headers.get("If-None-Match") == etag:
        response = HttpResponseNotModified()
    else:
        kind = row.content_type if row.content_type.startswith(INLINE_TYPES) else "application/octet-stream"
        response = HttpResponse(bytes(row.content), content_type=kind)
    response["ETag"] = etag
    response["Cache-Control"] = "public, max-age=86400"
    response["X-Content-Type-Options"] = "nosniff"
    return response

