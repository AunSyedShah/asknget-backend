"""Request / response schemas for the accounts API.

Conventions (see backend/README.md):
- `*In`     create payloads — every required field is required.
- `*PatchIn` partial updates — everything Optional; the view applies
             `payload.dict(exclude_unset=True)` so omitted ≠ null.
- `*Out`    responses — explicit field lists, never "__all__".
Enums reuse the model TextChoices so DB, validation and Swagger stay in sync.
"""

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from ninja import ModelSchema, Schema
from pydantic import Field, model_validator

from core.validators import (
    CountryCode,
    EmailStr,
    Last4,
    NonEmptyStr,
    OtpCode,
    PasswordStr,
    PastDate,
    PhoneStr,
    PostalCode,
)

from .models import Address, OtpVerification, PaymentMethod, TermsAcceptance, User

# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #


class TokenOut(Schema):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(description="Access-token lifetime in seconds")


class UserOut(ModelSchema):
    """Personal Info screen + 'Account Information' panel."""

    email_verified: bool
    phone_verified: bool
    member_since: datetime = Field(alias="created_at")
    avatar_url: Optional[str] = None

    class Meta:
        model = User
        fields = [
            "id", "email", "phone", "full_name", "first_name", "last_name", "gender",
            "date_of_birth", "profession", "country", "city", "address_line",
            "status", "is_seller", "is_staff",
        ]

    @staticmethod
    def resolve_avatar_url(obj: User):
        return obj.avatar.url if obj.avatar else None


class AuthOut(Schema):
    user: UserOut
    tokens: TokenOut


class _PasswordPair(Schema):
    password: PasswordStr
    confirm_password: str

    @model_validator(mode="after")
    def _passwords_match(self):
        if self.password != self.confirm_password:
            raise ValueError("Passwords do not match")
        return self


class RegisterIn(_PasswordPair):
    """Buyer signup. (No buyer-signup screen in Figma; mirrors the seller one minus account type.)"""

    email: EmailStr
    phone: Optional[PhoneStr] = None
    full_name: Optional[NonEmptyStr] = Field(None, max_length=150)


class SellerRegisterIn(_PasswordPair):
    """'Signup – Create Account' (seller). Creates the user + a draft SellerApplication."""

    email: EmailStr
    phone: PhoneStr
    account_type: Literal["individual", "sole_proprietor", "registered_company"]
    accept_terms: bool = Field(description="'I agree to the asknget Seller Terms & Conditions and Privacy Policy.'")

    @model_validator(mode="after")
    def _terms_required(self):
        if not self.accept_terms:
            raise ValueError("You must accept the Seller Terms & Conditions and Privacy Policy")
        return self


class LoginIn(Schema):
    email: EmailStr
    password: str = Field(min_length=1)


class RefreshIn(Schema):
    refresh_token: str = Field(min_length=20)


class LogoutIn(Schema):
    refresh_token: Optional[str] = Field(None, description="Revoke this token; omit to revoke all sessions")
    all_devices: bool = False


# --------------------------------------------------------------------------- #
# OTP
# --------------------------------------------------------------------------- #


class OtpSendIn(Schema):
    channel: OtpVerification.OtpChannel
    purpose: OtpVerification.OtpPurpose
    target: Optional[str] = Field(
        None,
        description="Email or E.164 phone. Optional when authenticated (defaults to the user's own).",
    )

    @model_validator(mode="after")
    def _target_matches_channel(self):
        if self.target is None:
            return self
        if self.channel == OtpVerification.OtpChannel.EMAIL:
            self.target = self.target.strip().lower()
            if "@" not in self.target:
                raise ValueError("target must be an email address for the email channel")
        else:
            from core.validators import _normalize_phone

            self.target = _normalize_phone(self.target)
        return self


class OtpSendOut(Schema):
    target_masked: str = Field(description="e.g. j***@gmail.com or +92*******567")
    expires_at: datetime
    resend_available_at: datetime


class OtpVerifyIn(Schema):
    channel: OtpVerification.OtpChannel
    purpose: OtpVerification.OtpPurpose
    target: str
    code: OtpCode


# --------------------------------------------------------------------------- #
# Password
# --------------------------------------------------------------------------- #


class PasswordChangeIn(Schema):
    """Password Manager screen."""

    current_password: str = Field(min_length=1)
    new_password: PasswordStr
    confirm_password: str

    @model_validator(mode="after")
    def _check(self):
        if self.new_password != self.confirm_password:
            raise ValueError("Passwords do not match")
        if self.new_password == self.current_password:
            raise ValueError("New password must differ from the current password")
        return self


class PasswordForgotIn(Schema):
    email: EmailStr


