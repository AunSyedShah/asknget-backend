"""Every tunable the platform exposes to an admin. Add a line here, read it with
`config.get(...)`, and it is editable in Django admin with validation and a default.

Scopes say where an override may be set: `global` for the whole platform, `country`
for a market, `store` for one seller. The most specific wins.
"""

from .registry import register

# --- Payouts: when a sale's money becomes the seller's to withdraw ---------------------
register(
    "payouts.hold_mode", "enum", "return_window", "Payout hold",
    help="How long a sale is held before it counts as available. return_window: until the order's return "
         "window has passed. fixed_days: payouts.hold_days after the order. none: available at once.",
    choices=("return_window", "fixed_days", "none"),
)
register(
    "payouts.hold_days", "int", 7, "Payout hold (days)",
    help="Used when the hold mode is fixed_days.", min=0, max=365,
)
register(
    "payouts.minimum_cents", "int", 0, "Minimum payout",
    help="The smallest payout staff can create for a store, in the market's minor units (cents). 0 = no minimum.",
    scopes=("global", "country"), min=0,
)

# --- Analytics --------------------------------------------------------------------------
register(
    "analytics.low_performer_min_views", "int", 20, "Low performers: minimum views",
    help="A product needs at least this many views in the period before its conversion is judged. "
         "Below it, there is too little data to call a product a low performer.",
    min=1,
)
register(
    "analytics.low_performer_limit", "int", 5, "Low performers: list length",
    help="How many products the Low Performing Products card shows.",
    scopes=("global", "country"), min=1, max=50,
)

# --- Team -------------------------------------------------------------------------------
register(
    "team.invite_expiry_days", "int", 7, "Team invite lifetime (days)",
    help="How long an invitation link works.", scopes=("global", "country"), min=1, max=60,
)
register(
    "team.max_members", "int", 0, "Team size limit",
    help="The most people a store can invite, besides its owner. 0 = unlimited. Set it on a store, a market or "
         "globally, e.g. to make bigger teams a premium feature.",
    min=0,
)

# --- Notifications ----------------------------------------------------------------------
register(
    "notifications.recent_days", "int", 90, "Notifications: how far back",
    help="The notification list shows this many days of history.", scopes=("global",), min=1, max=365,
)

# --- Returns ----------------------------------------------------------------------------
register(
    "returns.courier", "str", "manual", "Return courier",
    help="Which courier integration collects returned parcels in this market (see returns.couriers). "
         "'manual' = the buyer posts it and enters the tracking number. An unknown name falls back to manual.",
    scopes=("global", "country"),
)
register(
    "returns.auto_approve", "bool", False, "Approve returns automatically",
    help="On: a request that passes the policy is approved at once, without waiting for the seller.", scopes=("global", "country"),
)
register(
    "returns.seller_response_days", "int", 3, "Seller response time (days)",
    help="How long a seller has to approve or reject a return. After it the request shows as overdue to staff.",
    scopes=("global", "country"), min=1, max=30,
)
register(
    "returns.max_photos", "int", 5, "Photos per return",
    help="The most photos a buyer can attach to one return request.", scopes=("global",), min=0, max=10,
)
register(
    "returns.allow_escalation", "bool", True, "Buyers can dispute a rejection",
    help="On: a buyer whose return was rejected can ask staff to decide.", scopes=("global", "country"),
)
