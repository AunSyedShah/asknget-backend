"""Team / Managers.

  /seller/team          owner only: list, summary, roles, invite, resend, edit, remove, activity
  /seller/me            any member: who am I here, and what may I see
  /auth/team/accept     the invitation link lands here
"""

from datetime import timedelta

from django.utils import timezone
from ninja import Query, Router
from ninja.errors import HttpError
from ninja.pagination import paginate

from accounts import services as accounts
from accounts.models import User
from core.auth import OptionalJWTAuth
from core.pagination import PagePagination
from core.schemas import ErrorOut
from portal.auth import StoreAuth

from . import sections, services
from .models import StoreActivity, StoreMember, TeamRole
from .schemas import (
    AcceptIn,
    AcceptOut,
    ActivityOut,
    InviteIn,
    MeOut,
    MemberDetailOut,
    MemberPatchIn,
    MemberRowOut,
    PermissionOut,
    RoleOut,
    StoreBriefOut,
    TeamSummaryOut,
)

team_router = Router(tags=["seller: team"], auth=StoreAuth(owner_only=True))
me_router = Router(tags=["seller: team"], auth=StoreAuth())
accept_router = Router(tags=["auth"], auth=OptionalJWTAuth())

_NOT_FOUND = {404: ErrorOut}


# --------------------------------------------------------------------------- #
# Serialisation
# --------------------------------------------------------------------------- #


def _permissions(levels: dict) -> list[dict]:
    levels = sections.normalise(levels)
    return [{"section": s, "label": sections.SECTION_LABELS[s], "level": levels[s]} for s in sections.SECTIONS]


def _role_label(member: StoreMember) -> str:
    return "Custom" if member.is_custom else member.role.name


def _row(member: StoreMember) -> dict:
    expired = member.status == StoreMember.Status.INVITED and bool(member.invite_expires_at) and member.invite_expires_at < timezone.now()
    return {
        "id": str(member.pk), "name": (member.user.full_name if member.user_id else "") or member.email, "email": member.email,
        "role": _role_label(member), "role_key": member.role.key if member.role_id else None,
        "status": member.status, "is_owner": False, "invite_expired": expired, "last_active_at": member.last_active_at,
    }


def _owner_row(store) -> dict:
    owner = store.owner
    return {
        "id": "owner", "name": owner.full_name or owner.email, "email": owner.email, "role": "Owner", "role_key": None,
        "status": "active", "is_owner": True, "invite_expired": False, "last_active_at": owner.last_login,
    }


def _activity(store, user) -> list[dict]:
    return [{"id": str(a.pk), "action": a.action, "summary": a.summary, "created_at": a.created_at} for a in services.activity_for(store, user, limit=5)]


def _detail(store, member: StoreMember | None) -> dict:
    if member is None:                                   # the owner
        owner = store.owner
        return {
            **_owner_row(store), "phone": owner.phone or "", "joined_at": store.activated_at or store.created_at,
            "permissions": _permissions(sections.OWNER_ACCESS.permissions), "recent_activity": _activity(store, owner),
        }
    return {
        **_row(member), "phone": (member.user.phone if member.user_id else "") or "", "joined_at": member.joined_at,
        "permissions": _permissions(member.permissions), "recent_activity": _activity(store, member.user if member.user_id else None),
    }


def _get_member(store, member_id: str) -> StoreMember:
    member = None
    try:
        member = store.members.select_related("user", "role", "store").filter(pk=member_id).first()
    except (ValueError, TypeError):                      # not a UUID
        pass
    if member is None:
        raise HttpError(404, "Team member not found")
    return member


def _role(key: str | None) -> TeamRole | None:
    if key is None:
        return None
    role = TeamRole.objects.filter(key=key, is_active=True).first()
    if role is None:
        raise HttpError(422, "Unknown role")
    return role


def _brief(store) -> dict:
    return {"id": str(store.pk), "display_name": store.display_name, "slug": store.slug, "country": store.country_id, "is_premium": store.is_premium}


# --------------------------------------------------------------------------- #
# /seller/team   (owner only)
# --------------------------------------------------------------------------- #


@team_router.get("", response=list[MemberRowOut], summary="Team Members table (owner first)")
@paginate(PagePagination)
def team_list(request, status: str | None = None, role: str | None = None, q: str | None = None, last_active: str | None = None):
    store = request.store
    members = store.members.select_related("user", "role")
    if status:
        members = members.filter(status=status)
    if role and role != "owner":
        members = members.filter(role__key=role)
    if q:
        needle = q.strip().lower()
        members = members.filter(email__icontains=needle) | members.filter(user__full_name__icontains=needle)
    cutoff = {"today": timedelta(days=1), "week": timedelta(days=7), "month": timedelta(days=30)}.get(last_active or "")
    if cutoff:
        members = members.filter(last_active_at__gte=timezone.now() - cutoff)

    rows = [_row(m) for m in members.distinct()]
    owner = _owner_row(store)
    wants_owner = (not status or status == "active") and (not role or role == "owner")
    if q and q.strip().lower() not in f"{owner['name']} {owner['email']}".lower():
        wants_owner = False
    if cutoff and not (owner["last_active_at"] and owner["last_active_at"] >= timezone.now() - cutoff):
        wants_owner = False
    return ([owner] if wants_owner else []) + rows


