"""The built-in notification kinds and their wording.

These seed the `NotificationTemplate` table (so an admin can reword, translate or switch a kind
off) and act as the fallback when a row is missing, so a new kind added here works immediately.

Placeholders in {braces} are filled from the notification's `params` when it is read.
`{amount}` is built from `amount_cents` + `currency` if both are present.
"""

CATEGORIES = ("orders", "payments", "inventory", "customers", "returns", "system")

# kind: (category, {lang: title}, {lang: body}, what the placeholders are)
TEMPLATES = {
    "order_received": ("orders", {"en": "New Order Received"}, {"en": "Order #{order} has been placed by {customer}."}, "order, customer"),
    "payment_received": ("payments", {"en": "Payment Received"}, {"en": "Payment for Order #{order} has been successfully received."}, "order"),
    "order_cancelled_customer": ("orders", {"en": "Order Cancelled"}, {"en": "Order #{order} has been cancelled by the customer."}, "order"),
    "order_cancelled_staff": ("orders", {"en": "Order Cancelled"}, {"en": "Order #{order} has been cancelled by asknget."}, "order"),
    "low_stock": ("inventory", {"en": "Low Stock Alert"}, {"en": "{product} has only {qty} units remaining."}, "product, qty"),
    "out_of_stock": ("inventory", {"en": "Out of Stock"}, {"en": "{product} is out of stock."}, "product"),
    "new_customer": ("customers", {"en": "New Customer"}, {"en": "{customer} placed their first order with your store."}, "customer"),
    "payout_processed": ("payments", {"en": "Payout Processed"}, {"en": "Your payout of {amount} has been processed successfully."}, "amount"),
    "product_approved": ("system", {"en": "Product Approved"}, {"en": "{product} is now live in your store."}, "product"),
    "product_rejected": ("system", {"en": "Product Rejected"}, {"en": "{product} was not approved: {reason}"}, "product, reason"),
    "return_requested": ("returns", {"en": "Return Requested"}, {"en": "{customer} asked to return {items} item(s) from order #{order}: {reason}."}, "order, customer, items, reason"),
    "return_cancelled": ("returns", {"en": "Return Withdrawn"}, {"en": "{customer} withdrew the return request for order #{order}."}, "order, customer"),
    "return_escalated": ("returns", {"en": "Return Disputed"}, {"en": "{customer} disputed your rejection of the return for order #{order}. asknget will decide."}, "order, customer"),
    "return_decided": ("returns", {"en": "Return Decision"}, {"en": "asknget decided the return for order #{order}: {outcome}."}, "order, outcome"),
    "plan_changed": ("system", {"en": "Plan Updated"}, {"en": "Your store is now on the {plan} plan."}, "plan"),
}

# Which part of the portal a category belongs to. A team member sees a category only if they can
# view that section; `None` means everyone on the team.
CATEGORY_SECTION = {"orders": "orders", "payments": "transactions", "inventory": "inventory", "customers": "customers", "returns": "orders", "system": None}
