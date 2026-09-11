"""Business rules for accounts. Routers call these; schemas validate shape,
services validate state (uniqueness, cooldowns, status). Keep endpoint
functions thin so flow changes land here only.
"""

import hashlib
import hmac
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db import models, transaction
from django.utils import timezone
from ninja.errors import HttpError

from core import auth as jwt_auth

from core.models import coerce_nulls

from .models import Address, OtpVerification, PaymentMethod, TermsAcceptance, User

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Registration / login
# --------------------------------------------------------------------------- #


def _assert_unique(email: str, phone: str | None) -> None:
    if User.objects.filter(email=email).exists():
        raise HttpError(409, "An account with this email already exists")
    if phone and User.objects.filter(phone=phone).exists():
        raise HttpError(409, "An account with this phone number already exists")


def _run_django_password_validators(password: str, user: User | None) -> None:
    try:
        validate_password(password, user)
    except ValidationError as e:
        raise HttpError(422, " ".join(e.messages))


@transaction.atomic
def register_customer(*, email: str, password: str, phone: str | None, full_name: str | None) -> User:
    _assert_unique(email, phone)
    _run_django_password_validators(password, None)
    return User.objects.create_user(email=email, password=password, phone=phone, full_name=full_name or "")


@transaction.atomic
def register_seller(*, email: str, password: str, phone: str, account_type: str, ip: str | None) -> User:
    """'Create your seller account' — user + draft application + terms audit rows."""
    from sellers.models import SellerApplication

    _assert_unique(email, phone)
    _run_django_password_validators(password, None)
    user = User.objects.create_user(email=email, password=password, phone=phone, is_seller=True)
    SellerApplication.objects.create(user=user, account_type=account_type)
    TermsAcceptance.objects.bulk_create([
        TermsAcceptance(user=user, document=TermsAcceptance.TermsDocument.SELLER_TERMS, ip=ip),
        TermsAcceptance(user=user, document=TermsAcceptance.TermsDocument.PRIVACY_POLICY, ip=ip),
    ])
    return user


def login(request, *, email: str, password: str) -> User:
    user = authenticate(request, username=email, password=password)
    if user is None:
        raise HttpError(401, "Invalid email or password")
    jwt_auth._ensure_user_can_login(user)
    user.last_login = timezone.now()
    user.save(update_fields=["last_login"])
    return user


def issue_tokens(request, user: User) -> dict:
    access, expires_in = jwt_auth.create_access_token(user)
    refresh = jwt_auth.create_refresh_token(user, device=request.META.get("HTTP_USER_AGENT", ""), ip=client_ip(request))
    return {"access_token": access, "refresh_token": refresh, "expires_in": expires_in}


def refresh_tokens(request, raw_refresh: str) -> dict:
    user, new_refresh = jwt_auth.rotate_refresh_token(
        raw_refresh, device=request.META.get("HTTP_USER_AGENT", ""), ip=client_ip(request)
    )
    access, expires_in = jwt_auth.create_access_token(user)
    return {"access_token": access, "refresh_token": new_refresh, "expires_in": expires_in}


def logout(user: User, *, raw_refresh: str | None, all_devices: bool) -> None:
    if all_devices or raw_refresh is None:
        jwt_auth.revoke_all_refresh_tokens(user)
    else:
        jwt_auth.revoke_refresh_token(raw_refresh)


def client_ip(request) -> str | None:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    return forwarded.split(",")[0].strip() if forwarded else request.META.get("REMOTE_ADDR")


# --------------------------------------------------------------------------- #
# OTP
# --------------------------------------------------------------------------- #


def _otp_hash(code: str, target: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), f"{target}:{code}".encode(), hashlib.sha256).hexdigest()


def mask_target(target: str) -> str:
    if "@" in target:
        local, _, domain = target.partition("@")
        return f"{local[0]}***@{domain}"
    return f"{target[:3]}{'*' * (len(target) - 6)}{target[-3:]}"


