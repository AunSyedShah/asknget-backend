"""The bell, for each audience: sellers (/seller/notifications), customers (/me/notifications), staff (/admin/notifications).

Sellers:

Open to any member of the store; what each sees is filtered by the sections they can view
(orders -> Orders, payments -> Transactions, inventory -> Inventory, customers -> Customers;
system messages go to everyone).
"""

from uuid import UUID

from ninja import Field, Router, Schema
from ninja.pagination import paginate

from core.auth import JWTAuth, StaffAuth
from core.pagination import PagePagination
from core.schemas import ErrorOut
from portal.auth import StoreAuth

from . import realtime, services
from .schemas import MarkedOut, NotificationOut, NotificationSummaryOut

notifications_router = Router(tags=["seller: notifications"], auth=StoreAuth())


@notifications_router.get("", response=list[NotificationOut], summary="Notifications (tabs: orders, payments, inventory, customers, returns, system, all)")
@paginate(PagePagination)
def notification_list(request, tab: str | None = None):
    return services.rows(services.feed(request.store, request.auth, request.access, tab), request.store.country)


@notifications_router.get("/summary", response=NotificationSummaryOut, summary="Unread badge and the counts on each tab")
def notification_summary(request):
    return services.summary(request.store, request.auth, request.access)


@notifications_router.post("/read-all", response=MarkedOut, summary="'Mark All as Read' (optionally just one tab)")
def notification_read_all(request, tab: str | None = None):
    marked = services.mark_all_read(request.store, request.auth, request.access, tab)
    realtime.read_changed(request.auth, "seller", store=request.store)
    return {"marked": marked}


@notifications_router.post("/{notification_id}/read", response={204: None, 404: ErrorOut}, summary="Mark one as read")
def notification_read(request, notification_id: UUID):
    services.mark_read(request.store, request.auth, request.access, notification_id)
    realtime.read_changed(request.auth, "seller", store=request.store)
    return 204, None


# --------------------------------------------------------------------------------------
# Customers: their own notifications (orders, returns)
# --------------------------------------------------------------------------------------

my_notifications_router = Router(tags=["me: notifications"], auth=JWTAuth())


@my_notifications_router.get("", response=list[NotificationOut], summary="My notifications (tabs: orders, returns, all)")
@paginate(PagePagination)
def my_notification_list(request, tab: str | None = None):
    return services.rows(services.customer_feed(request.auth, tab), request.country)


@my_notifications_router.get("/summary", response=NotificationSummaryOut, summary="The bell's badge and the counts on each tab")
def my_notification_summary(request):
    return services.summary_of(services.customer_feed(request.auth), services.CUSTOMER_CATEGORIES)


@my_notifications_router.post("/read-all", response=MarkedOut, summary="Mark all as read (optionally just one tab)")
def my_notification_read_all(request, tab: str | None = None):
    marked = services.mark_all_read_in(services.customer_feed(request.auth, tab), request.auth)
    realtime.read_changed(request.auth, "customer")
    return {"marked": marked}


@my_notifications_router.post("/{notification_id}/read", response={204: None, 404: ErrorOut}, summary="Mark one as read")
def my_notification_read(request, notification_id: UUID):
    services.mark_read_in(services.customer_feed(request.auth), request.auth, notification_id)
    realtime.read_changed(request.auth, "customer")
    return 204, None


# --------------------------------------------------------------------------------------
# Staff: what their permissions and markets let them see (any staff member; no permission needed to read one's own bell)
# --------------------------------------------------------------------------------------

admin_notifications_router = Router(tags=["admin: notifications"], auth=StaffAuth())


@admin_notifications_router.get("", auth=StaffAuth(None), response=list[NotificationOut], summary="Staff notifications (tabs: orders, returns, all)")
@paginate(PagePagination)
def staff_notification_list(request, tab: str | None = None):
    return services.rows(services.staff_feed(request.auth, request.staff, tab), request.country)


@admin_notifications_router.get("/summary", auth=StaffAuth(None), response=NotificationSummaryOut, summary="The bell's badge and the counts on each tab")
def staff_notification_summary(request):
    return services.summary_of(services.staff_feed(request.auth, request.staff), services.STAFF_CATEGORIES)


@admin_notifications_router.post("/read-all", auth=StaffAuth(None), response=MarkedOut, summary="Mark all as read (optionally just one tab)")
def staff_notification_read_all(request, tab: str | None = None):
    marked = services.mark_all_read_in(services.staff_feed(request.auth, request.staff, tab), request.auth)
    realtime.read_changed(request.auth, "staff")
    return {"marked": marked}


@admin_notifications_router.post("/{notification_id}/read", auth=StaffAuth(None), response={204: None, 404: ErrorOut}, summary="Mark one as read")
def staff_notification_read(request, notification_id: UUID):
    services.mark_read_in(services.staff_feed(request.auth, request.staff), request.auth, notification_id)
    realtime.read_changed(request.auth, "staff")
    return 204, None


# --------------------------------------------------------------------------------------
# The live bell: a ticket to open the WebSocket with (see notifications/realtime.py)
# --------------------------------------------------------------------------------------

realtime_router = Router(tags=["realtime"], auth=JWTAuth())


class TicketOut(Schema):
    ticket: str
    expires_in: int = Field(description="Seconds; it also works only once")
    url: str = Field(description="Open wss://<API host><url>?ticket=<ticket> (ws:// on http). Not available on Vercel deployments: poll /summary there")


@realtime_router.post("/ticket", response=TicketOut, summary="A short-lived, single-use ticket to open the live notification socket")
def realtime_ticket(request):
    return {"ticket": realtime.issue_ticket(request.auth), "expires_in": realtime.TICKET_SECONDS, "url": "/ws/notifications"}
