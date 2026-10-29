"""What the generated OpenAPI document says about things every call shares, so a client generator gets them right.

django-ninja describes each endpoint's own parameters, but not the request headers the whole API understands, and it
declares a header written as a Python name (`x_cart_token`) literally with underscores. A generated client then sends
`x_cart_token`, which servers and proxies routinely drop. This post-processes the finished document (documentation only,
no behaviour changes) to:

* declare `X-Region`, `X-Language`, `X-Timezone` on every operation, `X-Store` on seller operations and `X-Cart-Token`
  on the cart and checkout operations, once each in `components.parameters`, referenced from the operations;
* give any other header parameter its real hyphenated name;
* add a `default` error response with the shape every error really has (`{"detail": "...", "errors": [...]}`) to every
  operation that does not declare one, so clients can type errors once.

Idempotent: running it twice gives the same document.
"""

from ninja import NinjaAPI

PARAMETERS = {
    "X-Region": ("Market (country code, e.g. ZA or PK). Unknown or inactive is 400 unknown_region. Without it the market comes from the host's subdomain, then the default country. "
                 "Send it on every call: a frontend behind a proxy never has a country subdomain."),
    "X-Language": ("The user's saved language (a code from GET /regions, e.g. en or ur). Otherwise Accept-Language, then the market's default."),
    "X-Timezone": "IANA timezone, e.g. Asia/Karachi. Otherwise the market's timezone.",
    "X-Store": ("Seller calls only: which store, when the signed-in person belongs to several (own store first, else the first joined). "
                "A store that is not theirs is 403 no_active_store."),
    "X-Cart-Token": ("Guest carts: the cart_token returned by the first POST /cart/items; send it back on every cart and checkout call. "
                     "Members can omit it; signing in merges the guest cart into the account cart."),
}
EVERY_OPERATION = ("X-Region", "X-Language", "X-Timezone")
ERROR_SCHEMA = "ErrorResponse"
HTTP_METHODS = ("get", "post", "put", "patch", "delete", "options", "head")


def _canonical(name: str) -> str:
    """x_cart_token / x-cart-token -> X-Cart-Token"""
    return "-".join(part.capitalize() for part in name.replace("_", "-").split("-"))


def _component_key(header: str) -> str:
    return header.replace("-", "")                  # XRegion, XCartToken


def document_headers_and_errors(schema: dict, prefix: str = "/api/v1") -> dict:
    components = schema.setdefault("components", {})
    parameters = components.setdefault("parameters", {})
    for header, description in PARAMETERS.items():
        parameters[_component_key(header)] = {
            "name": header, "in": "header", "required": False, "description": description,
            "schema": {"type": "string", "title": header},
        }
    components.setdefault("schemas", {})[ERROR_SCHEMA] = {
        "title": ERROR_SCHEMA, "type": "object", "required": ["detail"],
        "description": "The body of every error. 422 validation errors also carry errors[] so a form can highlight the field.",
        "properties": {
            "detail": {"type": "string", "description": "One human-readable message, or a machine code such as cart_region_mismatch"},
            "errors": {"type": "array", "description": "Only on 422",
                       "items": {"type": "object", "required": ["message"],
                                 "properties": {"field": {"type": ["string", "null"], "description": "e.g. address.city"}, "message": {"type": "string"}}}},
        },
    }
    managed = {header.lower() for header in PARAMETERS}

    for path, item in schema.get("paths", {}).items():
        local = path[len(prefix):] if path.startswith(prefix) else path
        wanted = list(EVERY_OPERATION)
        if local.startswith("/seller/"):
            wanted.append("X-Store")
        if local.startswith("/cart") or local.startswith("/checkout"):
            wanted.append("X-Cart-Token")
        for method in HTTP_METHODS:
            operation = item.get(method)
            if operation is None:
                continue
            kept = []
            for param in operation.get("parameters", []):
                if param.get("in") == "header" and "name" in param:
                    param = {**param, "name": _canonical(param["name"])}
                    if param["name"].lower() in managed:
                        continue                                    # replaced by the shared declaration below
                kept.append(param)
            operation["parameters"] = kept + [{"$ref": f"#/components/parameters/{_component_key(h)}"} for h in wanted]
            operation.setdefault("responses", {}).setdefault("default", {
                "description": "Error: the body is always {\"detail\": \"...\"} (422 adds errors[])",
                "content": {"application/json": {"schema": {"$ref": f"#/components/schemas/{ERROR_SCHEMA}"}}},
            })
    return schema


class DocumentedAPI(NinjaAPI):
    """NinjaAPI whose OpenAPI document also declares the shared headers and the error body."""

    def get_openapi_schema(self, **kwargs):
        return document_headers_and_errors(super().get_openapi_schema(**kwargs))