class PasswordResetIn(Schema):
    email: EmailStr
    code: OtpCode
    new_password: PasswordStr
    confirm_password: str

    @model_validator(mode="after")
    def _match(self):
        if self.new_password != self.confirm_password:
            raise ValueError("Passwords do not match")
        return self


# --------------------------------------------------------------------------- #
# Profile
# --------------------------------------------------------------------------- #


class UserPatchIn(Schema):
    """PATCH /me — Personal Details form. Email/phone changes go through OTP, not here."""

    full_name: Optional[NonEmptyStr] = Field(None, max_length=150)
    first_name: Optional[str] = Field(None, max_length=80)
    last_name: Optional[str] = Field(None, max_length=80)
    gender: Optional[User.Gender] = None
    date_of_birth: Optional[PastDate] = None
    profession: Optional[str] = Field(None, max_length=120)
    country: Optional[CountryCode] = None
    city: Optional[str] = Field(None, max_length=100)
    address_line: Optional[str] = Field(None, max_length=255)


class TermsAcceptIn(Schema):
    document: TermsAcceptance.TermsDocument
    version: str = Field("1.0", max_length=20)


# --------------------------------------------------------------------------- #
# Addresses
# --------------------------------------------------------------------------- #


class AddressIn(Schema):
    """'Add New Address' popup."""

    type: Address.AddressType = Address.AddressType.HOME
    label: Optional[str] = Field(None, max_length=60, description='Custom label, e.g. "Friend\'s House"')
    full_name: NonEmptyStr = Field(max_length=150)
    phone: PhoneStr
    street_address: NonEmptyStr = Field(max_length=255)
    unit_floor: Optional[str] = Field(None, max_length=100)
    city: NonEmptyStr = Field(max_length=100)
    province: Optional[str] = Field(None, max_length=100)
    postal_code: PostalCode
    country: CountryCode = "PK"
    delivery_instructions: Optional[str] = Field(None, max_length=500)
    is_default: bool = False


class AddressPatchIn(Schema):
    type: Optional[Address.AddressType] = None
    label: Optional[str] = Field(None, max_length=60)
    full_name: Optional[NonEmptyStr] = Field(None, max_length=150)
    phone: Optional[PhoneStr] = None
    street_address: Optional[NonEmptyStr] = Field(None, max_length=255)
    unit_floor: Optional[str] = Field(None, max_length=100)
    city: Optional[NonEmptyStr] = Field(None, max_length=100)
    province: Optional[str] = Field(None, max_length=100)
    postal_code: Optional[PostalCode] = None
    country: Optional[CountryCode] = None
    delivery_instructions: Optional[str] = Field(None, max_length=500)
    is_default: Optional[bool] = None


class AddressOut(ModelSchema):
    class Meta:
        model = Address
        fields = [
            "id", "type", "label", "full_name", "phone", "street_address", "unit_floor",
            "city", "province", "postal_code", "country", "delivery_instructions", "is_default",
            "created_at", "updated_at",
        ]


# --------------------------------------------------------------------------- #
# Payment methods (tokenised — the PSP token comes from the frontend SDK)
# --------------------------------------------------------------------------- #


class PaymentMethodIn(Schema):
    """'Add New Card' popup. The raw card number / CVV never reach this API:
    the frontend tokenises with the PSP and sends the token + display data."""

    type: PaymentMethod.PaymentMethodType
    provider: NonEmptyStr = Field(max_length=40, examples=["stripe"])
    provider_token: NonEmptyStr = Field(max_length=255)
    card_brand: Optional[PaymentMethod.CardBrand] = None
    card_holder_name: Optional[str] = Field(None, max_length=150)
    last4: Optional[Last4] = None
    exp_month: Optional[int] = Field(None, ge=1, le=12)
    exp_year: Optional[int] = Field(None, ge=2000, le=2100)
    is_default: bool = False

    @model_validator(mode="after")
    def _card_fields(self):
        if self.type == PaymentMethod.PaymentMethodType.CARD:
            missing = [f for f in ("card_brand", "card_holder_name", "last4", "exp_month", "exp_year") if getattr(self, f) is None]
            if missing:
                raise ValueError(f"Card payment methods require: {', '.join(missing)}")
            today = datetime.now()
            if (self.exp_year, self.exp_month) < (today.year, today.month):
                raise ValueError("Card has expired")
        return self


class PaymentMethodOut(ModelSchema):
    display_name: str

    class Meta:
        model = PaymentMethod
        fields = ["id", "type", "card_brand", "card_holder_name", "last4", "exp_month", "exp_year", "provider", "is_default", "created_at"]

    @staticmethod
    def resolve_display_name(obj: PaymentMethod):
        return str(obj)


class SetDefaultOut(Schema):
    id: UUID
    is_default: bool = True
