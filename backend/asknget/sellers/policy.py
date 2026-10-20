"""Per-country KYC rules.

Documents: `markets.DocumentType` is the catalogue (admins add types), `markets.DocumentRequirement` says which ones each account
type in a market uploads, required or optional, on which step, under which local name ("CNIC / Passport" vs "SA ID / Passport").
An account type with no rows in its market uses the platform defaults in `sellers.rules`. `markets.KycPolicy` adds the identity
field labels, the ID-number format and the instructions. `policy_for(application)` returns the merged, ready-to-use rules.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from django.conf import settings

from markets.i18n import pick
from markets.models import DocumentType

from . import rules


@dataclass(frozen=True)
class KycRules:
    country_code: str | None
    mode: str
    required_documents: dict[str, tuple[str, ...]]  # account_type → doc types that must be uploaded
    document_step: dict[str, int]
    document_labels: dict[str, str]  # doc type → label in the active language
    id_number_label: str
    id_number_regex: str
    tax_id_label: str
    company_registration_label: str
    instructions: str
    all_labels: dict[str, dict] = field(default_factory=dict)  # raw i18n, for admin/debug
    optional_documents: dict[str, tuple[str, ...]] = field(default_factory=dict)  # account_type → offered, not needed to submit
    types: dict = field(default_factory=dict)  # code → DocumentType (active ones)
    document_instructions: dict[str, str] = field(default_factory=dict)  # doc type → this market's note, active language

    def documents_for(self, account_type: str) -> tuple[str, ...]:
        return self.required_documents.get(account_type, ())

    def accepts(self, doc_type: str) -> bool:
        """Any active type may be uploaded (staff may ask for one the market does not list, through an info request)."""
        return doc_type in self.types

    def label(self, doc_type: str) -> str:
        t = self.types.get(doc_type)
        return self.document_labels.get(doc_type) or (pick(t.name, country=None) if t else doc_type)

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

    def _document(self, doc_type: str, account_type: str, *, required: bool) -> dict:
        t = self.types.get(doc_type)
        return {
            "type": doc_type, "label": self.label(doc_type), "step": self.step_for(doc_type, account_type), "required": required,
            "help": pick(t.help, country=None) if t and t.help else "", "instructions": self.document_instructions.get(doc_type, ""),
            "accepted_types": list(t.accepted_types) if t else list(rules.DOCUMENT_ALLOWED_TYPES), "max_mb": self._max_mb(t),
        }

    @staticmethod
    def _max_mb(t) -> int:
        """The type's own limit, never above the platform's (smaller where the host limits the request: Vercel)."""
        return min(t.max_mb if t else 10, settings.SELLER_DOCUMENT_MAX_BYTES // (1024 * 1024))

    def as_payload(self, account_type: str) -> dict:
        """The block the wizard renders: what to upload, how fields are labelled."""
        return {
            "country": self.country_code,
            "mode": self.mode,
            "required_documents": [
                self._document(d, account_type, required=True) for d in self.documents_for(account_type)
            ] + [self._document(d, account_type, required=False) for d in self.optional_documents.get(account_type, ())],
            "document_labels": {d: self.label(d) for d in self.document_step},
            "id_number_label": self.id_number_label or "ID / Passport number",
            "id_number_regex": self.id_number_regex,
            "tax_id_label": self.tax_id_label or "Tax / VAT number",
            "company_registration_label": self.company_registration_label or "Company registration number",
            "instructions": self.instructions,
        }


def _catalogue() -> dict:
    return {t.code: t for t in DocumentType.objects.filter(is_active=True)}


def defaults(country=None, types=None) -> KycRules:
    types = _catalogue() if types is None else types
    return KycRules(
        country_code=getattr(country, "code", None),
        mode="manual",
        required_documents={k: tuple(d for d in v if d in types) for k, v in rules.REQUIRED_DOCUMENTS.items()},
        document_step={**dict(rules.DOCUMENT_STEP), **{code: t.default_step for code, t in types.items()}},
        document_labels={code: pick(t.name, country=country) for code, t in types.items()},
        id_number_label="",
        id_number_regex="",
        tax_id_label="",
        company_registration_label="",
        instructions="",
        types=types,
    )


def for_country(country, *, language: str | None = None) -> KycRules:
    """Merged rules for a `Country` (or None → platform defaults). Two queries (the catalogue, the market's rows)."""
    types = _catalogue()
    base = defaults(country, types)
    if country is None:
        return base
    rows = [r for r in country.document_requirements.all() if r.document_type_id in types]
    required, optional = dict(base.required_documents), {}
    labels, notes, steps = {}, {}, {code: t.default_step for code, t in types.items()}
    for account_type in {r.account_type for r in rows}:                   # account types the market configured replace the defaults
        mine = sorted((r for r in rows if r.account_type == account_type), key=lambda r: (r.sort_order, r.document_type_id))
        required[account_type] = tuple(r.document_type_id for r in mine if r.required)
        optional[account_type] = tuple(r.document_type_id for r in mine if not r.required)
    for r in rows:
        if r.label:
            labels[r.document_type_id] = pick(r.label, language, country)
        if r.instructions:
            notes[r.document_type_id] = pick(r.instructions, language, country)
        if r.step:
            steps[r.document_type_id] = r.step
    labels.update({code: pick(t.name, language, country) for code, t in types.items() if code not in labels})
    policy = country.kyc
    return KycRules(
        country_code=country.code,
        mode=policy.mode if policy else "manual",
        required_documents=required,
        document_step=steps,
        document_labels=labels,
        id_number_label=policy.id_number_label if policy else "",
        id_number_regex=policy.id_number_regex if policy else "",
        tax_id_label=policy.tax_id_label if policy else "",
        company_registration_label=policy.company_registration_label if policy else "",
        instructions=pick(policy.instructions, language, country) if policy else "",
        all_labels={r.document_type_id: r.label for r in rows if r.label},
        optional_documents=optional,
        types=types,
        document_instructions=notes,
    )


def policy_for(app) -> KycRules:
    """Rules for one application — its own country's, whatever region the request came from. Worked out once per loaded
    application (a request asks several times: step states, the payload, validation)."""
    rules_ = getattr(app, "_kyc_rules", None)
    if rules_ is None:
        rules_ = app._kyc_rules = for_country(app.country)
    return rules_
