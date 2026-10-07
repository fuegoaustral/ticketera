from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from events.models import Event
from tickets.models import NewTicket, Order, TicketType
from user_profile.models import Profile


class FreeTicketCheckoutTest(TestCase):
    """Bonos de monto libre (price=0), incluido comprarlos a $0."""

    def setUp(self):
        now = timezone.now()
        Event.objects.filter(is_main=True).update(is_main=False)
        self.event = Event.objects.create(
            name='Gualicho', slug='gualicho', active=True, is_main=True,
            start=now + timedelta(days=20), end=now + timedelta(days=24),
            transfers_enabled_until=now + timedelta(days=10), header_image='events/heros/no-image.jpg',
            title='Gualicho', description='Evento', max_tickets=100,
        )
        self.ticket_type = TicketType.objects.create(
            event=self.event, name='Bono libre', price=0, ticket_count=50,
        )
        self.user = User.objects.create_user(
            username='ana', email='ana@example.com', first_name='Ana', last_name='Artista',
        )
        self.user.profile.document_number = '10000001'
        self.user.profile.phone = '+5491100000001'
        self.user.profile.profile_completion = Profile.COMPLETE
        self.user.profile.save()
        self.client.force_login(self.user)

    def checkout(self, custom_amount):
        url = reverse('select_tickets') + '?event=gualicho'
        self.client.get(url)
        response = self.client.post(url, {
            f'ticket_{self.ticket_type.id}_quantity': 1,
            f'ticket_{self.ticket_type.id}_custom_amount': custom_amount,
        })
        self.assertEqual(response.status_code, 302)
        self.client.post(reverse('select_donations') + '?event=gualicho', {
            'donation_art': 0, 'donation_venue': 0, 'donation_grant': 0,
        })
        return self.client.post(reverse('order_summary') + '?event=gualicho', {})

    @patch('tickets.views.checkout.mercadopago.SDK')
    def test_zero_amount_confirms_without_mercadopago(self, sdk):
        response = self.checkout('0')

        order = Order.objects.get(user=self.user)
        self.assertRedirects(
            response, reverse('checkout_payment_callback', kwargs={'order_key': order.key}),
            fetch_redirect_response=False,
        )
        sdk.assert_not_called()
        self.assertEqual(order.status, Order.OrderStatus.CONFIRMED)
        self.assertEqual(NewTicket.objects.filter(order=order).count(), 1)

    @patch('tickets.views.checkout.mercadopago.SDK')
    def test_custom_amount_goes_to_mercadopago(self, sdk):
        sdk.return_value.preference.return_value.create.return_value = {
            'response': {'init_point': 'https://mp.example/pay'},
        }
        response = self.checkout('1500.50')

        self.assertRedirects(response, 'https://mp.example/pay', fetch_redirect_response=False)
        order = Order.objects.get(user=self.user)
        self.assertEqual(order.amount, Decimal('1500.50'))
        self.assertEqual(order.status, Order.OrderStatus.PENDING)


class CurrentEventsTest(TestCase):
    def test_ended_events_are_not_current(self):
        now = timezone.now()
        common = dict(active=True, transfers_enabled_until=now, header_image='events/heros/no-image.jpg',
                      title='E', description='E')
        upcoming = Event.objects.create(name='Gualicho', slug='gualicho', start=now + timedelta(days=1),
                                        end=now + timedelta(days=2), **common)
        Event.objects.create(name='Abracadabra', slug='abracadabra', start=now - timedelta(days=5),
                             end=now - timedelta(days=4), **common)

        self.assertEqual(list(Event.get_current_events()), [upcoming])
