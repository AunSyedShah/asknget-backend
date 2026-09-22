"""Chat routers.

  /me/conversations    buyer side (signed in) — always available
  /me/chat/summary     header badge
  /seller/chat         seller inbox — premium (`chat_inbox`); `summary` is open to every
                       plan so a free seller sees that customers are waiting
  /admin/chat          staff: messages flagged as off-platform contact details

Polling, not websockets: `GET …/messages?after=<id of the newest message you hold>`
returns only what is new (docs/sprint-5-plan.md §1).
"""

from typing import Optional
from uuid import UUID

from ninja import Query, Router
from ninja.errors import HttpError
from ninja.pagination import paginate

from core.auth import JWTAuth, StaffAuth
from core.pagination import PagePagination
from core.schemas import ErrorOut, MessageOut as DetailOut
from portal import entitlements
from portal.auth import StoreAuth

from . import services
from .models import Conversation, Message
from .schemas import (
    CloseIn,
    ConversationOpenIn,
    CustomerConversationOut,
    CustomerSummaryOut,
    CustomerThreadOut,
    FlaggedMessageOut,
    MessageIn,
    MessageOut,
    SellerConversationOut,
    SellerSummaryOut,
    SellerTabLit,
    SellerThreadOut,
)

customer_chat_router = Router(tags=["me: chat"], auth=JWTAuth())
seller_chat_router = Router(tags=["seller: chat"], auth=StoreAuth())
admin_chat_router = Router(tags=["admin: chat"], auth=StaffAuth())

Role = Message.SenderRole


# --------------------------------------------------------------------------- #
# Buyer
# --------------------------------------------------------------------------- #


@customer_chat_router.get("/conversations", response=list[CustomerConversationOut], summary="My conversations with sellers")
@paginate(PagePagination)
def my_conversations(request):
    return [services.customer_row(c) for c in services.conversations_for_customer(request.auth)]


@customer_chat_router.post("/conversations", response={201: CustomerThreadOut, 404: ErrorOut, 422: ErrorOut, 429: ErrorOut},
                           summary="'Message seller' — opens the thread (or reuses it) and sends the first message")
def open_conversation(request, payload: ConversationOpenIn):
    if not (payload.store_id or payload.store_slug):
        raise HttpError(422, "Provide store_id or store_slug")
    convo = services.open_for_customer(
        request.auth, store_id=payload.store_id, store_slug=payload.store_slug,
        order_id=payload.order_id, body=payload.body, subject=payload.subject,
    )
    return 201, services.thread_out(convo, for_role=Role.CUSTOMER)


@customer_chat_router.get("/conversations/{conversation_id}", response={200: CustomerThreadOut, 404: ErrorOut})
def my_conversation(request, conversation_id: UUID, after: Optional[str] = Query(None, description="Id of the newest message you already hold"),
                    limit: int = Query(50, ge=1, le=200)):
    convo = services.get_for_customer(request.auth, conversation_id)
    return services.thread_out(convo, for_role=Role.CUSTOMER, after=after, limit=limit)


@customer_chat_router.get("/conversations/{conversation_id}/messages", response={200: list[MessageOut], 404: ErrorOut, 422: ErrorOut},
                          summary="Poll for new messages")
def my_messages(request, conversation_id: UUID, after: Optional[str] = Query(None), limit: int = Query(50, ge=1, le=200)):
    convo = services.get_for_customer(request.auth, conversation_id)
    return [services.message_out(m) for m in services.messages(convo, after=after, limit=limit)]


@customer_chat_router.post("/conversations/{conversation_id}/messages", response={201: MessageOut, 404: ErrorOut, 422: ErrorOut, 429: ErrorOut})
def my_send(request, conversation_id: UUID, payload: MessageIn):
    convo = services.get_for_customer(request.auth, conversation_id)
    return 201, services.message_out(services.send(convo, role=Role.CUSTOMER, sender=request.auth, body=payload.body))


@customer_chat_router.post("/conversations/{conversation_id}/read", response={200: CustomerConversationOut, 404: ErrorOut})
def my_read(request, conversation_id: UUID):
    convo = services.get_for_customer(request.auth, conversation_id)
    return services.customer_row(services.mark_read(convo, role=Role.CUSTOMER))


@customer_chat_router.get("/chat/summary", response=CustomerSummaryOut, summary="Unread badge")
def my_summary(request):
    return services.summary_for_customer(request.auth)


