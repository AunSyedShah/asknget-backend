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
