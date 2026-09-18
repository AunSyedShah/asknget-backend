"""Dashboard auth: a seller **with an active store**. Sets `request.store`."""

from ninja.errors import HttpError

from core.auth import SellerAuth
from sellers.models import Store


class StoreAuth(SellerAuth):
    """403 `no_active_store` until onboarding is approved and the store activated,
    so the portal can route the user back to the application status page."""

    def authenticate(self, request, token: str):
        user = super().authenticate(request, token)
        if user is None:
            return None
        store = Store.objects.filter(owner=user, status=Store.StoreStatus.ACTIVE).order_by("created_at").first()
        if store is None:
            raise HttpError(403, "no_active_store")
        request.store = store
        return user
