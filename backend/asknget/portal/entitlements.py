"""What a seller's plan unlocks.

One table, one gate. A store is premium or it is not (`Store.is_premium`, toggled
by staff); everything that depends on the plan asks this module rather than
reading the flag, so when real subscriptions arrive (docs/data-model.md §10 Q42)
only `plan_of()` changes.

Adding a feature = one row in FEATURES + one `require()` call at the endpoint.
"""

from __future__ import annotations

from dataclasses import dataclass

from ninja.errors import HttpError

FREE = "free"
PREMIUM = "premium"

ANALYTICS_ADVANCED = "analytics_advanced"
CHAT_INBOX = "chat_inbox"


@dataclass(frozen=True)
class Feature:
    key: str
    label: str
    description: str


FEATURES: dict[str, Feature] = {
    ANALYTICS_ADVANCED: Feature(
        ANALYTICS_ADVANCED, "Advanced Analytics",
        "Conversion funnel, product leaderboard and slow movers, search terms, customer cohorts and fulfilment times.",
    ),
    CHAT_INBOX: Feature(
        CHAT_INBOX, "Customer Chat",
        "Read and reply to messages from your customers. Buyers can always write to you; a plan with chat lets you answer.",
    ),
}

PLAN_FEATURES: dict[str, frozenset[str]] = {
    FREE: frozenset(),
    PREMIUM: frozenset({ANALYTICS_ADVANCED, CHAT_INBOX}),
}

PLAN_LABELS = {FREE: "Standard Seller", PREMIUM: "Premium Seller"}


def plan_of(store) -> str:
    return PREMIUM if getattr(store, "is_premium", False) else FREE


def features_for(store) -> frozenset[str]:
    return PLAN_FEATURES[plan_of(store)]


def has(store, feature: str) -> bool:
    return feature in features_for(store)


def require(store, feature: str) -> None:
    """403 `premium_required` — the portal shows the upgrade card for `feature`."""
    if not has(store, feature):
        raise HttpError(403, "premium_required")


def payload(store) -> dict:
    """`GET /seller/plan` — also embedded in the dashboard so the sidebar can lock
    entries without a second call. The copy lives here, not in the frontend."""
    plan = plan_of(store)
    included = PLAN_FEATURES[plan]
    return {
        "plan": plan,
        "plan_label": PLAN_LABELS[plan],
        "is_premium": plan == PREMIUM,
        "features": sorted(included),
        "locked_features": sorted(set(FEATURES) - included),
        "catalogue": [
            {"key": f.key, "label": f.label, "description": f.description, "included": f.key in included}
            for f in FEATURES.values()
        ],
    }
