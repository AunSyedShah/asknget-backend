from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.http import Http404
from django.urls import path, re_path

from core.storage import PRIVATE_PREFIXES, media_from_db

from .api import api

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", api.urls),
]

if settings.FILE_STORAGE == "database":
    # No disk to serve from (Vercel): public files come out of the database. Private papers are refused inside the view.
    urlpatterns += [re_path(rf"^{settings.MEDIA_URL.lstrip('/')}(?P<name>.+)$", media_from_db)]
elif settings.FILE_STORAGE == "filesystem" and settings.SERVE_MEDIA:
    def _private(request, rest=""):
        raise Http404          # seller documents only through signed links (sellers.files), as nginx does in production

    private = "|".join(p.rstrip("/") for p in PRIVATE_PREFIXES)
    urlpatterns += [re_path(rf"^{settings.MEDIA_URL.lstrip('/')}({private})/(?P<rest>.*)$", _private)]
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
# FILE_STORAGE=s3: files are served by the bucket (or CloudFront); there is nothing to route here.
