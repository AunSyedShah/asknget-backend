"""JWT authentication for django-ninja.

Access tokens are short-lived, stateless JWTs. Refresh tokens are opaque
random strings stored *hashed* in accounts.RefreshToken so they can be revoked
(Logout popup) and rotated on every use.

Usage in a router:
    router = Router(auth=JWTAuth())
    def view(request): request.auth  # -> User
"""

import hashlib
import secrets
from datetime import timedelta

import jwt
from django.conf import settings
from django.utils import timezone
from ninja.errors import HttpError
from ninja.security import HttpBearer

ALGORITHM = "HS256"


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def create_access_token(user) -> tuple[str, int]:
    """Returns (token, expires_in_seconds)."""
    ttl = timedelta(minutes=settings.JWT_ACCESS_TTL_MINUTES)
    now = timezone.now()
    payload = {
        "sub": str(user.id),
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
        "type": "access",
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM), int(ttl.total_seconds())


def create_refresh_token(user, *, device: str = "", ip: str | None = None) -> str:
    from accounts.models import RefreshToken

    raw = secrets.token_urlsafe(48)
    RefreshToken.objects.create(
        user=user,
        token_hash=_hash(raw),
        device=device[:255],
        ip=ip,
        expires_at=timezone.now() + timedelta(days=settings.JWT_REFRESH_TTL_DAYS),
    )
    return raw


def rotate_refresh_token(raw: str, *, device: str = "", ip: str | None = None):
    """Validate + revoke the presented refresh token and issue a new one.
    Returns (user, new_raw_token). Raises HttpError(401) if invalid."""
    from accounts.models import RefreshToken

    token = RefreshToken.objects.select_related("user").filter(token_hash=_hash(raw)).first()
    if token is None or not token.is_valid:
        raise HttpError(401, "Invalid or expired refresh token")
    token.revoked_at = timezone.now()
    token.save(update_fields=["revoked_at"])
    _ensure_user_can_login(token.user)
    return token.user, create_refresh_token(token.user, device=device, ip=ip)


def revoke_refresh_token(raw: str) -> None:
    from accounts.models import RefreshToken

    RefreshToken.objects.filter(token_hash=_hash(raw), revoked_at__isnull=True).update(revoked_at=timezone.now())


def revoke_all_refresh_tokens(user) -> int:
    from accounts.models import RefreshToken

    return RefreshToken.objects.filter(user=user, revoked_at__isnull=True).update(revoked_at=timezone.now())


def _ensure_user_can_login(user) -> None:
    from accounts.models import User

    if not user.is_active or user.status != User.UserStatus.ACTIVE:
        raise HttpError(403, "Account is not active")


class JWTAuth(HttpBearer):
    """Sets request.auth to the authenticated User."""

    def authenticate(self, request, token: str):
        from accounts.models import User

        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        except jwt.ExpiredSignatureError:
            raise HttpError(401, "Access token expired")
        except jwt.InvalidTokenError:
            return None  # -> 401 Unauthorized from Ninja
        if payload.get("type") != "access":
            return None
        user = User.objects.filter(id=payload.get("sub")).first()
        if user is None:
            return None
        _ensure_user_can_login(user)
        return user


class OptionalJWTAuth(JWTAuth):
    """For endpoints that behave differently when logged in (e.g. cart, reviews)
    but must also work for guests. request.auth is the User or None."""

    def __call__(self, request):
        result = super().__call__(request)
        return result if result is not None else AnonymousMarker()


class AnonymousMarker:
    """Truthy sentinel so Ninja treats the request as authenticated while views
    can still test `isinstance(request.auth, User)`."""

    is_authenticated = False

    def __bool__(self):
        return True