# --------------------------------------------------------------------------- #
# Seller (premium)
# --------------------------------------------------------------------------- #


@seller_chat_router.get("/summary", response=SellerSummaryOut,
                        summary="Unread badge — allowed on every plan; free sellers get counts only")
def chat_summary(request):
    return services.summary_for_store(request.store)


@seller_chat_router.get("", response={200: list[SellerConversationOut], 403: ErrorOut}, summary="Inbox (premium)")
@paginate(PagePagination)
def inbox(request, tab: SellerTabLit = "all", q: Optional[str] = Query(None, max_length=80)):
    entitlements.require(request.store, entitlements.CHAT_INBOX)
    return [services.seller_row(c) for c in services.conversations_for_store(request.store, tab=tab, q=q)]


@seller_chat_router.get("/{conversation_id}", response={200: SellerThreadOut, 403: ErrorOut, 404: ErrorOut})
def seller_conversation(request, conversation_id: UUID, after: Optional[str] = Query(None), limit: int = Query(50, ge=1, le=200)):
    entitlements.require(request.store, entitlements.CHAT_INBOX)
    convo = services.get_for_store(request.store, conversation_id)
    return services.thread_out(convo, for_role=Role.SELLER, after=after, limit=limit)


@seller_chat_router.get("/{conversation_id}/messages", response={200: list[MessageOut], 403: ErrorOut, 404: ErrorOut, 422: ErrorOut})
def seller_messages(request, conversation_id: UUID, after: Optional[str] = Query(None), limit: int = Query(50, ge=1, le=200)):
    entitlements.require(request.store, entitlements.CHAT_INBOX)
    convo = services.get_for_store(request.store, conversation_id)
    return [services.message_out(m) for m in services.messages(convo, after=after, limit=limit)]


@seller_chat_router.post("/{conversation_id}/messages", response={201: MessageOut, 403: ErrorOut, 404: ErrorOut, 422: ErrorOut, 429: ErrorOut})
def seller_send(request, conversation_id: UUID, payload: MessageIn):
    entitlements.require(request.store, entitlements.CHAT_INBOX)
    convo = services.get_for_store(request.store, conversation_id)
    return 201, services.message_out(services.send(convo, role=Role.SELLER, sender=request.auth, body=payload.body))


@seller_chat_router.post("/{conversation_id}/read", response={200: SellerConversationOut, 403: ErrorOut, 404: ErrorOut})
def seller_read(request, conversation_id: UUID):
    entitlements.require(request.store, entitlements.CHAT_INBOX)
    convo = services.get_for_store(request.store, conversation_id)
    return services.seller_row(services.mark_read(convo, role=Role.SELLER))


@seller_chat_router.post("/{conversation_id}/close", response={200: SellerConversationOut, 403: ErrorOut, 404: ErrorOut},
                         summary="Mark resolved; a new customer message reopens it")
def seller_close(request, conversation_id: UUID, payload: CloseIn):
    entitlements.require(request.store, entitlements.CHAT_INBOX)
    convo = services.get_for_store(request.store, conversation_id)
    return services.seller_row(services.set_closed(convo, closed=payload.closed))


# --------------------------------------------------------------------------- #
# Staff
# --------------------------------------------------------------------------- #


@admin_chat_router.get("/flagged", response=list[FlaggedMessageOut], summary="Messages that look like off-platform contact details")
@paginate(PagePagination)
def flagged(request, store_id: Optional[UUID] = Query(None)):
    qs = (
        Message.objects.filter(flagged_at__isnull=False)
        .select_related("conversation__store", "conversation__customer")
        .order_by("-flagged_at")
    )
    if store_id:
        qs = qs.filter(conversation__store_id=store_id)
    return [
        {
            "id": m.id, "conversation_id": m.conversation_id,
            "store": {"id": m.conversation.store_id, "name": m.conversation.store.display_name, "slug": m.conversation.store.slug},
            "customer": {"id": str(m.conversation.customer_id), "name": m.conversation.customer.full_name or m.conversation.customer.email,
                         "email": m.conversation.customer.email},
            "sender_role": m.sender_role, "body": m.body, "flag_reason": m.flag_reason, "created_at": m.created_at,
        }
        for m in qs
    ]


__all__ = ["customer_chat_router", "seller_chat_router", "admin_chat_router", "Conversation", "DetailOut"]
