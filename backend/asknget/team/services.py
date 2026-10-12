"""Team logic: which store a request works in, and the invite lifecycle."""

import hashlib
import secrets
from datetime import timedelta
from uuid import UUID

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from ninja.errors import HttpError

from config import services as config
from sellers.models import Store

from . import sections
from .models import StoreActivity, StoreMember, TeamRole

TOUCH_EVERY = timedelta(minutes=1)


# --------------------------------------------------------------------------------------
# Which store is this request about, and who is asking?
# --------------------------------------------------------------------------------------


def _uuid(value):
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def resolve_membership(user, store_header=None):
    """(store, member) for a signed-in user, or (None, None).

    `member` is None when the user owns the store. Without an `X-Store` header a user's own
    store comes first, then the first store they joined. With one, that exact store must be
    theirs (owned or joined), otherwise they get nothing: a header never silently falls back
    to a different store than the one asked for.

    The owner path is a single query, exactly as before teams existed.
    """
    wanted = _uuid(store_header) if store_header else None
    if store_header and wanted is None:
        return None, None

    related = ("country__return_policy", "country__kyc_policy")
    owned = Store.objects.filter(owner=user, status=Store.StoreStatus.ACTIVE).select_related(*related)
    if wanted:
        store = owned.filter(pk=wanted).first()
    else:
        store = owned.order_by("created_at").first()
    if store is not None:
        return store, None

    memberships = (
        StoreMember.objects.filter(user=user, status=StoreMember.Status.ACTIVE, store__status=Store.StoreStatus.ACTIVE)
        .select_related("store__country__return_policy", "store__country__kyc_policy", "role")
    )
    if wanted:
        memberships = memberships.filter(store_id=wanted)
    member = memberships.order_by("joined_at", "created_at").first()
    return (member.store, member) if member else (None, None)


def touch(member) -> None:
    """Remember when someone was last active, at most once a minute: a write on every request
    would turn each page load into two queries for something shown as '12 min ago'."""
    now = timezone.now()
    if member.last_active_at is None or now - member.last_active_at >= TOUCH_EVERY:
        StoreMember.objects.filter(pk=member.pk).update(last_active_at=now)
        member.last_active_at = now


# --------------------------------------------------------------------------------------
# Invitations
# --------------------------------------------------------------------------------------


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def seats_in_use(store) -> int:
    """People who occupy a seat: invited or active. A suspended member does not."""
    return store.members.filter(status__in=(StoreMember.Status.INVITED, StoreMember.Status.ACTIVE)).count()


def _link(token: str) -> str:
    base = (getattr(settings, "PORTAL_URL", "") or getattr(settings, "STOREFRONT_URL", "")).rstrip("/")
    return f"{base}/accept-invite?token={token}"


def _send_invite(member: StoreMember, token: str) -> None:
    inviter = member.invited_by.full_name or member.invited_by.email if member.invited_by else "The store owner"
    send_mail(
        subject=f"You've been invited to {member.store.display_name} on asknget",
        message=(
            f"{inviter} invited you to help run {member.store.display_name}"
            f"{f' as {member.role.name}' if member.role else ''}.\n\n"
            f"Accept the invitation: {_link(token)}\n\n"
            f"The link works until {timezone.localtime(member.invite_expires_at):%d %b %Y, %H:%M}. "
            "If you weren't expecting this, you can ignore this e-mail."
        ),
        from_email=None,
        recipient_list=[member.email],
    )


def _issue(member: StoreMember) -> str:
    token = secrets.token_urlsafe(32)
    days = config.get("team.invite_expiry_days", store=member.store)
    member.invite_token_hash = _hash(token)
    member.invite_expires_at = timezone.now() + timedelta(days=days)
    return token


