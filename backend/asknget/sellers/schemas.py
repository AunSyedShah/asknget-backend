"""Request / response schemas for seller onboarding and admin KYC review.

Step payloads are *partial by design* (autosave: "Save & Continue" may be hit
with half a form). Required-ness is enforced at submit time by
`rules.required_fields`, so a field becoming optional/required in the design is
a one-line change in `rules.py`, not a schema rewrite.
"""

from datetime import date, datetime
from typing import Literal, Optional
from uuid import UUID

from ninja import ModelSchema, Schema
from pydantic import Field, model_validator

from core.validators import EmailStr, NonEmptyStr, PastDate, PhoneStr, PostalCode

from .models import SellerApplication, SellerApplicationDirector, SellerDocument, SellerInfoRequestItem, Store
from . import rules

AccountTypeLit = Literal["individual", "sole_proprietor", "registered_company"]
BusinessTypeLit = Literal["private_limited", "public_limited", "partnership", "sole_proprietorship", "ngo", "other"]
RoleLit = Literal["owner", "director", "partner", "manager", "authorised_signatory", "other"]
BankAccountTypeLit = Literal["business", "personal", "savings", "current"]
DocumentTypeLit = Literal["id_passport", "company_registration", "bank_confirmation", "proof_of_address", "tax_registration", "business_licence", "other"]

# --------------------------------------------------------------------------- #
# Step payloads (seller)
# --------------------------------------------------------------------------- #


class BasicInfoIn(Schema):
    """Step 1 — 'Tell us about yourself' + 'Residential Address'."""

    first_name: Optional[NonEmptyStr] = Field(None, max_length=80)
    last_name: Optional[NonEmptyStr] = Field(None, max_length=80)
    date_of_birth: Optional[PastDate] = None
    id_passport_number: Optional[NonEmptyStr] = Field(None, max_length=40)
    phone: Optional[PhoneStr] = None
    email: Optional[EmailStr] = None
    residential_address: Optional[NonEmptyStr] = Field(None, max_length=255)
    residential_postal_code: Optional[PostalCode] = None
    residential_city: Optional[NonEmptyStr] = Field(None, max_length=100)
    residential_province: Optional[NonEmptyStr] = Field(None, max_length=100)


class BusinessInfoIn(Schema):
    """Step 2 — 'Tell us about your business'."""

    legal_business_name: Optional[NonEmptyStr] = Field(None, max_length=200)
    store_display_name: Optional[NonEmptyStr] = Field(None, max_length=120)
    business_type: Optional[BusinessTypeLit] = None
    company_registration_number: Optional[NonEmptyStr] = Field(None, max_length=60)
    vat_number: Optional[str] = Field(None, max_length=40, description="Optional in the design")
    business_email: Optional[EmailStr] = None
    business_phone: Optional[PhoneStr] = None
    business_address: Optional[NonEmptyStr] = Field(None, max_length=255)
    business_city: Optional[NonEmptyStr] = Field(None, max_length=100)
    business_province: Optional[NonEmptyStr] = Field(None, max_length=100)
    business_postal_code: Optional[PostalCode] = None
    pickup_same_as_business: Optional[bool] = None
    pickup_address: Optional[str] = Field(None, max_length=255)
    returns_same_as_business: Optional[bool] = None
    returns_address: Optional[str] = Field(None, max_length=255)
    store_description: Optional[NonEmptyStr] = Field(None, max_length=250, description="'0 / 250 characters'")
    category_ids: Optional[list[UUID]] = Field(None, min_length=1, max_length=10, description="'Select the categories you intend to sell'")

    @model_validator(mode="after")
    def _addresses_when_not_same(self):
        if self.pickup_same_as_business is False and not self.pickup_address:
            raise ValueError("pickup_address is required when it differs from the business address")
        if self.returns_same_as_business is False and not self.returns_address:
            raise ValueError("returns_address is required when it differs from the business address")
        return self


