"""Dashboard auth: a seller **with an active store**. Sets `request.store` and
switches the request to the store's market: a seller's numbers are in their
store's currency and timezone whichever domain they log in from."""

from zoneinfo import ZoneInfo

from django.utils import timezone
from ninja.errors import HttpError

from core.auth import SellerAuth
from markets import context
from sellers.models import Store


class StoreAuth(SellerAuth):
    """403 `no_active_store` until onboarding is approved and the store activated,
    so the portal can route the user back to the application status page."""

    def authenticate(self, request, token: str):
        user = super().authenticate(request, token)
        if user is None:
            return None
        store = (
            Store.objects.filter(owner=user, status=Store.StoreStatus.ACTIVE)
            .select_related("country__return_policy", "country__kyc_policy").order_by("created_at").first()
        )
        if store is None:
            raise HttpError(403, "no_active_store")
        request.store = store
        if getattr(request, "country", None) is None or request.country.code != store.country_id:
            # Re-point the request at the store's market. The middleware's `finally`
            # resets everything after the response, so no token bookkeeping here.
            request.country = store.country
            request.timezone = store.country.timezone
            context.activate(store.country)
            timezone.activate(ZoneInfo(store.country.timezone))
        return user
