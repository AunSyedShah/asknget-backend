from ninja import Router, Schema

from core.auth import StaffAuth

admin_me_router = Router(tags=["admin: me"], auth=StaffAuth())


class StaffMeOut(Schema):
    is_superuser: bool
    permissions: list[str]
    markets: list[str] | None


@admin_me_router.get("", auth=StaffAuth(None), response=StaffMeOut,
                     summary="What the signed-in staff member may do (permission codes) and see (market codes; null = every market)")
def admin_me(request):
    staff = request.staff
    return {"is_superuser": staff.is_superuser, "permissions": sorted(staff.permissions),
            "markets": sorted(staff.markets) if staff.markets is not None else None}
