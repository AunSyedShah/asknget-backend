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
