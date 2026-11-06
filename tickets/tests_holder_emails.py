from datetime import timedelta

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase
from django.utils import timezone

from events.models import Event
from tickets.models import NewTicket, Order, TicketType
from tickets.views.send_holder_tickets_email import run_send_holder_tickets_email
from user_profile.models import Profile



def _html(message):
    # templated_email puede mandar el HTML como alternativa o directo en body (EmailMessage).
    alternatives = getattr(message, 'alternatives', None)
    return alternatives[0][0] if alternatives else message.body

class HolderTicketsEmailTest(TestCase):
    """Eventos con cuenta obligatoria (FA, Abracadabra): sin PDF, links directos y transferir."""

    def setUp(self):
        now = timezone.now()
        Event.objects.filter(is_main=True).update(is_main=False)
        self.event = Event.objects.create(
            name='Fuego Austral', slug='fuego-austral', active=True, is_main=True,
            start=now + timedelta(days=20), end=now + timedelta(days=24),
            transfers_enabled_until=now + timedelta(days=10),
            attendee_must_be_registered=True,
            header_image='events/heros/no-image.jpg', title='FA', description='Evento',
        )
        self.ticket_type = TicketType.objects.create(event=self.event, name='Bono General', price=0)
        self.user = User.objects.create_user(username='ana', email='ana@example.com')
        self.user.profile.profile_completion = Profile.COMPLETE
        self.user.profile.save()
        order = Order.objects.create(
            first_name='Ana', last_name='A', email='ana@example.com', phone='1', dni='1',
            amount=0, status=Order.OrderStatus.CONFIRMED, event=self.event, user=self.user,
        )
        self.mine = NewTicket.objects.create(
            event=self.event, ticket_type=self.ticket_type, order=order, holder=self.user, owner=self.user,
        )
        self.guest = NewTicket.objects.create(
            event=self.event, ticket_type=self.ticket_type, order=order, holder=self.user,
        )

    def test_registered_event_sends_links_without_pdf(self):
        result = run_send_holder_tickets_email(self.event, holder_user_id=self.user.id)

        self.assertTrue(result['ok'], result)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.attachments, [])
        html = _html(message)
        base = '/mi-fuego/mis-bonos/fuego-austral/'
        self.assertIn(f'{base}?bono={self.mine.key}', html)
        self.assertIn(f'{base}?bono={self.guest.key}', html)
        # Solo el bono que no es propio se puede transferir.
        self.assertIn(f'{base}?bono={self.guest.key}&amp;transferir=1', html)
        self.assertNotIn(f'{base}?bono={self.mine.key}&amp;transferir=1', html)

    def test_no_transfer_button_after_transfer_period(self):
        self.event.transfers_enabled_until = timezone.now() - timedelta(days=1)
        self.event.save()

        run_send_holder_tickets_email(self.event, holder_user_id=self.user.id)

        html = _html(mail.outbox[0])
        self.assertIn(f'?bono={self.guest.key}', html)
        self.assertNotIn('transferir=1', html)

    def test_landing_page_handles_direct_link(self):
        self.client.force_login(self.user)
        response = self.client.get(
            f'/mi-fuego/mis-bonos/fuego-austral/?bono={self.guest.key}&transferir=1'
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'data-ticket-key="{self.guest.key}"')
