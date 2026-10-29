"""Buyer, seller and staff views of a conversation."""

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from ninja import Field, Schema

from core.validators import NonEmptyStr

from .rules import MAX_BODY

SellerTabLit = Literal["all", "unread", "open", "closed"]


class ChatMessageOut(Schema):
    id: UUID
    sender_role: str = Field(description="customer | seller | system")
    sender_name: str
    body: str
    flagged: bool = Field(description="Looks like off-platform contact details; staff review it")
    created_at: datetime


class MessageIn(Schema):
    body: NonEmptyStr = Field(max_length=MAX_BODY)


class ChatStoreRefOut(Schema):
    id: UUID
    name: str
    slug: str


class CustomerRefOut(Schema):
    id: str
    name: str
    email: str


class CustomerConversationOut(Schema):
    """Buyer's list row."""

    id: UUID
    store: ChatStoreRefOut
    order_number: Optional[str] = None
    subject: str
    last_message_at: Optional[datetime]
    last_message_preview: str
    unread: int
    closed: bool
    seller_can_reply: bool = Field(description="False when the seller's plan has no inbox — tell the buyer before they wait")


class CustomerThreadOut(CustomerConversationOut):
    messages: list[ChatMessageOut]


class SellerConversationOut(Schema):
    """Seller inbox row."""

    id: UUID
    customer: CustomerRefOut
    order_number: Optional[str] = None
    subject: str
    last_message_at: Optional[datetime]
    last_message_preview: str
    unread: int
    closed: bool


class SellerThreadOut(SellerConversationOut):
    messages: list[ChatMessageOut]


class ConversationOpenIn(Schema):
    """'Message seller' — by store id or slug; `order_id` adds context."""

    store_id: Optional[UUID] = None
    store_slug: Optional[str] = Field(None, max_length=140)
    order_id: Optional[UUID] = None
    subject: str = Field("", max_length=140)
    body: NonEmptyStr = Field(max_length=MAX_BODY)


class CustomerSummaryOut(Schema):
    unread: int
    conversations: int


class SellerSummaryOut(Schema):
    """Header badge. Readable on every plan so a free seller knows messages are waiting."""

    unread: int
    conversations: int
    waiting_customers: int
    locked: bool = Field(description="True when the plan has no inbox: counts only, no content")
    locked_reason: str


class CloseIn(Schema):
    closed: bool = True


class FlaggedMessageOut(Schema):
    id: UUID
    conversation_id: UUID
    store: ChatStoreRefOut
    customer: CustomerRefOut
    sender_role: str
    body: str
    flag_reason: str
    created_at: datetime
