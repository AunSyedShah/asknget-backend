"""Seller onboarding (KYC application) and stores.

Screens: Signup – Create Account, Basic Info, Business Info, Representative,
Banking Info, Review & Submit, Application Submitted / Status / More Info
Required / Update Application, Application Approved, Activate Seller Account.
"""

from django.conf import settings
from django.db import models

from core.fields import EncryptedTextField
from core.models import TimeStampedModel


def seller_document_path(instance, filename):
    return f"seller_documents/{instance.application_id}/{instance.type}/{filename}"


class SellerApplication(TimeStampedModel):
    """One application per user. Steps 1–4 are autosaved ("Save & Continue"),
    then submitted for review."""

    class AccountType(models.TextChoices):
        INDIVIDUAL = "individual", "Individual"
        SOLE_PROPRIETOR = "sole_proprietor", "Sole Proprietor"
        REGISTERED_COMPANY = "registered_company", "Registered Company"

    class ApplicationStatus(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        UNDER_REVIEW = "under_review", "Under Review"
        MORE_INFO_REQUIRED = "more_info_required", "More Info Required"
        RESUBMITTED = "resubmitted", "Resubmitted"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        ACTIVE = "active", "Active"

    class BusinessType(models.TextChoices):
        # TODO(design): option list not in Figma — confirm with team lead.
        PRIVATE_LIMITED = "private_limited", "Private Limited"
        PUBLIC_LIMITED = "public_limited", "Public Limited"
        PARTNERSHIP = "partnership", "Partnership"
        SOLE_PROPRIETORSHIP = "sole_proprietorship", "Sole Proprietorship"
        NGO = "ngo", "NGO / Non-profit"
        OTHER = "other", "Other"

    class RepresentativeRole(models.TextChoices):
        # TODO(design): option list not in Figma — "Director" is the only value shown.
        OWNER = "owner", "Owner"
        DIRECTOR = "director", "Director"
        PARTNER = "partner", "Partner"
        MANAGER = "manager", "Manager"
        AUTHORISED_SIGNATORY = "authorised_signatory", "Authorised Signatory"
        OTHER = "other", "Other"

    class BankAccountType(models.TextChoices):
        # TODO(design): option list not in Figma — "Business Account" is the only value shown.
        BUSINESS = "business", "Business Account"
        PERSONAL = "personal", "Personal Account"
        SAVINGS = "savings", "Savings Account"
        CURRENT = "current", "Current / Cheque Account"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="seller_application")
    application_number = models.CharField(max_length=20, unique=True, null=True, blank=True, help_text="Assigned on submit, e.g. ASK-SLR-004821")
    account_type = models.CharField(max_length=20, choices=AccountType.choices)
    status = models.CharField(max_length=20, choices=ApplicationStatus.choices, default=ApplicationStatus.DRAFT, db_index=True)
    current_step = models.PositiveSmallIntegerField(default=1, help_text="1 Basic · 2 Business · 3 Representative · 4 Banking · 5 Review")

    # --- Step 1: Basic information --------------------------------------
    first_name = models.CharField(max_length=80, blank=True)
    last_name = models.CharField(max_length=80, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    id_passport_number = EncryptedTextField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    residential_address = models.CharField(max_length=255, blank=True)
    residential_postal_code = models.CharField(max_length=20, blank=True)
    residential_city = models.CharField(max_length=100, blank=True)
    residential_province = models.CharField(max_length=100, blank=True)

    # --- Step 2: Business information -----------------------------------
    legal_business_name = models.CharField(max_length=200, blank=True)
    store_display_name = models.CharField(max_length=120, blank=True)
    business_type = models.CharField(max_length=30, choices=BusinessType.choices, blank=True)
    company_registration_number = models.CharField(max_length=60, blank=True)
    vat_number = models.CharField(max_length=40, blank=True)
    business_email = models.EmailField(blank=True)
    business_phone = models.CharField(max_length=20, blank=True)
    business_address = models.CharField(max_length=255, blank=True)
    business_city = models.CharField(max_length=100, blank=True)
    business_province = models.CharField(max_length=100, blank=True)
    business_postal_code = models.CharField(max_length=20, blank=True)
    pickup_same_as_business = models.BooleanField(default=True)
    pickup_address = models.CharField(max_length=255, blank=True)
    returns_same_as_business = models.BooleanField(default=True)
    returns_address = models.CharField(max_length=255, blank=True)
    store_description = models.CharField(max_length=250, blank=True)
    categories = models.ManyToManyField("catalog.Category", blank=True, related_name="seller_applications")

    # --- Step 3: Authorised representative ------------------------------
    rep_first_name = models.CharField(max_length=80, blank=True)
    rep_last_name = models.CharField(max_length=80, blank=True)
    rep_role = models.CharField(max_length=30, choices=RepresentativeRole.choices, blank=True)
    rep_id_passport_number = EncryptedTextField(blank=True)
    rep_mobile = models.CharField(max_length=20, blank=True)
    rep_email = models.EmailField(blank=True)

    # --- Step 4: Banking -------------------------------------------------
    bank = models.ForeignKey("core.Bank", on_delete=models.PROTECT, null=True, blank=True)
    bank_account_type = models.CharField(max_length=20, choices=BankAccountType.choices, blank=True)
    account_holder_name = models.CharField(max_length=150, blank=True)
    account_number = EncryptedTextField(blank=True)
    account_number_last4 = models.CharField(max_length=4, blank=True)
    branch_code = models.CharField(max_length=20, blank=True)
    tax_reference_number = models.CharField(max_length=40, blank=True)

    # --- Step 5: Review & submit ----------------------------------------
    info_accuracy_confirmed_at = models.DateTimeField(null=True, blank=True)
    terms_accepted_at = models.DateTimeField(null=True, blank=True)

    # --- Workflow --------------------------------------------------------
    submitted_at = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="reviewed_seller_applications"
    )
    reviewer_message = models.TextField(blank=True, help_text='Shown in the "More Info Required" banner')
    rejection_reason = models.TextField(blank=True)

    class Meta(TimeStampedModel.Meta):
        pass

    def __str__(self):
        return f"{self.application_number or 'draft'} – {self.store_display_name or self.user_id}"

    def save(self, *args, **kwargs):
        if self.account_number and not self.account_number_last4:
            self.account_number_last4 = str(self.account_number)[-4:]
        super().save(*args, **kwargs)


