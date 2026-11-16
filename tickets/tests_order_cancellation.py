from datetime import timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from events.models import Event
from tickets.models import NewTicket, Order, OrderTicket, TicketType


def fake_sdk(paid=280000, refunded=0, refund_status=201, refund_response=None):
    sdk = MagicMock()
    sdk.payment.return_value.get.return_value = {
        'status': 200,
        'response': {'id': 555, 'status': 'approved', 'transaction_amount': paid,
                     'transaction_amount_refunded': refunded},
    }
    sdk.refund.return_value.create.return_value = {
        'status': refund_status,
        'response': refund_response or {'id': 999, 'status': 'approved'},
    }
    return sdk


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class OrderCancellationAdminTest(TestCase):
    def setUp(self):
        now = timezone.now()
        Event.objects.update(is_main=False, active=False)
        self.event = Event.objects.create(
            name='Abracadabra', slug='abracadabra', active=True, is_main=True,
            start=now + timedelta(days=20), end=now + timedelta(days=24),
            transfers_enabled_until=now + timedelta(days=10), header_image='events/heros/no-image.jpg',
            title='Abracadabra', description='Evento', max_tickets=100,
        )
        self.tt = TicketType.objects.create(event=self.event, name='Beca Inclusión Radical', price=140000)
        self.buyer = User.objects.create_user(username='ariel', email='ariel@example.com', first_name='Ariel')
        self.order = Order.objects.create(
            first_name='Ariel', last_name='R', email='ariel@example.com', phone='1', dni='1',
            amount=Decimal('280000'), event=self.event, user=self.buyer,
            status=Order.OrderStatus.CONFIRMED, processor_callback={'id': 555, 'status': 'approved'},
        )
        OrderTicket.objects.create(order=self.order, ticket_type=self.tt, quantity=2)
        self.tickets = [
            NewTicket.objects.create(event=self.event, order=self.order, ticket_type=self.tt,
                                     owner=self.buyer, holder=self.buyer)
            for _ in range(2)
        ]
        self.admin = User.objects.create_superuser(username='admin', email='admin@example.com', password='x')
        self.client.force_login(self.admin)
        self.url = reverse('admin:tickets_order_cancel', args=[self.order.pk])

    def post(self, **overrides):
        data = {
            'quantity': 2, 'do_refund': 'on', 'refund_amount': '280000', 'send_email': 'on',
            'email_subject': 'Anulación', 'email_body': 'Hola Ariel:\n\nTe devolvimos la plata.',
            'idempotency_key': 'abc',
        }
        data.update(overrides)
        return self.client.post(self.url, {k: v for k, v in data.items() if v is not None})

    @patch('tickets.order_cancellation.mercadopago.SDK')
    def test_get_shows_prefilled_email_and_payment(self, sdk_cls):
        sdk_cls.return_value = fake_sdk()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'queda por devolver $280000')
        self.assertContains(response, 'Beca Inclusión Radical')
        self.assertContains(response, 'Ya te devolvimos $280.000 por Mercado Pago')

    @patch('tickets.order_cancellation.mercadopago.SDK')
    def test_full_cancellation_refunds_deletes_and_emails(self, sdk_cls):
        sdk = sdk_cls.return_value = fake_sdk()
        response = self.post()
        self.assertRedirects(response, reverse('admin:tickets_order_change', args=[self.order.pk]))

        sdk.refund.return_value.create.assert_called_once()
        args, kwargs = sdk.refund.return_value.create.call_args
        self.assertEqual(args, ('555', {'amount': 280000.0}))
        self.assertEqual(kwargs['request_options'].custom_headers, {'X-Idempotency-Key': 'abc'})

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.OrderStatus.REFUNDED)
        self.assertFalse(NewTicket.objects.filter(order=self.order).exists())
        self.assertIn('refund MP #999', self.order.notes)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['ariel@example.com'])
        self.assertEqual(mail.outbox[0].subject, 'Anulación')

    @patch('tickets.order_cancellation.mercadopago.SDK')
    def test_partial_cancellation_keeps_order_confirmed(self, sdk_cls):
        sdk_cls.return_value = fake_sdk()
        self.post(quantity=1, refund_amount='140000', send_email=None)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.OrderStatus.CONFIRMED)
        self.assertEqual(NewTicket.objects.filter(order=self.order).count(), 1)
        self.assertEqual(OrderTicket.objects.get(order=self.order).quantity, 1)
        self.assertEqual(len(mail.outbox), 0)

    @patch('tickets.order_cancellation.mercadopago.SDK')
    def test_mp_failure_changes_nothing(self, sdk_cls):
        sdk_cls.return_value = fake_sdk(refund_status=400, refund_response={'message': 'nope'})
        response = self.post()
        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.OrderStatus.CONFIRMED)
        self.assertEqual(NewTicket.objects.filter(order=self.order).count(), 2)
        self.assertEqual(len(mail.outbox), 0)

    @patch('tickets.order_cancellation.mercadopago.SDK')
    def test_cannot_refund_more_than_left_in_mp(self, sdk_cls):
        sdk = sdk_cls.return_value = fake_sdk(refunded=200000)
        self.post()
        sdk.refund.return_value.create.assert_not_called()
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.OrderStatus.CONFIRMED)

    @patch('tickets.order_cancellation.mercadopago.SDK')
    def test_already_refunded_in_mp_without_tickets_marks_refunded(self, sdk_cls):
        """Caso de las becas publicadas por error: refund hecho a mano en MP y bonos ya borrados."""
        sdk = sdk_cls.return_value = fake_sdk(refunded=280000)
        NewTicket.objects.filter(order=self.order).delete()
        self.post(quantity=0, do_refund=None, refund_amount='0')
        sdk.refund.return_value.create.assert_not_called()
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.OrderStatus.REFUNDED)
        self.assertEqual(len(mail.outbox), 1)

    def test_link_only_for_confirmed_orders(self):
        from tickets.admin import OrderAdmin
        from django.contrib import admin

        model_admin = OrderAdmin(Order, admin.site)
        self.assertIn('/anular/', model_admin.cancel_link(self.order))
        self.order.status = Order.OrderStatus.REFUNDED
        self.assertEqual(model_admin.cancel_link(self.order), '-')

    @patch('tickets.order_cancellation.mercadopago.SDK')
    def test_staff_without_change_permission_is_denied(self, sdk_cls):
        sdk_cls.return_value = fake_sdk()
        staff = User.objects.create_user(username='s', email='s@example.com', is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(self.url).status_code, 403)
