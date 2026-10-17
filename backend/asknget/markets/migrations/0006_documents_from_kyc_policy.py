"""The seven built-in document types, and each country's KYC JSON (required documents, labels) as requirement rows.

A country with a KYC policy gets a row per account type and document (its own list, or the platform defaults when its JSON had
none for that account type), carrying its local label ("CNIC / Passport"). A country without a policy keeps using the platform
defaults, as before. The JSON fields are removed by the next migration.
"""

from django.db import migrations

TYPES = [
    # code, English name, default step
    ("id_passport", "ID / Passport", 1),
    ("company_registration", "Company Registration", 3),
    ("tax_registration", "Tax Registration", 3),
    ("business_licence", "Business Licence", 3),
    ("bank_confirmation", "Bank Confirmation", 4),
    ("proof_of_address", "Proof of Address", 4),
    ("other", "Other Supporting Document", 4),
]

DEFAULTS = {     # sellers.rules.REQUIRED_DOCUMENTS, frozen
    "individual": ["id_passport", "bank_confirmation", "proof_of_address"],
    "sole_proprietor": ["id_passport", "company_registration", "bank_confirmation", "proof_of_address"],
    "registered_company": ["id_passport", "company_registration", "bank_confirmation", "proof_of_address"],
}


def forwards(apps, schema_editor):
    DocumentType = apps.get_model("markets", "DocumentType")
    DocumentRequirement = apps.get_model("markets", "DocumentRequirement")
    KycPolicy = apps.get_model("markets", "KycPolicy")
    for order, (code, name, step) in enumerate(TYPES):
        DocumentType.objects.get_or_create(code=code, defaults=dict(name={"en": name}, default_step=step, sort_order=order))
    known = {code for code, _, _ in TYPES}
    for policy in KycPolicy.objects.all():
        custom = policy.required_documents or {}
        labels = policy.document_labels or {}
        for account_type, defaults in DEFAULTS.items():
            for order, code in enumerate(d for d in (custom.get(account_type) or defaults) if d in known):
                DocumentRequirement.objects.get_or_create(
                    market_id=policy.country_id, account_type=account_type, document_type_id=code,
                    defaults=dict(required=True, label=labels.get(code) or {}, sort_order=order),
                )


def backwards(apps, schema_editor):
    """Put the rows back into the JSON (labels and lists), so the previous code works again."""
    KycPolicy = apps.get_model("markets", "KycPolicy")
    DocumentRequirement = apps.get_model("markets", "DocumentRequirement")
    for policy in KycPolicy.objects.all():
        rows = DocumentRequirement.objects.filter(market_id=policy.country_id, required=True).order_by("account_type", "sort_order")
        policy.required_documents, policy.document_labels = {}, {}
        for row in rows:
            policy.required_documents.setdefault(row.account_type, []).append(row.document_type_id)
            if row.label:
                policy.document_labels[row.document_type_id] = row.label
        policy.save()


class Migration(migrations.Migration):
    dependencies = [("markets", "0005_document_catalogue")]

    operations = [migrations.RunPython(forwards, backwards)]