class RepresentativeIn(Schema):
    """Step 3 — 'Who represents your business?'."""

    rep_first_name: Optional[NonEmptyStr] = Field(None, max_length=80)
    rep_last_name: Optional[NonEmptyStr] = Field(None, max_length=80)
    rep_role: Optional[RoleLit] = None
    rep_id_passport_number: Optional[NonEmptyStr] = Field(None, max_length=40)
    rep_mobile: Optional[PhoneStr] = None
    rep_email: Optional[EmailStr] = None


class BankingIn(Schema):
    """Step 4 — 'Set up your payout account'."""

    bank_id: Optional[UUID] = None
    bank_account_type: Optional[BankAccountTypeLit] = None
    account_holder_name: Optional[NonEmptyStr] = Field(None, max_length=150)
    account_number: Optional[str] = Field(None, min_length=6, max_length=34, pattern=r"^[0-9A-Za-z]+$")
    branch_code: Optional[str] = Field(None, min_length=3, max_length=20, pattern=r"^[0-9A-Za-z-]+$")
    tax_reference_number: Optional[str] = Field(None, max_length=40, description="Optional in the design")


class DirectorIn(Schema):
    """'+ Add Another Director'."""

    first_name: NonEmptyStr = Field(max_length=80)
    last_name: NonEmptyStr = Field(max_length=80)
    role: Optional[RoleLit] = None
    id_passport_number: Optional[NonEmptyStr] = Field(None, max_length=40)
    email: Optional[EmailStr] = None
    phone: Optional[PhoneStr] = None


class SubmitIn(Schema):
    """Step 5 — the two checkboxes on Review & Submit."""

    confirm_accuracy: bool = Field(description="'I confirm that the information provided is accurate and complete.'")
    accept_terms: bool = Field(description="'I agree to the asknget Seller Terms & Conditions and Privacy Policy.'")

    @model_validator(mode="after")
    def _both_required(self):
        if not (self.confirm_accuracy and self.accept_terms):
            raise ValueError("Both confirmations are required to submit")
        return self


class ResubmitIn(Schema):
    confirm_accuracy: bool = Field(description="'I confirm that the updated information is accurate.'")

    @model_validator(mode="after")
    def _required(self):
        if not self.confirm_accuracy:
            raise ValueError("You must confirm the updated information is accurate")
        return self