class SellerApplicationDirector(TimeStampedModel):
    """'Additional Directors (if any) → + Add Another Director'."""

    application = models.ForeignKey(SellerApplication, on_delete=models.CASCADE, related_name="directors")
    first_name = models.CharField(max_length=80)
    last_name = models.CharField(max_length=80)
    role = models.CharField(max_length=30, choices=SellerApplication.RepresentativeRole.choices, blank=True)
    id_passport_number = EncryptedTextField(blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta(TimeStampedModel.Meta):
        ordering = ["sort_order", "created_at"]


class SellerDocument(TimeStampedModel):
    """Uploaded KYC documents. Replacements create a new row; the old one is
    linked via `superseded_by` so the review history is preserved."""

    class DocumentType(models.TextChoices):
        ID_PASSPORT = "id_passport", "ID / Passport"
        COMPANY_REGISTRATION = "company_registration", "Company Registration"
        BANK_CONFIRMATION = "bank_confirmation", "Bank Confirmation"
        PROOF_OF_ADDRESS = "proof_of_address", "Proof of Address"

    class DocumentStatus(models.TextChoices):
        UPLOADED = "uploaded", "Uploaded"
        ACCEPTED = "accepted", "Accepted"
        REJECTED = "rejected", "Rejected"
        REPLACEMENT_REQUESTED = "replacement_requested", "Replacement Requested"

    application = models.ForeignKey(SellerApplication, on_delete=models.CASCADE, related_name="documents")
    type = models.CharField(max_length=30, choices=DocumentType.choices)
    file = models.FileField(upload_to=seller_document_path, help_text="PDF, JPG or PNG · max 10 MB")
    file_name = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=100)
    size_bytes = models.PositiveIntegerField()
    version = models.PositiveSmallIntegerField(default=1)
    status = models.CharField(max_length=30, choices=DocumentStatus.choices, default=DocumentStatus.UPLOADED)
    reviewer_note = models.TextField(blank=True)
    superseded_by = models.OneToOneField("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="supersedes")

    class Meta(TimeStampedModel.Meta):
        indexes = [models.Index(fields=["application", "type", "version"])]

    def __str__(self):
        return f"{self.get_type_display()} v{self.version} – {self.file_name}"


class SellerInfoRequest(TimeStampedModel):
    """A reviewer's 'More Info Required' round. Items list what must change."""

    application = models.ForeignKey(SellerApplication, on_delete=models.CASCADE, related_name="info_requests")
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    message = models.TextField()
    resolved_at = models.DateTimeField(null=True, blank=True)


class SellerInfoRequestItem(TimeStampedModel):
    class InfoRequestKind(models.TextChoices):
        DOCUMENT = "document", "Document"
        FIELD_GROUP = "field_group", "Field group"

    request = models.ForeignKey(SellerInfoRequest, on_delete=models.CASCADE, related_name="items")
    kind = models.CharField(max_length=20, choices=InfoRequestKind.choices)
    target = models.CharField(
        max_length=60,
        help_text="SellerDocument.DocumentType value for documents, or a field-group key such as 'business_address'",
    )
    instruction = models.CharField(max_length=255)
    sort_order = models.PositiveSmallIntegerField(default=0)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta(TimeStampedModel.Meta):
        ordering = ["sort_order", "created_at"]


class SellerApplicationStatusEvent(TimeStampedModel):
    """Drives the 'Application Progress' timeline."""

    application = models.ForeignKey(SellerApplication, on_delete=models.CASCADE, related_name="status_events")
    from_status = models.CharField(max_length=20, choices=SellerApplication.ApplicationStatus.choices, blank=True)
    to_status = models.CharField(max_length=20, choices=SellerApplication.ApplicationStatus.choices)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    note = models.TextField(blank=True)

    class Meta(TimeStampedModel.Meta):
        ordering = ["created_at"]


class Store(TimeStampedModel):
    """Public storefront. Created on approval, activated on 'Activate & Start Selling'."""

    class StoreStatus(models.TextChoices):
        PENDING_ACTIVATION = "pending_activation", "Pending Activation"
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"
        CLOSED = "closed", "Closed"

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="stores")
    application = models.OneToOneField(SellerApplication, on_delete=models.PROTECT, related_name="store")
    display_name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140, unique=True, help_text="asknget.com/store/<slug>")
    description = models.CharField(max_length=250, blank=True)
    logo = models.ImageField(upload_to="stores/logos/", null=True, blank=True)
    banner = models.ImageField(upload_to="stores/banners/", null=True, blank=True)
    status = models.CharField(max_length=20, choices=StoreStatus.choices, default=StoreStatus.PENDING_ACTIVATION, db_index=True)
    seller_agreement_accepted_at = models.DateTimeField(null=True, blank=True)
    low_stock_threshold = models.PositiveSmallIntegerField(default=10, help_text="'Low Stock' when 0 < stock <= this (per-product override allowed)")
    info_reconfirmed_at = models.DateTimeField(null=True, blank=True)
    activated_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.display_name