@transaction.atomic
def invite(store, *, email: str, role: TeamRole | None, permissions: dict | None, invited_by) -> StoreMember:
    email = email.strip().lower()
    if store.owner.email.lower() == email:
        raise HttpError(409, "That is the store owner's own address")
    if role is None and not permissions:
        raise HttpError(422, "Choose a role or set permissions")
    if role is not None and not role.is_active:
        raise HttpError(422, "That role is not available")
    limit = config.get("team.max_members", store=store)
    if limit and seats_in_use(store) >= limit:
        raise HttpError(409, "member_limit")
    if store.members.filter(email__iexact=email).exists():
        raise HttpError(409, "That person is already on the team or has a pending invitation")
    try:
        levels = sections.normalise(permissions if permissions else (role.permissions if role else {}))
    except ValueError as exc:
        raise HttpError(422, str(exc)) from None

    member = StoreMember(store=store, email=email, role=role, permissions=levels, invited_by=invited_by)
    token = _issue(member)
    member.save()
    transaction.on_commit(lambda: _send_invite(member, token))
    return member


@transaction.atomic
def resend(member: StoreMember) -> StoreMember:
    if member.status != StoreMember.Status.INVITED:
        raise HttpError(409, "Only a pending invitation can be re-sent")
    token = _issue(member)
    member.save(update_fields=["invite_token_hash", "invite_expires_at", "updated_at"])
    transaction.on_commit(lambda: _send_invite(member, token))
    return member


def find_invite(token: str) -> StoreMember:
    """The pending invitation behind a link, or 404 (never reveals whether a token ever existed)
    / 410 when it has lapsed."""
    member = (
        StoreMember.objects.select_related("store", "role", "invited_by")
        .filter(invite_token_hash=_hash(token or ""), status=StoreMember.Status.INVITED)
        .first()
    )
    if member is None:
        raise HttpError(404, "This invitation does not exist or was already used")
    if member.invite_expires_at and member.invite_expires_at < timezone.now():
        raise HttpError(410, "This invitation has expired. Ask the store owner to send a new one")
    return member


@transaction.atomic
def accept(member: StoreMember, user) -> StoreMember:
    if user.email.lower() != member.email:
        raise HttpError(403, "This invitation was sent to a different e-mail address")
    member.user = user
    member.status = StoreMember.Status.ACTIVE
    member.joined_at = timezone.now()
    member.invite_token_hash = ""          # one use
    member.invite_expires_at = None
    member.save()
    return member


# --------------------------------------------------------------------------------------
# Editing
# --------------------------------------------------------------------------------------


@transaction.atomic
def update_member(member: StoreMember, *, role: TeamRole | None = None, permissions: dict | None = None, status: str | None = None) -> StoreMember:
    if role is not None:
        if not role.is_active:
            raise HttpError(422, "That role is not available")
        member.role = role
        if permissions is None:                       # a new role brings its own levels
            member.permissions = sections.normalise(role.permissions)
    if permissions is not None:
        try:
            member.permissions = sections.normalise(permissions)
        except ValueError as exc:
            raise HttpError(422, str(exc)) from None
    if status is not None:
        allowed = {StoreMember.Status.ACTIVE, StoreMember.Status.SUSPENDED}
        if status not in allowed or member.status == StoreMember.Status.INVITED:
            raise HttpError(409, "Only an active member can be suspended or reinstated")
        member.status = status
    member.save()
    return member


# --------------------------------------------------------------------------------------
# Activity and summary
# --------------------------------------------------------------------------------------


def log(store, actor, action: str, summary: str, target=None) -> None:
    """Record something a person did, in words. `target` is any model instance."""
    StoreActivity.objects.create(
        store=store, actor=actor if getattr(actor, "pk", None) else None, action=action, summary=summary[:255],
        target_type=target._meta.model_name if target is not None else "", target_id=str(target.pk) if target is not None else "",
    )


def summary(store) -> dict:
    members = store.members.select_related("role")
    total = active = pending = managers = 0
    for m in members:
        total += 1
        if m.status == StoreMember.Status.ACTIVE:
            active += 1
            managers += 1 if m.role and m.role.counts_as_manager else 0
        elif m.status == StoreMember.Status.INVITED:
            pending += 1
    return {"total": total + 1, "active": active + 1, "pending_invites": pending, "managers": managers}   # +1: the owner


def activity_for(store, user, limit: int = 20):
    return StoreActivity.objects.filter(store=store, actor=user).order_by("-created_at")[:limit] if user else StoreActivity.objects.none()
