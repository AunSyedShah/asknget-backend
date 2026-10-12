"""Request-path hygiene, and refusing to serve a deployment that is not configured."""

from django.conf import settings
from django.http import JsonResponse


class CollapseLeadingSlashesMiddleware:
    """Serve `//api/v1/x` as `/api/v1/x`.

    A frontend that joins a base URL ending in `/` to a path starting with `/` sends
    `http://host//api/v1/x`. Django strips one slash and finds nothing, so the client
    gets a 404 (`APPEND_SLASH` does not help; it only ever appends a trailing slash). It
    has to run before CORS and the region middleware, which both match on `/api/`.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path_info
        if path.startswith("//"):
            clean = "/" + path.lstrip("/")
            request.path = request.path[: len(request.path) - len(path)] + clean
            request.path_info = clean
        return self.get_response(request)


class MissingConfigMiddleware:
    """Installed only when a Vercel deployment lacks a required setting (`settings.MISSING_CONFIG`).

    The build succeeds so the deployment exists, but nothing is served on a development default (a database that
    loses writes, a secret key that is public in the repository): every request gets 503 with the names (never the
    values) of what to set in Vercel > Settings > Environment Variables, then a redeploy.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return JsonResponse(
            {"detail": "server_not_configured", "missing": list(settings.MISSING_CONFIG),
             "hint": "Set these environment variables for this Vercel environment, then redeploy."},
            status=503,
        )
