"""Accounts routers.

  /auth/*   public (register, login, tokens, OTP, password reset)
  /me/*     authenticated (profile, addresses, payment methods, password change)
"""

from uuid import UUID

from django.shortcuts import get_object_or_404
from ninja import File, Router
from ninja.files import UploadedFile

from core.auth import JWTAuth, OptionalJWTAuth
from core.schemas import ErrorOut, MessageOut

from . import services
from .models import Address, PaymentMethod, User
from .schemas import (
    AddressIn,
    AddressOut,
    AddressPatchIn,
    AuthOut,
    LoginIn,
    LogoutIn,
    OtpSendIn,
    OtpSendOut,
    OtpVerifyIn,
    PasswordChangeIn,
    PasswordForgotIn,
    PasswordResetIn,
    PaymentMethodIn,
    PaymentMethodOut,
    RefreshIn,
    RegisterIn,
    SellerRegisterIn,
    TermsAcceptIn,
    TokenOut,
    UserOut,
    UserPatchIn,
)

auth_router = Router(tags=["auth"])
me_router = Router(tags=["me"], auth=JWTAuth())

# --------------------------------------------------------------------------- #
# /auth
# --------------------------------------------------------------------------- #


@auth_router.post("/register", response={201: AuthOut, 409: ErrorOut, 422: ErrorOut}, summary="Register as a customer")
def register(request, payload: RegisterIn):
    user = services.register_customer(
        email=payload.email, password=payload.password, phone=payload.phone, full_name=payload.full_name
    )
    return 201, {"user": user, "tokens": services.issue_tokens(request, user)}


@auth_router.post(
    "/seller/register",
    response={201: AuthOut, 409: ErrorOut, 422: ErrorOut},
    summary="Register as a seller ('Create your seller account')",
)
def seller_register(request, payload: SellerRegisterIn):
    user = services.register_seller(
        email=payload.email,
        password=payload.password,
        phone=payload.phone,
        account_type=payload.account_type,
        ip=services.client_ip(request),
    )
    return 201, {"user": user, "tokens": services.issue_tokens(request, user)}


@auth_router.post("/login", response={200: AuthOut, 401: ErrorOut, 403: ErrorOut})
def login(request, payload: LoginIn):
    user = services.login(request, email=payload.email, password=payload.password)
    return {"user": user, "tokens": services.issue_tokens(request, user)}


@auth_router.post("/refresh", response={200: TokenOut, 401: ErrorOut}, summary="Rotate refresh token")
def refresh(request, payload: RefreshIn):
    return services.refresh_tokens(request, payload.refresh_token)


@auth_router.post("/logout", response={204: None}, auth=JWTAuth())
def logout(request, payload: LogoutIn):
    services.logout(request.auth, raw_refresh=payload.refresh_token, all_devices=payload.all_devices)
    return 204, None


@auth_router.post(
    "/otp/send",
    response={200: OtpSendOut, 422: ErrorOut, 429: ErrorOut},
    auth=OptionalJWTAuth(),
    summary="Send a 6-digit code by email or SMS",
)
def otp_send(request, payload: OtpSendIn):
    user = request.auth if isinstance(request.auth, User) else None
    otp = services.send_otp(user=user, channel=payload.channel, purpose=payload.purpose, target=payload.target)
    return {
        "target_masked": services.mask_target(otp.target),
        "expires_at": otp.expires_at,
        "resend_available_at": otp.resend_available_at,
    }


@auth_router.post("/otp/verify", response={200: MessageOut, 400: ErrorOut, 429: ErrorOut})
def otp_verify(request, payload: OtpVerifyIn):
    services.verify_otp(channel=payload.channel, purpose=payload.purpose, target=payload.target, code=payload.code)
    return {"detail": "Verified"}


@auth_router.post("/password/forgot", response={200: MessageOut, 429: ErrorOut})
def password_forgot(request, payload: PasswordForgotIn):
    services.send_otp(user=None, channel="email", purpose="password_reset", target=payload.email)
    # Same response whether or not the email exists.
    return {"detail": "If that email is registered, a reset code has been sent"}


