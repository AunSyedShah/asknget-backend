"""One pagination style for every list endpoint.

    GET /products?page=2&per_page=12
    → {"items": [...], "total": 120, "page": 2, "per_page": 12, "pages": 10}

Matches the design's "Showing 1 – 12 of 120 results" + numbered pager.
"""

from typing import Any

from ninja import Field, Schema
from ninja.pagination import PaginationBase


class PagePagination(PaginationBase):
    default_per_page = 12  # design: 12 cards per page
    max_per_page = 100

    class Input(Schema):
        page: int = Field(1, ge=1)
        per_page: int = Field(12, ge=1, le=100)

    class Output(Schema):
        items: list[Any]
        total: int
        page: int
        per_page: int
        pages: int

    items_attribute = "items"

    def paginate_queryset(self, queryset, pagination: Input, **params):
        total = self._items_count(queryset)
        per_page = pagination.per_page
        start = (pagination.page - 1) * per_page
        return {
            "items": queryset[start : start + per_page],
            "total": total,
            "page": pagination.page,
            "per_page": per_page,
            "pages": max((total + per_page - 1) // per_page, 1),
        }


class PageOrLimitIn(Schema):
    page: int | None = Field(None, ge=1)
    per_page: int | None = Field(None, ge=1, le=100)
    limit: int | None = Field(None, ge=1, le=100, description="Older style: same as per_page")
    offset: int | None = Field(None, ge=0, description="Older style: skip this many")


class PageOrLimitOut(Schema):
    items: list[Any]
    total: int
    page: int
    per_page: int
    pages: int
    count: int = Field(description="Same as total; kept for clients written for limit/offset")


class PageOrLimitPagination(PaginationBase):
    """The standard `?page=&per_page=` list, which also still answers the older `?limit=&offset=`.

    Used by the one list that started life with limit/offset (the staff queue of seller applications). The response is the
    standard `{items, total, page, per_page, pages}` plus `count` (the same number as `total`), so a client written for either
    style keeps working. `page`/`per_page` win when both are sent; with neither, the first 100 are returned, as before."""

    default_per_page = 100
    max_per_page = 100

    Input = PageOrLimitIn
    Output = PageOrLimitOut

    items_attribute = "items"

    def paginate_queryset(self, queryset, pagination: Input, **params):
        total = self._items_count(queryset)
        if pagination.page is not None or pagination.per_page is not None:
            per_page = pagination.per_page or self.default_per_page
            page = pagination.page or 1
            start = (page - 1) * per_page
        else:
            per_page = pagination.limit or self.default_per_page
            start = pagination.offset or 0
            page = start // per_page + 1
        return {
            "items": queryset[start : start + per_page],
            "total": total, "count": total, "page": page, "per_page": per_page,
            "pages": max((total + per_page - 1) // per_page, 1),
        }