class StoreActivateIn(Schema):
    """'Activate Your Seller Account' — Store Setup."""

    display_name: Optional[NonEmptyStr] = Field(None, max_length=120, description="Defaults to the application's store name")
    slug: Optional[str] = Field(None, min_length=3, max_length=140, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", description="asknget.com/store/<slug>; generated from display_name when omitted")
    accept_seller_agreement: bool = Field(description="'I agree to the asknget Seller Agreement and marketplace policies.'")
    confirm_info_accurate: bool = Field(description="'I confirm that my business information remains accurate and up to date.'")

    @model_validator(mode="after")
    def _both_required(self):
        if not (self.accept_seller_agreement and self.confirm_info_accurate):
            raise ValueError("Both confirmations are required to activate your store")
        return self


# --------------------------------------------------------------------------- #
# Responses (seller)
# --------------------------------------------------------------------------- #


class DocumentOut(ModelSchema):
    url: Optional[str] = None

    class Meta:
        model = SellerDocument
        fields = ["id", "type", "file_name", "mime_type", "size_bytes", "version", "status", "reviewer_note", "created_at"]

    @staticmethod
    def resolve_url(obj):
        from .files import document_url

        return document_url(obj) if obj.file else None      # signed and expiring; never the /media/ path


class DirectorOut(ModelSchema):
    class Meta:
        model = SellerApplicationDirector
        fields = ["id", "first_name", "last_name", "role", "email", "phone", "sort_order"]


class StepStateOut(Schema):
    step: int
    name: str
    required: bool = Field(description="False when this account type skips the step")
    complete: bool
    missing_fields: list[str]
    missing_documents: list[str]


class BasicInfoOut(Schema):
    first_name: str
    last_name: str
    date_of_birth: Optional[date]
    id_passport_number_masked: str
    phone: str
    email: str
    residential_address: str
    residential_postal_code: str
    residential_city: str
    residential_province: str


class CategoryRefOut(Schema):
    id: UUID
    name: str
    slug: str


class BusinessInfoOut(Schema):
    legal_business_name: str
    store_display_name: str
    business_type: str
    company_registration_number: str
    vat_number: str
    business_email: str
    business_phone: str
    business_address: str
    business_city: str
    business_province: str
    business_postal_code: str
    pickup_same_as_business: bool
    pickup_address: str
    returns_same_as_business: bool
    returns_address: str
    store_description: str
    categories: list[CategoryRefOut]


class RepresentativeOut(Schema):
    rep_first_name: str
    rep_last_name: str
    rep_role: str
    rep_id_passport_number_masked: str
    rep_mobile: str
    rep_email: str


class BankRefOut(Schema):
    id: UUID
    name: str
    country: str


class BankingOut(Schema):
    bank: Optional[BankRefOut]
    bank_account_type: str
    account_holder_name: str
    account_number_last4: str
    branch_code: str
    tax_reference_number: str


class InfoRequestItemOut(ModelSchema):
    resolved: bool

    class Meta:
        model = SellerInfoRequestItem
        fields = ["id", "kind", "target", "instruction", "sort_order"]

    @staticmethod
    def resolve_resolved(obj):
        return obj.resolved_at is not None


class InfoRequestOut(Schema):
    id: UUID
    message: str
    created_at: datetime
    resolved_at: Optional[datetime]
    items: list[InfoRequestItemOut]


class StoreOut(ModelSchema):
    url: str
    logo_url: Optional[str] = None
    banner_url: Optional[str] = None

    class Meta:
        model = Store
        fields = ["id", "display_name", "slug", "description", "status", "activated_at", "created_at"]

    @staticmethod
    def resolve_url(obj):
        return f"asknget.com/store/{obj.slug}"

    @staticmethod
    def resolve_logo_url(obj):
        return obj.logo.url if obj.logo else None

    @staticmethod
    def resolve_banner_url(obj):
        return obj.banner.url if obj.banner else None


class RequiredDocumentOut(Schema):
    type: str
    label: str = Field(description="Country-specific, in the request language")
    step: int


class KycPolicyOut(Schema):
    """The country's KYC rules for this application: what to upload, how fields are labelled."""

    country: Optional[str]
    mode: str
    required_documents: list[RequiredDocumentOut]
    document_labels: dict[str, str]
    id_number_label: str
    id_number_regex: str
    tax_id_label: str
    company_registration_label: str
    instructions: str


class ApplicationOut(Schema):
    """Everything the 5-step wizard and the Review & Submit page need."""

    id: UUID
    application_number: Optional[str] = Field(None, description="Assigned on submit")
    country: str = Field(alias="country_id", description="Market the seller applies to")
    policy: KycPolicyOut
    account_type: str
    status: str
    status_headline: str
    editable: bool = Field(description="Seller may still change steps / upload documents")
    current_step: int
    steps: list[StepStateOut]
    can_submit: bool
    basic_info: BasicInfoOut
    business_info: BusinessInfoOut
    representative: RepresentativeOut
    banking: BankingOut
    directors: list[DirectorOut]
    documents: list[DocumentOut] = Field(description="Latest version of each document type")
    open_info_request: Optional[InfoRequestOut]
    reviewer_message: str
    rejection_reason: str
    submitted_at: Optional[datetime]
    approved_at: Optional[datetime]
    rejected_at: Optional[datetime]
    activated_at: Optional[datetime]
    store: Optional[StoreOut]
    created_at: datetime
    updated_at: datetime


class TimelineStageOut(Schema):
    stage: str
    state: Literal["done", "current", "pending", "skipped"]
    at: Optional[datetime]


class StatusEventOut(Schema):
    from_status: str
    to_status: str
    note: str
    created_at: datetime


class ApplicationStatusOut(Schema):
    """'Application Status' / 'Application Submitted' / 'More Info Required' screens."""

    application_number: Optional[str]
    status: str
    status_headline: str
    account_type: str
    store_display_name: str
    submitted_at: Optional[datetime]
    timeline: list[TimelineStageOut]
    events: list[StatusEventOut]
    open_info_request: Optional[InfoRequestOut]
    reviewer_message: str
    rejection_reason: str
    store: Optional[StoreOut]


class SlugAvailableOut(Schema):
    slug: str
    available: bool
    suggestion: str = Field(description="A free slug (same as `slug` when available)")


class ChoiceOut(Schema):
    value: str
    label: str


class SellerLookupsOut(Schema):
    account_types: list[ChoiceOut]
    business_types: list[ChoiceOut]
    representative_roles: list[ChoiceOut]
    bank_account_types: list[ChoiceOut]
    document_types: list[ChoiceOut] = Field(description="Every type the platform knows; `policy` says which this country requires")
    field_groups: list[ChoiceOut]
    document_max_bytes: int
    document_allowed_types: list[str]
    policy: KycPolicyOut = Field(description="Rules for ?country= (default: the request's market) and ?account_type=")


class BankOut(Schema):
    id: UUID
    name: str
    country: str
    swift: str


# --------------------------------------------------------------------------- #
# Admin review
# --------------------------------------------------------------------------- #


class AdminApplicantOut(Schema):
    id: UUID
    email: str
    phone: Optional[str]
    full_name: str


class AdminApplicationListOut(Schema):
    id: UUID
    application_number: Optional[str]
    country: str
    account_type: str
    status: str
    store_display_name: str
    legal_business_name: str
    applicant: AdminApplicantOut
    submitted_at: Optional[datetime]
    updated_at: datetime


class AdminBasicInfoOut(BasicInfoOut):
    id_passport_number: str


class AdminRepresentativeOut(RepresentativeOut):
    rep_id_passport_number: str


class AdminBankingOut(BankingOut):
    account_number: str


class AdminDirectorOut(DirectorOut):
    id_passport_number: str


class AdminApplicationOut(ApplicationOut):
    """Admin does KYC, so sees the decrypted identifiers the seller only sees masked."""

    applicant: AdminApplicantOut
    basic_info: AdminBasicInfoOut
    representative: AdminRepresentativeOut
    banking: AdminBankingOut
    directors: list[AdminDirectorOut]
    all_documents: list[DocumentOut] = Field(description="Every version, newest first")
    info_requests: list[InfoRequestOut]
    events: list[StatusEventOut]
    reviewer_email: Optional[str]


class InfoRequestItemIn(Schema):
    kind: Literal["document", "field_group"]
    target: str = Field(description="Document type (kind=document) or field-group key (kind=field_group)")
    instruction: NonEmptyStr = Field(max_length=255)

    @model_validator(mode="after")
    def _target_known(self):
        valid = set(rules.DocType.values) if self.kind == "document" else set(rules.FIELD_GROUPS)
        if self.target not in valid:
            raise ValueError(f"target must be one of: {', '.join(sorted(valid))}")
        return self


class RequestInfoIn(Schema):
    """'We Need More Information' — reviewer message + the 'Required Updates' list."""

    message: NonEmptyStr = Field(max_length=2000)
    items: list[InfoRequestItemIn] = Field(min_length=1, max_length=20)


class RejectIn(Schema):
    reason: NonEmptyStr = Field(max_length=2000)


class ApproveIn(Schema):
    note: str = Field("", max_length=1000)


class DocumentReviewIn(Schema):
    status: Literal["accepted", "rejected", "replacement_requested"]
    note: str = Field("", max_length=500)