def _deliver_otp(channel: str, target: str, code: str) -> None:
    if channel == OtpVerification.OtpChannel.EMAIL:
        send_mail(
            subject="Your asknget verification code",
            message=f"Your 6-digit verification code is {code}. It expires in {settings.OTP_TTL_MINUTES} minutes.",
            from_email=None,
            recipient_list=[target],
        )
    else:
        # TODO(infra): plug in an SMS provider (Twilio / local gateway). Console log until then.
        log.info("SMS OTP to %s: %s", target, code)


def send_otp(*, user: User | None, channel: str, purpose: str, target: str | None) -> OtpVerification:
    if target is None:
        if user is None:
            raise HttpError(422, "target is required when not authenticated")
        target = user.email if channel == OtpVerification.OtpChannel.EMAIL else user.phone
        if not target:
            raise HttpError(422, f"No {channel} on file for this account")

    if purpose == OtpVerification.OtpPurpose.PASSWORD_RESET:
        # Never reveal whether an email exists; still enforce the cooldown per target.
        user = User.objects.filter(email=target).first()

    now = timezone.now()
    latest = (
        OtpVerification.objects.filter(target=target, purpose=purpose, verified_at__isnull=True)
        .order_by("-created_at")
        .first()
    )
    if latest and latest.resend_available_at > now:
        wait = int((latest.resend_available_at - now).total_seconds())
        raise HttpError(429, f"Please wait {wait}s before requesting another code")

    code = f"{secrets.randbelow(10**6):06d}"
    otp = OtpVerification.objects.create(
        user=user,
        channel=channel,
        target=target,
        purpose=purpose,
        code_hash=_otp_hash(code, target),
        expires_at=now + timedelta(minutes=settings.OTP_TTL_MINUTES),
        resend_available_at=now + timedelta(seconds=settings.OTP_RESEND_COOLDOWN_SECONDS),
    )
    if user is not None or purpose != OtpVerification.OtpPurpose.PASSWORD_RESET:
        _deliver_otp(channel, target, code)
    return otp


def verify_otp(*, channel: str, purpose: str, target: str, code: str) -> OtpVerification:
    """Not wrapped in a transaction on purpose: a wrong code must *commit* the
    attempts increment even though we raise."""
    otp = (
        OtpVerification.objects
        .filter(target=target, purpose=purpose, channel=channel, verified_at__isnull=True)
        .order_by("-created_at")
        .first()
    )
    if otp is None or otp.expires_at < timezone.now():
        raise HttpError(400, "Code expired or not found. Request a new one.")
    if otp.attempts >= settings.OTP_MAX_ATTEMPTS:
        raise HttpError(429, "Too many attempts. Request a new code.")
    if not hmac.compare_digest(otp.code_hash, _otp_hash(code, target)):
        OtpVerification.objects.filter(pk=otp.pk).update(attempts=models.F("attempts") + 1)
        raise HttpError(400, "Incorrect code")

    with transaction.atomic():
        otp.verified_at = timezone.now()
        otp.save(update_fields=["verified_at"])
        # Side effects per purpose — extend here as new flows appear.
        if otp.user and purpose == OtpVerification.OtpPurpose.SIGNUP_EMAIL:
            otp.user.email_verified_at = otp.verified_at
            otp.user.save(update_fields=["email_verified_at"])
        elif otp.user and purpose == OtpVerification.OtpPurpose.SIGNUP_PHONE:
            otp.user.phone_verified_at = otp.verified_at
            otp.user.save(update_fields=["phone_verified_at"])
    return otp


# --------------------------------------------------------------------------- #
# Password
# --------------------------------------------------------------------------- #


def change_password(user: User, *, current: str, new: str) -> None:
    if not user.check_password(current):
        raise HttpError(400, "Current password is incorrect")
    _run_django_password_validators(new, user)
    user.set_password(new)
    user.save(update_fields=["password"])
    jwt_auth.revoke_all_refresh_tokens(user)  # force re-login everywhere


