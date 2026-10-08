from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Permission, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from events.models import Event
from tickets.models import NewTicket, Order, TicketType
from teams.models import Team, TeamMembership
from user_profile.models import Profile


class AccountMenuTest(TestCase):
    """The "Mi cuenta" dropdown in the top nav (tickets/barbu_base.html)."""

    def setUp(self):
        now = timezone.now()
        Event.objects.filter(is_main=True).update(is_main=False)
        self.event = Event.objects.create(
            name='FA Carnaval', slug='fa-carnaval', active=True, is_main=True,
            start=now + timedelta(days=20), end=now + timedelta(days=24),
            transfers_enabled_until=now + timedelta(days=10), header_image='events/heros/no-image.jpg', title='FA', description='Evento',
        )
        self.user = User.objects.create_user(
            username='ana', email='ana@example.com', first_name='Ana', last_name='Artista',
        )
        self.user.profile.document_number = '10000001'
        self.user.profile.phone = '+5491100000001'
        self.user.profile.profile_completion = Profile.COMPLETE
        self.user.profile.save()
        self.client.force_login(self.user)

    def menu(self):
        response = self.client.get(reverse('art_dashboard'))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        start = html.index('class="dropdown-menu dropdown-menu-end account-menu"')
        return html[start:html.index('Cerrar sesión', start)]

    def test_participant_sees_own_sections_and_no_administration(self):
        menu = self.menu()
        self.assertIn('Ana Artista', menu)
        self.assertIn('ana@example.com', menu)
        self.assertIn(reverse('art_dashboard'), menu)
        self.assertIn('Mis bonos y eventos', menu)
        self.assertIn('https://fuegoaustral.org/faq/', menu)
        self.assertNotIn('target="_blank"', menu)
        self.assertIn(reverse('account_logout'), menu)
        for name in ('my_events', 'estafa_home', 'scanner_events', 'caja_events', 'admin_logros'):
            self.assertNotIn(reverse(name), menu)
        self.assertNotIn(reverse('la_sede'), menu)
        self.assertNotIn('text-success', menu)
        self.assertNotIn('text-danger', menu)

    def test_current_section_is_marked(self):
        menu = self.menu()
        self.assertIn(f'href="{reverse("art_dashboard")}" aria-current="page"', menu)
        self.assertEqual(menu.count('aria-current="page"'), 1)

    def test_event_admin_sees_events_scanner_and_cajas(self):
        self.event.admins.add(self.user)
        menu = self.menu()
        for name in ('my_events', 'scanner_events', 'caja_events'):
            self.assertIn(reverse(name), menu)
        self.assertNotIn(reverse('admin_logros'), menu)
        self.assertNotIn(reverse('estafa_home'), menu)

    def test_estafa_member_sees_estafa_only_while_active(self):
        estafa = Team.objects.get(slug='estafa')
        today = timezone.localdate()
        membership = TeamMembership.objects.create(team=estafa, user=self.user, started_on=today - timedelta(days=30))
        menu = self.menu()
        self.assertIn(reverse('estafa_home'), menu)
        self.assertNotIn(reverse('my_events'), menu)
        membership.ended_on = today - timedelta(days=1)
        membership.save()
        self.assertNotIn(reverse('estafa_home'), self.menu())

    def test_scanner_role_sees_only_scanner(self):
        self.event.access_scanner.add(self.user)
        menu = self.menu()
        self.assertIn(reverse('scanner_events'), menu)
        self.assertNotIn(reverse('my_events'), menu)
        self.assertNotIn(reverse('caja_events'), menu)

    def test_figuritas_permission_sees_figuritas_admin(self):
        self.user.user_permissions.add(Permission.objects.get(codename='manage_achievements'))
        menu = self.menu()
        self.assertIn(reverse('admin_logros'), menu)
        self.assertNotIn(reverse('admin_logros_assign'), menu)
        self.assertNotIn(reverse('my_events'), menu)



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

    @patch('tickets.views.checkout.mercadopago.SDK')
    def test_blank_price_ticket_is_free(self, sdk):
        self.ticket_type.price = None
        self.ticket_type.save()

        response = self.checkout('')

        order = Order.objects.get(user=self.user)
        self.assertRedirects(
            response, reverse('checkout_payment_callback', kwargs={'order_key': order.key}),
            fetch_redirect_response=False,
        )
        sdk.assert_not_called()
        self.assertEqual(order.amount, 0)
        self.assertEqual(order.status, Order.OrderStatus.CONFIRMED)


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
