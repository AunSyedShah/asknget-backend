"""Request-path hygiene."""


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
