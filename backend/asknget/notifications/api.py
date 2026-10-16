"""Notifications for the seller portal: a feed per person, with read state per person.

Open to any member of the store; what each sees is filtered by the sections they can view
(orders -> Orders, payments -> Transactions, inventory -> Inventory, customers -> Customers;
system messages go to everyone).
"""

from uuid import UUID

from ninja import Router
from ninja.pagination import paginate

from core.pagination import PagePagination
from core.schemas import ErrorOut
from portal.auth import StoreAuth

from . import services
from .schemas import MarkedOut, NotificationOut, NotificationSummaryOut

notifications_router = Router(tags=["seller: notifications"], auth=StoreAuth())


@notifications_router.get("", response=list[NotificationOut], summary="Notifications (tabs: orders, payments, inventory, customers, system, all)")
@paginate(PagePagination)
def notification_list(request, tab: str | None = None):
    known = services.templates()
    country = request.store.country
    rows = []
    for n in services.feed(request.store, request.auth, request.access, tab):
        title, body = services.render(n, known, country)
        rows.append({
            "id": n.pk, "category": n.category, "kind": n.kind, "title": title, "body": body, "link_type": n.link_type, "link_id": n.link_id,
            "created_at": n.created_at, "group": services.group_of(n), "is_read": n.is_read,
        })
    return rows


@notifications_router.get("/summary", response=NotificationSummaryOut, summary="Unread badge and the counts on each tab")
def notification_summary(request):
    return services.summary(request.store, request.auth, request.access)


@notifications_router.post("/read-all", response=MarkedOut, summary="'Mark All as Read' (optionally just one tab)")
def notification_read_all(request, tab: str | None = None):
    return {"marked": services.mark_all_read(request.store, request.auth, request.access, tab)}


@notifications_router.post("/{notification_id}/read", response={204: None, 404: ErrorOut}, summary="Mark one as read")
def notification_read(request, notification_id: UUID):
    services.mark_read(request.store, request.auth, request.access, notification_id)
    return 204, None
