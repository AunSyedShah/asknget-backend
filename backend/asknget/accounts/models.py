"""Identity & buyer profile: User, OTP, terms audit, addresses, tokenised payment methods.

Screens: Signup, Email/Mobile OTP, My Account (Personal Info, Manage Addresses,
Payment Methods, Password Manager, Logout).
"""

import uuid

from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.db.models import Q

from core.models import SoftDeleteModel, TimeStampedModel


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create(self, email, password, **extra):
        if not email:
            raise ValueError("Email is required")
        user = self.model(email=self.normalize_email(email), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra.update(is_staff=True, is_superuser=True, status=User.UserStatus.ACTIVE)
        return self._create(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    """Email is the login identifier. Buyers, sellers and admins share this table;
    `is_seller` gates the Seller Portal, `is_staff` gates Django admin."""

    class Gender(models.TextChoices):
        MALE = "male", "Male"
        FEMALE = "female", "Female"
        OTHER = "other", "Other"
        PREFER_NOT = "prefer_not_to_say", "Prefer not to say"

    class UserStatus(models.TextChoices):
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"
        DELETED = "deleted", "Deleted"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=20, unique=True, null=True, blank=True, help_text="E.164")

    # Personal Info screen
    full_name = models.CharField(max_length=150, blank=True)
    first_name = models.CharField(max_length=80, blank=True)
    last_name = models.CharField(max_length=80, blank=True)
    gender = models.CharField(max_length=20, choices=Gender.choices, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    profession = models.CharField(max_length=120, blank=True)
    country = models.CharField(max_length=2, blank=True, help_text="ISO 3166-1 alpha-2; filled from the request's market on sign-up")
    language = models.CharField(max_length=10, blank=True, help_text="Preferred language; blank = pick from region / Accept-Language")
    city = models.CharField(max_length=100, blank=True)
    address_line = models.CharField(max_length=255, blank=True)
    avatar = models.ImageField(upload_to="avatars/", null=True, blank=True)

    # Verification & account state ("Account Information" panel)
    email_verified_at = models.DateTimeField(null=True, blank=True)
    phone_verified_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=UserStatus.choices, default=UserStatus.ACTIVE)

    is_seller = models.BooleanField(default=False, help_text="Has started/completed seller onboarding")
    is_staff = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)  # "Member Since"
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.email

    @property
    def email_verified(self) -> bool:
        return self.email_verified_at is not None

    @property
    def phone_verified(self) -> bool:
        return self.phone_verified_at is not None


class OtpVerification(TimeStampedModel):
    """6-digit codes for email / SMS verification. Code is stored hashed."""

    class OtpChannel(models.TextChoices):
        EMAIL = "email", "Email"
        SMS = "sms", "SMS"

    class OtpPurpose(models.TextChoices):
        SIGNUP_EMAIL = "signup_email", "Signup – email"
        SIGNUP_PHONE = "signup_phone", "Signup – phone"
        PASSWORD_RESET = "password_reset", "Password reset"
        LOGIN = "login", "Login"

    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True, related_name="otps")
    channel = models.CharField(max_length=10, choices=OtpChannel.choices)
    target = models.CharField(max_length=254, help_text="Email address or E.164 phone the code was sent to")
    purpose = models.CharField(max_length=20, choices=OtpPurpose.choices)
    code_hash = models.CharField(max_length=128)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    resend_available_at = models.DateTimeField(help_text="Design shows a 45 s resend cooldown")
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta(TimeStampedModel.Meta):
        indexes = [models.Index(fields=["target", "purpose", "verified_at"])]


class RefreshToken(TimeStampedModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="refresh_tokens")
    token_hash = models.CharField(max_length=128, unique=True)
    device = models.CharField(max_length=255, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)

    @property
    def is_valid(self) -> bool:
        from django.utils import timezone

        return self.revoked_at is None and self.expires_at > timezone.now()