@auth_router.post("/password/reset", response={200: MessageOut, 400: ErrorOut, 422: ErrorOut})
def password_reset(request, payload: PasswordResetIn):
    services.reset_password(email=payload.email, code=payload.code, new=payload.new_password)
    return {"detail": "Password updated. Please sign in again."}


# --------------------------------------------------------------------------- #
# /me
# --------------------------------------------------------------------------- #


@me_router.get("", response=UserOut)
def me(request):
    return request.auth


@me_router.patch("", response={200: UserOut, 422: ErrorOut})
def me_update(request, payload: UserPatchIn):
    return services.update_profile(request.auth, payload.dict(exclude_unset=True))


@me_router.post("/avatar", response={200: UserOut, 422: ErrorOut}, summary="Upload profile photo (PNG/JPG/GIF, ≤ 2 MB)")
def me_avatar(request, file: UploadedFile = File(...)):
    return services.set_avatar(request.auth, file)


@me_router.post("/password", response={200: MessageOut, 400: ErrorOut, 422: ErrorOut}, summary="Change password (Password Manager)")
def me_change_password(request, payload: PasswordChangeIn):
    services.change_password(request.auth, current=payload.current_password, new=payload.new_password)
    return {"detail": "Password updated. Other sessions have been signed out."}


@me_router.post("/terms", response={201: MessageOut})
def me_accept_terms(request, payload: TermsAcceptIn):
    services.accept_terms(request.auth, document=payload.document, version=payload.version, ip=services.client_ip(request))
    return 201, {"detail": "Recorded"}


# --- addresses ---------------------------------------------------------------


@me_router.get("/addresses", response=list[AddressOut])
def address_list(request):
    return request.auth.addresses.order_by("-is_default", "-created_at")


@me_router.post("/addresses", response={201: AddressOut, 422: ErrorOut})
def address_create(request, payload: AddressIn):
    return 201, services.create_address(request.auth, payload.dict())


@me_router.get("/addresses/{address_id}", response={200: AddressOut, 404: ErrorOut})
def address_detail(request, address_id: UUID):
    return get_object_or_404(Address, pk=address_id, user=request.auth)


@me_router.patch("/addresses/{address_id}", response={200: AddressOut, 404: ErrorOut, 422: ErrorOut})
def address_update(request, address_id: UUID, payload: AddressPatchIn):
    address = get_object_or_404(Address, pk=address_id, user=request.auth)
    return services.update_address(address, payload.dict(exclude_unset=True))


@me_router.post("/addresses/{address_id}/default", response={200: AddressOut, 404: ErrorOut})
def address_set_default(request, address_id: UUID):
    return services.set_default_address(get_object_or_404(Address, pk=address_id, user=request.auth))


@me_router.delete("/addresses/{address_id}", response={204: None, 404: ErrorOut})
def address_delete(request, address_id: UUID):
    services.delete_address(get_object_or_404(Address, pk=address_id, user=request.auth))
    return 204, None


# --- payment methods -----------------------------------------------------------


@me_router.get("/payment-methods", response=list[PaymentMethodOut])
def payment_method_list(request):
    return request.auth.payment_methods.order_by("-is_default", "-created_at")


@me_router.post("/payment-methods", response={201: PaymentMethodOut, 422: ErrorOut})
def payment_method_create(request, payload: PaymentMethodIn):
    return 201, services.create_payment_method(request.auth, payload.dict())


@me_router.post("/payment-methods/{pm_id}/default", response={200: PaymentMethodOut, 404: ErrorOut})
def payment_method_set_default(request, pm_id: UUID):
    return services.set_default_payment_method(get_object_or_404(PaymentMethod, pk=pm_id, user=request.auth))


@me_router.delete("/payment-methods/{pm_id}", response={204: None, 404: ErrorOut})
def payment_method_delete(request, pm_id: UUID):
    services.delete_payment_method(get_object_or_404(PaymentMethod, pk=pm_id, user=request.auth))
    return 204, None
