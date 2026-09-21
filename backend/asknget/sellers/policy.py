"""Per-country KYC rules.

`sellers.rules` holds the platform defaults (which documents each account type
must upload, which step they belong to). A country's `markets.KycPolicy` — edited
by staff in admin — overlays those defaults: a different document list, local
labels ("CNIC / Passport" vs "SA ID / Passport"), an ID-number format, extra
instructions. `policy_for(application)` returns the merged, ready-to-use rules.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from markets.i18n import pick

from . import rules
from .models import SellerDocument

DocType = SellerDocument.DocumentType

DEFAULT_LABELS = {d.value: d.label for d in DocType}


@dataclass(frozen=True)
class KycRules:
    country_code: str | None
    mode: str
    required_documents: dict[str, tuple[str, ...]]  # account_type → doc types
    document_step: dict[str, int]
    document_labels: dict[str, str]  # doc type → label in the active language
    id_number_label: str
    id_number_regex: str
    tax_id_label: str
    company_registration_label: str
    instructions: str
    all_labels: dict[str, dict] = field(default_factory=dict)  # raw i18n, for admin/debug

    def documents_for(self, account_type: str) -> tuple[str, ...]:
        return self.required_documents.get(account_type, ())

    def label(self, doc_type: str) -> str:
        return self.document_labels.get(doc_type) or DEFAULT_LABELS.get(doc_type, doc_type)

    def step_for(self, doc_type: str, account_type: str) -> int:
        """The wizard step that collects `doc_type` for this account type. A document
        whose natural step the account type skips (e.g. an individual's tax certificate
        on the Representative step) moves to the last step they do fill in."""
        required_steps = rules.REQUIRED_STEPS[account_type]
        step = self.document_step.get(doc_type, 1)
        return step if step in required_steps else max(required_steps)

    def validate_id_number(self, value: str) -> str | None:
        """Error message when `value` does not match the country's format, else None."""
        if not value or not self.id_number_regex:
            return None
        if re.fullmatch(self.id_number_regex, value.replace(" ", "").upper()):
            return None
        return f"{self.id_number_label or 'ID / Passport number'} is not in a valid format"

    def as_payload(self, account_type: str) -> dict:
        """The block the wizard renders: what to upload, how fields are labelled."""
        return {
            "country": self.country_code,
            "mode": self.mode,
            "required_documents": [
                {"type": d, "label": self.label(d), "step": self.step_for(d, account_type)} for d in self.documents_for(account_type)
            ],
            "document_labels": {d: self.label(d) for d in self.document_step},
            "id_number_label": self.id_number_label or "ID / Passport number",
            "id_number_regex": self.id_number_regex,
            "tax_id_label": self.tax_id_label or "Tax / VAT number",
            "company_registration_label": self.company_registration_label or "Company registration number",
            "instructions": self.instructions,
        }


def defaults(country=None) -> KycRules:
    return KycRules(
        country_code=getattr(country, "code", None),
        mode="manual",
        required_documents={k: tuple(v) for k, v in rules.REQUIRED_DOCUMENTS.items()},
        document_step=dict(rules.DOCUMENT_STEP),
        document_labels={},
        id_number_label="",
        id_number_regex="",
        tax_id_label="",
        company_registration_label="",
        instructions="",
    )


def for_country(country, *, language: str | None = None) -> KycRules:
    """Merged rules for a `Country` (or None → platform defaults)."""
    base = defaults(country)
    policy = country.kyc if country is not None else None
    if policy is None:
        return base
    required = dict(base.required_documents)
    for account_type, docs in (policy.required_documents or {}).items():
        required[account_type] = tuple(d for d in docs if d in DEFAULT_LABELS)
    steps = dict(base.document_step)
    for doc_type in {d for docs in required.values() for d in docs}:
        steps.setdefault(doc_type, rules.DOCUMENT_STEP.get(doc_type, 1))
    labels = {d: pick(m, language, country) for d, m in (policy.document_labels or {}).items() if d in DEFAULT_LABELS}
    return KycRules(
        country_code=country.code,
        mode=policy.mode,
        required_documents=required,
        document_step=steps,
        document_labels=labels,
        id_number_label=policy.id_number_label,
        id_number_regex=policy.id_number_regex,
        tax_id_label=policy.tax_id_label,
        company_registration_label=policy.company_registration_label,
        instructions=pick(policy.instructions, language, country),
        all_labels=dict(policy.document_labels or {}),
    )


def policy_for(app) -> KycRules:
    """Rules for one application — its own country's, whatever region the request came from."""
    return for_country(app.country)