class TermsAcceptance(TimeStampedModel):
    """Audit trail for the four distinct checkbox agreements in the seller flow."""

    class TermsDocument(models.TextChoices):
        SELLER_TERMS = "seller_terms", "Seller Terms & Conditions"
        PRIVACY_POLICY = "privacy_policy", "Privacy Policy"
        SELLER_AGREEMENT = "seller_agreement", "Seller Agreement"
        MARKETPLACE_POLICIES = "marketplace_policies", "Marketplace Policies"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="terms_acceptances")
    document = models.CharField(max_length=30, choices=TermsDocument.choices)
    version = models.CharField(max_length=20, default="1.0")
    accepted_at = models.DateTimeField(auto_now_add=True)
    ip = models.GenericIPAddressField(null=True, blank=True)


class Address(SoftDeleteModel):
    """Saved delivery address (Manage Addresses + Add New Address popup)."""

    class AddressType(models.TextChoices):
        HOME = "home", "Home"
        OFFICE = "office", "Office"
        OTHER = "other", "Other"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="addresses")
    type = models.CharField(max_length=10, choices=AddressType.choices, default=AddressType.HOME)
    label = models.CharField(max_length=60, blank=True, help_text='Free label, e.g. "Friend\'s House"')
    full_name = models.CharField(max_length=150)
    phone = models.CharField(max_length=20)
    street_address = models.CharField(max_length=255)
    unit_floor = models.CharField(max_length=100, blank=True)
    city = models.CharField(max_length=100)
    province = models.CharField(max_length=100, blank=True)
    postal_code = models.CharField(max_length=20)
    country = models.CharField(max_length=2, default="PK")
    delivery_instructions = models.TextField(blank=True)
    is_default = models.BooleanField(default=False)

    class Meta(SoftDeleteModel.Meta):
        verbose_name_plural = "addresses"
        constraints = [
            # Only one live default address per user.
            models.UniqueConstraint(
                fields=["user"],
                condition=Q(is_default=True, deleted_at__isnull=True),
                name="uniq_default_address_per_user",
            ),
        ]

    def __str__(self):
        return f"{self.label or self.get_type_display()} – {self.street_address}, {self.city}"


class PaymentMethod(SoftDeleteModel):
    """Tokenised saved payment method. PAN / CVV never touch this table."""

    class PaymentMethodType(models.TextChoices):
        CARD = "card", "Card"
        PAYPAL = "paypal", "PayPal"
        APPLE_PAY = "apple_pay", "Apple Pay"
        GOOGLE_PAY = "google_pay", "Google Pay"
        BANK_TRANSFER = "bank_transfer", "Bank Transfer"

    class CardBrand(models.TextChoices):
        VISA = "visa", "VISA"
        MASTERCARD = "mastercard", "Mastercard"
        OTHER = "other", "Other"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="payment_methods")
    type = models.CharField(max_length=20, choices=PaymentMethodType.choices)
    card_brand = models.CharField(max_length=20, choices=CardBrand.choices, blank=True)
    card_holder_name = models.CharField(max_length=150, blank=True)
    last4 = models.CharField(max_length=4, blank=True)
    exp_month = models.PositiveSmallIntegerField(null=True, blank=True)
    exp_year = models.PositiveSmallIntegerField(null=True, blank=True)
    provider = models.CharField(max_length=40, help_text="PSP name, e.g. stripe")
    provider_token = models.CharField(max_length=255, help_text="PSP payment-method token")
    is_default = models.BooleanField(default=False)

    class Meta(SoftDeleteModel.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=Q(is_default=True, deleted_at__isnull=True),
                name="uniq_default_payment_method_per_user",
            ),
            models.CheckConstraint(
                condition=Q(exp_month__isnull=True) | Q(exp_month__gte=1, exp_month__lte=12),
                name="payment_method_exp_month_range",
            ),
        ]

    def __str__(self):
        return f"{self.get_card_brand_display() or self.get_type_display()} ....{self.last4}"
