"""The staff permission catalogue.

Roles are data (Django groups an admin creates and ticks permissions on); the permissions themselves are code, because code
checks them. Each one is registered as a Django permission `staff.<code>` on `StaffProfile` (see its Meta), so the group editor in
Django admin lists them. Adding one here needs a migration (`makemigrations staff`); a test fails until it exists.
"""

CATALOGUE = {
    # section.action: what it allows
    "orders.view": "See every order",
    "orders.manage": "Advance an order's status",
    "sellers.view": "See seller applications",
    "sellers.review": "Review seller applications and their documents (start, ask for more, approve, reject)",
    "stores.manage": "Change a store's plan",
    "products.view": "See the product review queue",
    "products.review": "Approve or reject products",
    "returns.view": "See returns and the return settings",
    "returns.decide": "Settle disputed returns",
    "returns.refund": "Record that a refund was paid",
    "returns.configure": "Edit return policies, reasons and rules",
    "category_requests.view": "See category requests",
    "category_requests.decide": "Approve or reject category requests",
    "chat.moderate": "See flagged chat messages",
    "ledger.view": "See a store's balances",
    "ledger.adjust": "Credit or debit a store",
    "payouts.view": "See payouts",
    "payouts.manage": "Create payouts and change their status",
}


# The starter roles (Administrator, KYC reviewer, Catalogue, Support, Finance) are created once by migration staff 0002.
