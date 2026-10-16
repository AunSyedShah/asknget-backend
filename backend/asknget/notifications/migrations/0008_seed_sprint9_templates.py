"""Wording for the customer and staff notifications of sprint 9 (and the seller's refund notice). Existing rows are left alone."""

from django.db import migrations

ROWS = {
    'refund_issued': ('returns', {'en': 'Refund Issued'}, {'en': 'A refund of {amount} was issued for return {reference} on order #{order}.'}, 'order, reference, amount'),
    'customer_order_placed': ('orders', {'en': 'Order Placed'}, {'en': 'Thank you, {customer}. Your order #{order} has been placed. Total: {amount}.'}, 'order, customer, amount'),
    'customer_order_paid': ('orders', {'en': 'Payment Received'}, {'en': 'We received your payment of {amount} for order #{order}.'}, 'order, amount'),
    'customer_order_cancelled': ('orders', {'en': 'Order Cancelled'}, {'en': 'Your order #{order} has been cancelled.'}, 'order, by'),
    'customer_order_cancelled_store': ('orders', {'en': 'Items Cancelled'}, {'en': '{store} cancelled its items in your order #{order}.'}, 'order, store'),
    'customer_order_shipped': ('orders', {'en': 'Order Shipped'}, {'en': 'Your order #{order} is on its way.'}, 'order'),
    'customer_order_shipped_store': ('orders', {'en': 'Items Shipped'}, {'en': '{store} shipped its items in your order #{order}.'}, 'order, store'),
    'customer_order_out_for_delivery': ('orders', {'en': 'Out for Delivery'}, {'en': 'Your order #{order} is out for delivery.'}, 'order'),
    'customer_order_out_for_delivery_store': ('orders', {'en': 'Out for Delivery'}, {'en': 'Items from {store} in your order #{order} are out for delivery.'}, 'order, store'),
    'customer_order_delivered': ('orders', {'en': 'Order Delivered'}, {'en': 'Your order #{order} has been delivered.'}, 'order'),
    'customer_order_delivered_store': ('orders', {'en': 'Items Delivered'}, {'en': 'Items from {store} in your order #{order} have been delivered.'}, 'order, store'),
    'customer_return_requested': ('returns', {'en': 'Return Requested'}, {'en': 'We have your return request {reference} for order #{order}. The seller will respond within {due_days} days.'}, 'order, reference, due_days'),
    'customer_return_approved': ('returns', {'en': 'Return Approved'}, {'en': 'Your return {reference} was approved. {how}'}, 'reference, how'),
    'customer_return_rejected': ('returns', {'en': 'Return Not Approved'}, {'en': 'Your return {reference} was not approved: {note}'}, 'reference, note'),
    'customer_return_no_refund': ('returns', {'en': 'No Refund Due'}, {'en': 'We checked the item you returned under {reference} and no refund is due: {note}'}, 'reference, note'),
    'customer_return_decided': ('returns', {'en': 'Return Decision'}, {'en': 'asknget reviewed your return {reference}: {outcome}.'}, 'reference, outcome'),
    'customer_refund_issued': ('returns', {'en': 'Refund Issued'}, {'en': 'Your return {reference} is complete. A refund of {amount} is on its way to you.'}, 'reference, amount'),
    'staff_order_placed': ('orders', {'en': 'High-Value Order'}, {'en': 'Order #{order} for {amount} was placed in {market}.'}, 'order, amount, market'),
    'staff_order_cancelled': ('orders', {'en': 'Order Cancelled'}, {'en': 'Order #{order} was cancelled by the {by}.'}, 'order, by'),
    'staff_return_requested': ('returns', {'en': 'Return Requested'}, {'en': '{customer} asked to return items from order #{order} ({reference}).'}, 'order, customer, reference'),
    'staff_return_escalated': ('returns', {'en': 'Return Disputed'}, {'en': 'Return {reference} on order #{order} was disputed and needs a decision.'}, 'order, reference'),
    'staff_refund_issued': ('returns', {'en': 'Refund Issued'}, {'en': 'A refund of {amount} was issued for return {reference}.'}, 'reference, amount'),
}

SUBJECTS = {'customer_order_placed': {'en': 'asknget order {order} received'}, 'customer_order_paid': {'en': 'asknget order {order}: payment received'}, 'customer_order_cancelled': {'en': 'asknget order {order}: cancelled'}, 'customer_order_cancelled_store': {'en': 'asknget order {order}: items cancelled'}, 'customer_order_shipped': {'en': 'asknget order {order}: shipped'}, 'customer_order_shipped_store': {'en': 'asknget order {order}: items shipped'}, 'customer_order_out_for_delivery': {'en': 'asknget order {order}: out for delivery'}, 'customer_order_out_for_delivery_store': {'en': 'asknget order {order}: out for delivery'}, 'customer_order_delivered': {'en': 'asknget order {order}: delivered'}, 'customer_order_delivered_store': {'en': 'asknget order {order}: items delivered'}, 'customer_return_requested': {'en': 'asknget return {reference}: received'}, 'customer_return_approved': {'en': 'asknget return {reference}: approved'}, 'customer_return_rejected': {'en': 'asknget return {reference}: rejected'}, 'customer_return_no_refund': {'en': 'asknget return {reference}: no refund'}, 'customer_return_decided': {'en': 'asknget return {reference}: decision'}, 'customer_refund_issued': {'en': 'asknget return {reference}: refund issued'}, 'order_received': {'en': 'New order #{order}'}, 'order_cancelled_customer': {'en': 'Order #{order} cancelled by the customer'}, 'order_cancelled_staff': {'en': 'Order #{order} cancelled by asknget'}, 'return_requested': {'en': 'Return requested on order #{order}'}, 'return_escalated': {'en': 'Return disputed on order #{order}'}, 'return_decided': {'en': 'Return decided on order #{order}'}, 'staff_return_escalated': {'en': 'Return {reference} needs a decision'}}


def forwards(apps, schema_editor):
    Template = apps.get_model("notifications", "NotificationTemplate")
    for kind, (category, title, body, placeholders) in ROWS.items():
        Template.objects.get_or_create(kind=kind, defaults=dict(category=category, title=title, body=body, email_subject=SUBJECTS.get(kind, {}),
                                                                 description=f"Placeholders: {placeholders}"))
    for kind, subject in SUBJECTS.items():                       # the seller kinds that already existed get a subject too
        Template.objects.filter(kind=kind, email_subject={}).update(email_subject=subject)


def backwards(apps, schema_editor):
    apps.get_model("notifications", "NotificationTemplate").objects.filter(kind__in=ROWS).delete()


class Migration(migrations.Migration):
    dependencies = [("notifications", "0007_sprint9_events_rules_outbox")]

    operations = [migrations.RunPython(forwards, backwards)]
