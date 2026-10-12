from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from ninja import Schema
from pydantic import Field


class NotificationOut(Schema):
    id: UUID
    category: Literal["orders", "payments", "inventory", "customers", "system"]
    kind: str
    title: str = Field(description="Rendered in the request's language")
    body: str
    link_type: str = Field(description="order | product | payout | customer; empty when there is nothing to open")
    link_id: str
    created_at: datetime
    group: Literal["today", "yesterday", "earlier"] = Field(description="The section heading, in the store's timezone")
    is_read: bool = Field(description="For the person asking: reading one does not clear it for the rest of the team")


class TabCountOut(Schema):
    total: int
    unread: int


class SummaryOut(Schema):
    unread: int = Field(description="The header badge")
    tabs: dict[str, TabCountOut] = Field(description="Only the tabs this person may see, plus `all`: 'Orders (5)', 'Payments (2)'…")


class MarkedOut(Schema):
    marked: int
