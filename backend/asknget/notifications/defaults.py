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
    "category_request_approved": ("system", {"en": "Category Approved"}, {"en": "Your request for the category {name} was approved: you can now list products in {category}."}, "name, category"),
    "category_request_rejected": ("system", {"en": "Category Request Declined"}, {"en": "Your request for the category {name} was declined: {reason}"}, "name, reason"),
}

# Sprint 9: customers and staff get notifications too (events in notifications/catalog.py).
TEMPLATES.update({
    "refund_issued": ("returns", {"en": "Refund Issued"}, {"en": "A refund of {amount} was issued for return {reference} on order #{order}."}, "order, reference, amount"),
    # customers
    "customer_order_placed": ("orders", {"en": "Order Placed"}, {"en": "Thank you, {customer}. Your order #{order} has been placed. Total: {amount}."}, "order, customer, amount"),
    "customer_order_paid": ("orders", {"en": "Payment Received"}, {"en": "We received your payment of {amount} for order #{order}."}, "order, amount"),
    "customer_order_cancelled": ("orders", {"en": "Order Cancelled"}, {"en": "Your order #{order} has been cancelled."}, "order, by"),
    "customer_order_cancelled_store": ("orders", {"en": "Items Cancelled"}, {"en": "{store} cancelled its items in your order #{order}."}, "order, store"),
    "customer_order_shipped": ("orders", {"en": "Order Shipped"}, {"en": "Your order #{order} is on its way."}, "order"),
    "customer_order_shipped_store": ("orders", {"en": "Items Shipped"}, {"en": "{store} shipped its items in your order #{order}."}, "order, store"),
    "customer_order_out_for_delivery": ("orders", {"en": "Out for Delivery"}, {"en": "Your order #{order} is out for delivery."}, "order"),
    "customer_order_out_for_delivery_store": ("orders", {"en": "Out for Delivery"}, {"en": "Items from {store} in your order #{order} are out for delivery."}, "order, store"),
    "customer_order_delivered": ("orders", {"en": "Order Delivered"}, {"en": "Your order #{order} has been delivered."}, "order"),
    "customer_order_delivered_store": ("orders", {"en": "Items Delivered"}, {"en": "Items from {store} in your order #{order} have been delivered."}, "order, store"),
    "customer_return_requested": ("returns", {"en": "Return Requested"}, {"en": "We have your return request {reference} for order #{order}. The seller will respond within {due_days} days."}, "order, reference, due_days"),
    "customer_return_approved": ("returns", {"en": "Return Approved"}, {"en": "Your return {reference} was approved. {how}"}, "reference, how"),
    "customer_return_rejected": ("returns", {"en": "Return Not Approved"}, {"en": "Your return {reference} was not approved: {note}"}, "reference, note"),
    "customer_return_no_refund": ("returns", {"en": "No Refund Due"}, {"en": "We checked the item you returned under {reference} and no refund is due: {note}"}, "reference, note"),
    "customer_return_decided": ("returns", {"en": "Return Decision"}, {"en": "asknget reviewed your return {reference}: {outcome}."}, "reference, outcome"),
    "customer_refund_issued": ("returns", {"en": "Refund Issued"}, {"en": "Your return {reference} is complete. A refund of {amount} is on its way to you."}, "reference, amount"),
    # staff
    "staff_order_placed": ("orders", {"en": "High-Value Order"}, {"en": "Order #{order} for {amount} was placed in {market}."}, "order, amount, market"),
    "staff_order_cancelled": ("orders", {"en": "Order Cancelled"}, {"en": "Order #{order} was cancelled by the {by}."}, "order, by"),
    "staff_return_requested": ("returns", {"en": "Return Requested"}, {"en": "{customer} asked to return items from order #{order} ({reference})."}, "order, customer, reference"),
    "staff_return_escalated": ("returns", {"en": "Return Disputed"}, {"en": "Return {reference} on order #{order} was disputed and needs a decision."}, "order, reference"),
    "staff_refund_issued": ("returns", {"en": "Refund Issued"}, {"en": "A refund of {amount} was issued for return {reference}."}, "reference, amount"),
})

# E-mail subjects, where the title alone would not do (it has no order number or return reference). Kinds left out use their title.
EMAIL_SUBJECTS = {
    "customer_order_placed": {"en": "asknget order {order} received"},
    "customer_order_paid": {"en": "asknget order {order}: payment received"},
    "customer_order_cancelled": {"en": "asknget order {order}: cancelled"},
    "customer_order_cancelled_store": {"en": "asknget order {order}: items cancelled"},
    "customer_order_shipped": {"en": "asknget order {order}: shipped"},
    "customer_order_shipped_store": {"en": "asknget order {order}: items shipped"},
    "customer_order_out_for_delivery": {"en": "asknget order {order}: out for delivery"},
    "customer_order_out_for_delivery_store": {"en": "asknget order {order}: out for delivery"},
    "customer_order_delivered": {"en": "asknget order {order}: delivered"},
    "customer_order_delivered_store": {"en": "asknget order {order}: items delivered"},
    "customer_return_requested": {"en": "asknget return {reference}: received"},
    "customer_return_approved": {"en": "asknget return {reference}: approved"},
    "customer_return_rejected": {"en": "asknget return {reference}: rejected"},
    "customer_return_no_refund": {"en": "asknget return {reference}: no refund"},
    "customer_return_decided": {"en": "asknget return {reference}: decision"},
    "customer_refund_issued": {"en": "asknget return {reference}: refund issued"},
    "order_received": {"en": "New order #{order}"},
    "order_cancelled_customer": {"en": "Order #{order} cancelled by the customer"},
    "order_cancelled_staff": {"en": "Order #{order} cancelled by asknget"},
    "return_requested": {"en": "Return requested on order #{order}"},
    "return_escalated": {"en": "Return disputed on order #{order}"},
    "return_decided": {"en": "Return decided on order #{order}"},
    "staff_return_escalated": {"en": "Return {reference} needs a decision"},
}

# Which part of the portal a category belongs to. A team member sees a category only if they can
# view that section; `None` means everyone on the team.
CATEGORY_SECTION = {"orders": "orders", "payments": "transactions", "inventory": "inventory", "customers": "customers", "returns": "orders", "system": None}
