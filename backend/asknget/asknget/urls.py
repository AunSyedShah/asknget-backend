from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.http import Http404
from django.urls import path, re_path

from .api import api

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", api.urls),
]

if settings.SERVE_MEDIA:
    def _private(request, rest=""):
        raise Http404          # seller documents only through signed links (sellers.files), as nginx does in production

    urlpatterns += [re_path(rf"^{settings.MEDIA_URL.lstrip('/')}seller_documents/(?P<rest>.*)$", _private)]
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
