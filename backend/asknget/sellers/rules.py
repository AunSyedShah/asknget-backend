"""Onboarding rules in one place.

Everything the design is likely to change — which fields make a step
"complete", which documents each account type must upload, which status
transitions exist — is data here, not logic scattered through services.
Change a table, run the tests, ship.
"""

from .models import SellerApplication, SellerDocument

AccountType = SellerApplication.AccountType
Status = SellerApplication.ApplicationStatus
DocType = SellerDocument.DocumentType

STEP_NAMES = {1: "Basic Info", 2: "Business Info", 3: "Representative", 4: "Banking", 5: "Review"}

# Fields that must be non-empty for a step to count as complete. Optional
# inputs from the design (VAT number, tax reference, pickup/returns when
# "same as business") are deliberately absent.
STEP_REQUIRED_FIELDS: dict[int, tuple[str, ...]] = {
    1: (
        "first_name", "last_name", "date_of_birth", "id_passport_number", "phone", "email",
        "residential_address", "residential_postal_code", "residential_city", "residential_province",
    ),
    2: (
        "legal_business_name", "store_display_name", "business_type", "company_registration_number",
        "business_email", "business_phone", "business_address", "business_city", "business_province",
        "business_postal_code", "store_description",
    ),
    3: ("rep_first_name", "rep_last_name", "rep_role", "rep_id_passport_number", "rep_mobile", "rep_email"),
    4: ("bank", "bank_account_type", "account_holder_name", "account_number", "branch_code"),
}

# Field-group keys a reviewer can point at in an info request ("Update your
# business address"). The Update Application screen renders one card per key.
FIELD_GROUPS: dict[str, tuple[str, ...]] = {
    "personal_info": STEP_REQUIRED_FIELDS[1][:6],
    "residential_address": ("residential_address", "residential_postal_code", "residential_city", "residential_province"),
    "business_info": ("legal_business_name", "store_display_name", "business_type", "company_registration_number", "vat_number"),
    "business_address": ("business_address", "business_city", "business_province", "business_postal_code"),
    "business_contact": ("business_email", "business_phone"),
    "representative": STEP_REQUIRED_FIELDS[3],
    "banking": STEP_REQUIRED_FIELDS[4] + ("tax_reference_number",),
}
FIELD_GROUP_STEP = {
    "personal_info": 1, "residential_address": 1,
    "business_info": 2, "business_address": 2, "business_contact": 2,
    "representative": 3, "banking": 4,
}

# Steps that must be complete before submit, per account type.
# TODO(design): §10 Q7 — assumes an individual seller has no company/representative.
REQUIRED_STEPS: dict[str, tuple[int, ...]] = {
    AccountType.INDIVIDUAL: (1, 2, 4),
    AccountType.SOLE_PROPRIETOR: (1, 2, 3, 4),
    AccountType.REGISTERED_COMPANY: (1, 2, 3, 4),
}

# Business fields an individual is *not* asked for even though step 2 is required.
STEP_FIELDS_EXEMPT: dict[str, dict[int, tuple[str, ...]]] = {
    AccountType.INDIVIDUAL: {2: ("legal_business_name", "business_type", "company_registration_number")},
}

REQUIRED_DOCUMENTS: dict[str, tuple[str, ...]] = {
    AccountType.INDIVIDUAL: (DocType.ID_PASSPORT, DocType.BANK_CONFIRMATION, DocType.PROOF_OF_ADDRESS),
    AccountType.SOLE_PROPRIETOR: (DocType.ID_PASSPORT, DocType.COMPANY_REGISTRATION, DocType.BANK_CONFIRMATION, DocType.PROOF_OF_ADDRESS),
    AccountType.REGISTERED_COMPANY: (DocType.ID_PASSPORT, DocType.COMPANY_REGISTRATION, DocType.BANK_CONFIRMATION, DocType.PROOF_OF_ADDRESS),
}

# Which step each document is uploaded on (drives the step's `documents` block).
DOCUMENT_STEP = {
    DocType.ID_PASSPORT: 1,
    DocType.COMPANY_REGISTRATION: 3,
    DocType.BANK_CONFIRMATION: 4,
    DocType.PROOF_OF_ADDRESS: 4,
}

DOCUMENT_ALLOWED_TYPES = {"application/pdf", "image/jpeg", "image/png"}

# Statuses in which the seller may still edit steps / upload documents.
EDITABLE_STATUSES = {Status.DRAFT, Status.MORE_INFO_REQUIRED}

TRANSITIONS: dict[str, set[str]] = {
    Status.DRAFT: {Status.SUBMITTED},
    Status.SUBMITTED: {Status.UNDER_REVIEW, Status.MORE_INFO_REQUIRED, Status.APPROVED, Status.REJECTED},
    Status.UNDER_REVIEW: {Status.MORE_INFO_REQUIRED, Status.APPROVED, Status.REJECTED},
    Status.MORE_INFO_REQUIRED: {Status.RESUBMITTED},
    Status.RESUBMITTED: {Status.UNDER_REVIEW, Status.MORE_INFO_REQUIRED, Status.APPROVED, Status.REJECTED},
    Status.APPROVED: {Status.ACTIVE},
    Status.REJECTED: set(),
    Status.ACTIVE: set(),
}

# The five stages on the Application Status screen, in order.
# TODO(design): "More Info" is drawn as a permanent stage; we mark it `skipped` when it never happened.
TIMELINE_STAGES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Submitted", (Status.SUBMITTED,)),
    ("Under Review", (Status.UNDER_REVIEW,)),
    ("More Info", (Status.MORE_INFO_REQUIRED, Status.RESUBMITTED)),
    ("Approved", (Status.APPROVED,)),
    ("Active", (Status.ACTIVE,)),
)

# Human copy for the status banner ("Your application is currently being reviewed").
STATUS_HEADLINES = {
    Status.DRAFT: "Complete your application to submit it for review",
    Status.SUBMITTED: "Your application has been received",
    Status.UNDER_REVIEW: "Your application is currently being reviewed",
    Status.MORE_INFO_REQUIRED: "Your application needs additional information before we can complete the verification process",
    Status.RESUBMITTED: "Your updated application is being reviewed",
    Status.APPROVED: "Congratulations! Your seller application has been approved",
    Status.REJECTED: "Unfortunately your application was not approved",
    Status.ACTIVE: "Your seller account is active",
}


def required_fields(account_type: str, step: int) -> tuple[str, ...]:
    exempt = STEP_FIELDS_EXEMPT.get(account_type, {}).get(step, ())
    return tuple(f for f in STEP_REQUIRED_FIELDS.get(step, ()) if f not in exempt)
