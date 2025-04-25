from django.urls import path

from .views import home, order, ticket, checkout, webhooks, new_ticket
from tickets.views import admin

urlpatterns = [
    path('', home.home, name='home'),

    # Order related paths
    path('new-order/<int:ticket_type_id>/', order.order, name='order'),
    path('order/<str:order_key>', order.order_detail, name='order_detail'),
    path('order/<str:order_key>/payments/success', order.payment_success, name='payment_success_callback'),
    path('order/<str:order_key>/payments/failure', order.payment_failure, name='payment_failure_callback'),
    path('order/<str:order_key>/payments/pending', order.payment_pending, name='payment_pending_callback'),
    path('order/<str:order_key>/confirm', order.free_order_confirmation, name='free_order_confirmation'),
    path('payments/ipn/', order.payment_notification, name='payment_notification'),
    path('checkout/payment-callback/<uuid:order_key>', order.checkout_payment_callback,
         name='checkout_payment_callback'),
    path('checkout/check-order-status/<uuid:order_key>', order.check_order_status, name='check_order_status'),

    # Ticket related paths
    path('ticket/<str:ticket_key>/transfer/', new_ticket.transfer_ticket, name='transfer_ticket'),
    path('ticket/<str:ticket_key>/unassign/', new_ticket.unassign_ticket, name='unassign_ticket'),
    path('ticket/transfer-ticket/cancel-ticket-transfer', new_ticket.cancel_ticket_transfer,
         name='cancel_ticket_transfer'),

    path('ticket/<str:ticket_key>', ticket.ticket_detail, name='ticket_detail'),
    path('ticket/<str:ticket_key>/transfer', ticket.ticket_transfer, name='ticket_transfer'),
    path('ticket/<str:ticket_key>/transfer/confirmation', ticket.ticket_transfer_confirmation,
         name='ticket_transfer_confirmation'),
    path('ticket/<str:transfer_key>/confirmed', ticket.ticket_transfer_confirmed, name='ticket_transfer_confirmed'),

    # Checkout related paths
    path('checkout/select-tickets', checkout.select_tickets, name='select_tickets'),
    path('checkout/select-donations', checkout.select_donations, name='select_donations'),
    path('checkout/order-summary', checkout.order_summary, name='order_summary'),

    # Webhook related paths
    path('webhooks/mercadopago', webhooks.mercadopago_webhook, name='mercadopago_webhook'),

    path('ticket/<str:ticket_key>/assign', new_ticket.assign_ticket, name='assign_ticket'),
    path('ticket/<str:ticket_key>/unassign', new_ticket.unassign_ticket, name='unassign_ticket'),

    path('ping/', home.ping, name='ping'),

    path('scan/', admin.scan_tickets, name='scan_tickets'),
    path('api/tickets/<str:ticket_key>/check/', admin.check_ticket, name='check_ticket'),
    path('api/tickets/<str:ticket_key>/mark-used/', admin.mark_ticket_used, name='mark_ticket_used'),
]