def reset_password(*, email: str, code: str, new: str) -> None:
    otp = verify_otp(channel=OtpVerification.OtpChannel.EMAIL, purpose=OtpVerification.OtpPurpose.PASSWORD_RESET, target=email, code=code)
    user = otp.user or User.objects.filter(email=email).first()
    if user is None:
        raise HttpError(400, "Invalid code")
    _run_django_password_validators(new, user)
    with transaction.atomic():
        user.set_password(new)
        user.save(update_fields=["password"])
        jwt_auth.revoke_all_refresh_tokens(user)


# --------------------------------------------------------------------------- #
# Profile
# --------------------------------------------------------------------------- #


def update_profile(user: User, data: dict) -> User:
    for field, value in coerce_nulls(User, data).items():
        setattr(user, field, value)
    user.save(update_fields=list(data.keys()) + ["updated_at"])
    return user


ALLOWED_AVATAR_TYPES = {"image/png", "image/jpeg", "image/gif"}


def set_avatar(user: User, file) -> User:
    if file.content_type not in ALLOWED_AVATAR_TYPES:
        raise HttpError(422, "Avatar must be PNG, JPG or GIF")
    if file.size > settings.AVATAR_MAX_BYTES:
        raise HttpError(422, "Avatar must be 2 MB or smaller")
    if user.avatar:
        user.avatar.delete(save=False)
    user.avatar.save(file.name, file, save=True)
    return user


def accept_terms(user: User, *, document: str, version: str, ip: str | None) -> TermsAcceptance:
    return TermsAcceptance.objects.create(user=user, document=document, version=version, ip=ip)


# --------------------------------------------------------------------------- #
# Addresses / payment methods — "only one default" is enforced by a partial
# unique constraint, so we clear the old default inside the same transaction.
# --------------------------------------------------------------------------- #


@transaction.atomic
def create_address(user: User, data: dict) -> Address:
    data = coerce_nulls(Address, data)
    if data.get("is_default") or not user.addresses.exists():
        user.addresses.update(is_default=False)
        data["is_default"] = True
    return Address.objects.create(user=user, **data)


@transaction.atomic
def update_address(address: Address, data: dict) -> Address:
    if data.get("is_default"):
        address.user.addresses.exclude(pk=address.pk).update(is_default=False)
    for field, value in coerce_nulls(Address, data).items():
        setattr(address, field, value)
    address.save()
    return address


@transaction.atomic
def set_default_address(address: Address) -> Address:
    address.user.addresses.exclude(pk=address.pk).update(is_default=False)
    address.is_default = True
    address.save(update_fields=["is_default", "updated_at"])
    return address


@transaction.atomic
def delete_address(address: Address) -> None:
    was_default = address.is_default
    address.is_default = False
    address.save(update_fields=["is_default"])
    address.soft_delete()
    if was_default:
        replacement = address.user.addresses.order_by("-created_at").first()
        if replacement:
            replacement.is_default = True
            replacement.save(update_fields=["is_default"])


@transaction.atomic
def create_payment_method(user: User, data: dict) -> PaymentMethod:
    data = coerce_nulls(PaymentMethod, data)
    if data.get("is_default") or not user.payment_methods.exists():
        user.payment_methods.update(is_default=False)
        data["is_default"] = True
    return PaymentMethod.objects.create(user=user, **data)


@transaction.atomic
def set_default_payment_method(pm: PaymentMethod) -> PaymentMethod:
    pm.user.payment_methods.exclude(pk=pm.pk).update(is_default=False)
    pm.is_default = True
    pm.save(update_fields=["is_default", "updated_at"])
    return pm


@transaction.atomic
def delete_payment_method(pm: PaymentMethod) -> None:
    was_default = pm.is_default
    pm.is_default = False
    pm.save(update_fields=["is_default"])
    pm.soft_delete()
    if was_default:
        replacement = pm.user.payment_methods.order_by("-created_at").first()
        if replacement:
            replacement.is_default = True
            replacement.save(update_fields=["is_default"])
