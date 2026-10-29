"""What one staff member may do and see, worked out once per request by `core.auth.StaffAuth`.

Views read it from `request.staff`:
    qs = scope(request, qs)                      # lists: only the staff member's markets
    check_market(request, obj.country_id)        # details: outside them is a 404
    require_every_market(request)                # platform-wide settings
"""

from dataclasses import dataclass

from django.contrib.auth.models import Permission
from django.db.models import Q
from ninja.errors import HttpError


@dataclass(frozen=True)
class StaffAccess:
    is_superuser: bool
    permissions: frozenset[str]         # catalogue codes, e.g. "orders.view"
    markets: frozenset[str] | None      # country codes; None = every market

    def can(self, code: str) -> bool:
        return self.is_superuser or code in self.permissions

    def sees(self, country_code) -> bool:
        return self.markets is None or country_code in self.markets


def access_for(user) -> StaffAccess:
    """Two queries for a staff member (permissions, markets), none for a superuser."""
    from .models import StaffProfile
    from .permissions import CATALOGUE

    if user.is_superuser:
        return StaffAccess(True, frozenset(CATALOGUE), None)
    codes = Permission.objects.filter(Q(group__user=user) | Q(user=user), content_type__app_label="staff", codename__in=CATALOGUE).values_list("codename", flat=True)
    markets = list(StaffProfile.markets.through.objects.filter(staffprofile__user=user).values_list("country_id", flat=True))
    return StaffAccess(False, frozenset(codes), frozenset(markets) or None)


def scope(request, qs, field: str = "country"):
    markets = request.staff.markets
    return qs if markets is None else qs.filter(**{f"{field}__in": markets})


def check_market(request, country_code, what: str = "Not found"):
    if not request.staff.sees(country_code):
        raise HttpError(404, what)


def require_every_market(request):
    if request.staff.markets is not None:
        raise HttpError(403, "This setting applies to every market: only staff without a market limit can change it")
