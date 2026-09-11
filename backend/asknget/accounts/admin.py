from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import Address, OtpVerification, PaymentMethod, RefreshToken, TermsAcceptance, User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    ordering = ("-created_at",)
    list_display = ("email", "full_name", "phone", "status", "is_seller", "is_staff", "created_at")
    list_filter = ("status", "is_seller", "is_staff", "is_superuser")
    search_fields = ("email", "full_name", "phone")
    readonly_fields = ("created_at", "updated_at", "last_login")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Profile", {"fields": ("full_name", "first_name", "last_name", "phone", "gender", "date_of_birth",
                                "profession", "country", "city", "address_line", "avatar")}),
        ("Verification", {"fields": ("email_verified_at", "phone_verified_at", "status")}),
        ("Permissions", {"fields": ("is_active", "is_seller", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Dates", {"fields": ("last_login", "created_at", "updated_at")}),
    )
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("email", "password1", "password2")}),)


@admin.register(OtpVerification)
class OtpVerificationAdmin(admin.ModelAdmin):
    list_display = ("target", "channel", "purpose", "attempts", "expires_at", "verified_at")
    list_filter = ("channel", "purpose")
    search_fields = ("target",)


@admin.register(RefreshToken)
class RefreshTokenAdmin(admin.ModelAdmin):
    list_display = ("user", "device", "ip", "expires_at", "revoked_at")
    search_fields = ("user__email",)


@admin.register(TermsAcceptance)
class TermsAcceptanceAdmin(admin.ModelAdmin):
    list_display = ("user", "document", "version", "accepted_at")
    list_filter = ("document",)


@admin.register(Address)
class AddressAdmin(admin.ModelAdmin):
    list_display = ("user", "type", "label", "city", "postal_code", "is_default")
    list_filter = ("type", "is_default")
    search_fields = ("user__email", "street_address", "city")


@admin.register(PaymentMethod)
class PaymentMethodAdmin(admin.ModelAdmin):
    list_display = ("user", "type", "card_brand", "last4", "exp_month", "exp_year", "is_default")
    list_filter = ("type", "card_brand")
    search_fields = ("user__email", "last4")
