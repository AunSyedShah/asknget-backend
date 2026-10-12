"""Dashboard auth: someone working in an **active store**, as its owner or as a team member.

Sets `request.store`, `request.member` (None for the owner) and `request.access` (what they
may do), and switches the request to the store's market: a seller's numbers are in their
store's currency and timezone whichever domain they log in from.

    products_router = Router(auth=StoreAuth(section="products"))   # view to read, manage to change
    team_router     = Router(auth=StoreAuth(owner_only=True))
    plan_router     = Router(auth=StoreAuth())                    # any member of the store
"""

from zoneinfo import ZoneInfo

from django.utils import timezone
from ninja.errors import HttpError

from core.auth import JWTAuth
from markets import context
from team import services as team
from team.sections import OWNER_ACCESS, required_level


class StoreAuth(JWTAuth):
    """403 `no_active_store` until onboarding is approved and the store activated, so the portal
    can route the user back to the application status page; 403 `permission_denied` when a team
    member lacks the level this route needs."""

    def __init__(self, section: str | None = None, *, owner_only: bool = False):
        super().__init__()
        self.section = section
        self.owner_only = owner_only

    def authenticate(self, request, token: str):
        user = super().authenticate(request, token)
        if user is None:
            return None

        store, member = team.resolve_membership(user, request.headers.get("X-Store"))
        if store is None:
            if not user.is_seller and not user.store_memberships.exists():
                raise HttpError(403, "Seller account required")
            raise HttpError(403, "no_active_store")

        access = OWNER_ACCESS if member is None else member.access()
        if self.owner_only and member is not None:
            raise HttpError(403, "permission_denied")
        if self.section and not access.allows(self.section, required_level(request.method)):
            raise HttpError(403, "permission_denied")

        request.store, request.member, request.access = store, member, access
        if member is not None:
            team.touch(member)

        if getattr(request, "country", None) is None or request.country.code != store.country_id:
            # Re-point the request at the store's market. The middleware's `finally`
            # resets everything after the response, so no token bookkeeping here.
            request.country = store.country
            request.timezone = store.country.timezone
            context.activate(store.country)
            timezone.activate(ZoneInfo(store.country.timezone))
        return user
