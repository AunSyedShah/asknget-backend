"""Marketing / support routers — all public; contact links the signed-in user when present."""

from django.core.mail import send_mail
from django.db.models import Q
from django.utils import timezone
from ninja import Query, Router

from core.auth import OptionalJWTAuth
from core.models import SiteSetting
from core.schemas import ErrorOut, MessageOut
from markets.context import get_country

from .models import Banner, ContactMessage, NewsletterSubscriber
from .schemas import BannerOut, ContactCreatedOut, ContactFormIn, NewsletterSubscribeIn, PlacementLit, SiteInfoOut

marketing_router = Router(tags=["marketing"])


@marketing_router.get("/banners", response=list[BannerOut], summary="Landing hero / promo tiles (live ones only)")
def banners(request, placement: PlacementLit | None = Query(None)):
    now = timezone.now()
    qs = (
        Banner.objects.filter(is_active=True)
        .filter(Q(starts_at__isnull=True) | Q(starts_at__lte=now))
        .filter(Q(ends_at__isnull=True) | Q(ends_at__gte=now))
    )
    if placement:
        qs = qs.filter(placement=placement)
    country = get_country()
    if country is not None:
        qs = qs.filter(Q(country__isnull=True) | Q(country=country))
    return qs.order_by("placement", "sort_order")


@marketing_router.post("/newsletter/subscribe", response={201: MessageOut, 200: MessageOut}, summary="'Subscribe Now'")
def newsletter_subscribe(request, payload: NewsletterSubscribeIn):
    sub, created = NewsletterSubscriber.objects.get_or_create(email=payload.email, defaults={"source": payload.source})
    if not created and sub.unsubscribed_at:
        sub.unsubscribed_at = None
        sub.save(update_fields=["unsubscribed_at", "updated_at"])
        created = True
    return (201 if created else 200), {"detail": "You're on the list!" if created else "You're already subscribed"}


@marketing_router.post("/contact", response={201: ContactCreatedOut, 422: ErrorOut}, auth=OptionalJWTAuth(), summary="'Send a Message'")
def contact(request, payload: ContactFormIn):
    user = request.auth if getattr(request.auth, "pk", None) else None
    application = None
    if user is not None and payload.subject == "seller_support":
        from sellers.models import SellerApplication

        application = SellerApplication.objects.filter(user=user).first()
    msg = ContactMessage.objects.create(
        user=user, seller_application=application, first_name=payload.first_name, last_name=payload.last_name,
        email=payload.email, phone=payload.phone or "", subject=payload.subject, message=payload.message,
    )
    support = SiteSetting.objects.filter(key="support_email").values_list("value", flat=True).first() or "support@asknget.local"
    send_mail(
        f"[asknget contact] {msg.get_subject_display()} from {msg.first_name} {msg.last_name}",
        f"{msg.message}\n\nReply to: {msg.email} {msg.phone}".strip(), None, [support], fail_silently=True,
    )
    return 201, {"id": msg.id, "detail": "Thanks — we'll get back to you shortly."}


@marketing_router.get("/site-info", response=SiteInfoOut, summary="Footer + 'Contact Info' block")
def site_info(request):
    keys = ("contact_phone", "contact_email", "address", "social", "accepted_payments")
    values = {s.key: s.value for s in SiteSetting.objects.filter(key__in=keys)}
    country = get_country()
    if country is not None:                                   # this market's own details win over the platform-wide ones
        values.update({k: v for k, v in (country.site_info or {}).items() if k in keys and v not in (None, "", {})})
    values["contact_subjects"] = [{"value": v, "label": l} for v, l in ContactMessage.ContactSubject.choices]
    return values