@team_router.get("/summary", response=TeamSummaryOut, summary="The four KPI cards")
def team_summary(request):
    return services.summary(request.store)


@team_router.get("/roles", response=list[RoleOut], summary="Role presets (for the invite form and the filter)")
def team_roles(request):
    return TeamRole.objects.filter(is_active=True)


@team_router.post("/invites", response={201: MemberRowOut, 409: ErrorOut, 422: ErrorOut}, summary="'+ Invite Member'")
def team_invite(request, payload: InviteIn):
    member = services.invite(request.store, email=payload.email, role=_role(payload.role), permissions=payload.permissions, invited_by=request.auth)
    services.log(request.store, request.auth, "invite_member", f"Invited {member.email}", member)
    return 201, _row(StoreMember.objects.select_related("user", "role").get(pk=member.pk))


@team_router.post("/{member_id}/resend", response={200: MemberRowOut, 404: ErrorOut, 409: ErrorOut}, summary="Send a pending invitation again (new link, new expiry)")
def team_resend(request, member_id: str):
    member = services.resend(_get_member(request.store, member_id))
    return _row(member)


@team_router.get("/{member_id}", response={200: MemberDetailOut, 404: ErrorOut}, summary="Member Details (`owner` is a valid id)")
def team_detail(request, member_id: str):
    if member_id == "owner":
        return _detail(request.store, None)
    return _detail(request.store, _get_member(request.store, member_id))


@team_router.patch("/{member_id}", response={200: MemberDetailOut, 404: ErrorOut, 409: ErrorOut, 422: ErrorOut}, summary="Edit Permissions popup / change role / suspend")
def team_update(request, member_id: str, payload: MemberPatchIn):
    if member_id == "owner":
        raise HttpError(409, "The store owner's access cannot be changed")
    member = _get_member(request.store, member_id)
    data = payload.model_dump(exclude_unset=True)
    member = services.update_member(
        member, role=_role(data["role"]) if data.get("role") else None, permissions=data.get("permissions"), status=data.get("status"),
    )
    services.log(request.store, request.auth, "update_member", f"Changed access for {member.email}", member)
    return _detail(request.store, _get_member(request.store, member_id))


@team_router.delete("/{member_id}", response={204: None, 404: ErrorOut, 409: ErrorOut}, summary="Remove a member or cancel an invitation")
def team_remove(request, member_id: str):
    if member_id == "owner":
        raise HttpError(409, "The store owner cannot be removed")
    member = _get_member(request.store, member_id)
    services.log(request.store, request.auth, "remove_member", f"Removed {member.email}", member)
    member.delete()
    return 204, None


@team_router.get("/{member_id}/activity", response={200: list[ActivityOut], 404: ErrorOut}, summary="Everything this person did (newest first)")
def team_activity(request, member_id: str):
    user = request.store.owner if member_id == "owner" else _get_member(request.store, member_id).user
    return [{"id": str(a.pk), "action": a.action, "summary": a.summary, "created_at": a.created_at} for a in services.activity_for(request.store, user, limit=50)]


# --------------------------------------------------------------------------- #
# /seller/me   (any member)
# --------------------------------------------------------------------------- #


@me_router.get("", response=MeOut, summary="Who is working here and what may they see (drives the sidebar)")
def me(request):
    access, member = request.access, request.member
    return {
        "store": _brief(request.store), "is_owner": member is None, "role": "Owner" if member is None else _role_label(member),
        "permissions": _permissions(access.permissions), "sections": access.sections("view"), "can_manage_team": member is None,
    }


# --------------------------------------------------------------------------- #
# /auth/team/accept
# --------------------------------------------------------------------------- #


@accept_router.post("/accept", response={200: AcceptOut, 403: ErrorOut, 404: ErrorOut, 409: ErrorOut, 410: ErrorOut, 422: ErrorOut},
                    summary="Accept an invitation (sign in first if the e-mail already has an account)")
def team_accept(request, payload: AcceptIn):
    member = services.find_invite(payload.token)
    user = request.auth if isinstance(request.auth, User) else None
    created = False
    if user is None:
        if User.objects.filter(email__iexact=member.email).exists():
            raise HttpError(409, "account_exists")       # sign in as that account, then accept
        if not payload.full_name or not payload.password:
            raise HttpError(422, "Choose a name and a password to create your account")
        user = accounts.register_customer(
            email=member.email, password=payload.password, phone=None, full_name=payload.full_name, country=getattr(request, "country", None),
        )
        user.email_verified_at = timezone.now()          # the link arrived in that inbox
        user.save(update_fields=["email_verified_at"])
        created = True
    member = services.accept(member, user)
    services.log(member.store, user, "join_team", f"{user.full_name or user.email} joined the team", member)
    return {
        "store": _brief(member.store), "role": _role_label(member), "created_account": created,
        "tokens": accounts.issue_tokens(request, user) if created else None,
    }
