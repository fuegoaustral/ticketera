from datetime import timedelta

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from events.models import Event
from teams.models import Team, TeamMembership
from user_profile.models import Profile

from .estafa import ESTAFA_SLUG
from .models import ArtProgram, Artwork


class ArtworkStateEmailTest(TestCase):
    """Emails al equipo cuando la Propuesta de Arte cambia de estado."""

    def setUp(self):
        now = timezone.now()
        self.owner = User.objects.create_user(username='artista', email='artista@example.com')
        self.collaborator = User.objects.create_user(username='colab', email='colab@example.com')
        self.estafa = User.objects.create_user(username='coord', email='coord@example.com')
        for index, user in enumerate((self.owner, self.collaborator, self.estafa), start=1):
            user.profile.document_number = f'2000000{index}'
            user.profile.phone = f'+54911100000{index:02d}'
            user.profile.profile_completion = Profile.COMPLETE
            user.profile.save()
        Event.objects.filter(is_main=True).update(is_main=False)
        self.event = Event.objects.create(
            name='FA Carnaval', slug='fa-carnaval', active=True, is_main=True,
            start=now + timedelta(days=20), end=now + timedelta(days=24),
            transfers_enabled_until=now + timedelta(days=10), header_image='events/heros/no-image.jpg',
            title='FA', description='Evento', has_volunteers=True,
        )
        TeamMembership.objects.create(
            team=Team.objects.get(slug=ESTAFA_SLUG), user=self.estafa, started_on=now.date() - timedelta(days=30),
        )
        self.program = ArtProgram.objects.create(
            event=self.event, is_current=True,
            registration_opens=now - timedelta(days=30), registration_closes=now + timedelta(days=1),
            proposal_deadline=now + timedelta(days=2),
        )

    def test_first_save_confirms_once_to_the_owner(self):
        self.client.force_login(self.owner)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('artwork_create', args=[self.event.slug]), {'title': 'Faro'})
        artwork = Artwork.objects.get(owner=self.owner)
        # ESTAFA recibe aparte su aviso de obra nueva (tests_estafa_notifications).
        owner_mail = [message for message in mail.outbox if message.to == ['artista@example.com']]
        self.assertEqual(len(owner_mail), 1)
        self.assertIn('Recibimos tu Propuesta de Arte «Faro»', owner_mail[0].subject)
        self.assertIn(reverse('artwork_edit', args=[artwork.pk]), owner_mail[0].body)
        sent = len(mail.outbox)

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('artwork_edit', args=[artwork.pk]), {
                'title': 'Faro grande', 'expected_version': artwork.version,
            })
        artwork.refresh_from_db()
        self.assertEqual(artwork.title, 'Faro grande')
        self.assertEqual(len(mail.outbox), sent)

    def status_post(self, transition, **data):
        artwork = Artwork.objects.get(owner=self.owner)
        self.client.force_login(self.estafa)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                reverse('artwork_status', args=[self.event.slug, artwork.pk]), {'transition': transition, **data},
            )
        artwork.refresh_from_db()
        return artwork

    def test_each_estafa_status_change_emails_the_team_once(self):
        artwork = Artwork.objects.create(event=self.event, owner=self.owner, title='Faro', proposal='Texto')
        artwork.collaborators.add(self.collaborator)
        team = ['artista@example.com', 'colab@example.com']

        for transition, data, status, subject in (
            ('approve', {}, Artwork.Status.ACTIVE, 'Aprobamos'),
            ('reopen', {}, Artwork.Status.PENDING, 'volvió a revisión'),
            ('reject', {'message': 'Tarde.'}, Artwork.Status.REJECTED, 'Novedades'),
        ):
            mail.outbox.clear()
            artwork = self.status_post(transition, **data)
            self.assertEqual(artwork.status, status)
            self.assertEqual(len(mail.outbox), 1, transition)
            self.assertEqual(sorted(mail.outbox[0].to), team)
            self.assertIn(subject, mail.outbox[0].subject)

        # Una transición inválida no cambia nada ni manda nada.
        mail.outbox.clear()
        self.status_post('approve')
        self.assertEqual(mail.outbox, [])

    def test_checkout_verification_emails_the_team_once(self):
        artwork = Artwork.objects.create(
            event=self.event, owner=self.owner, title='Faro', proposal='Texto',
            status=Artwork.Status.CHECKOUT_SUBMITTED, checkout_completed=True, checkout_requested_at=timezone.now(),
        )
        self.client.force_login(self.estafa)
        review_url = reverse('artwork_review', args=[self.event.slug, artwork.pk])
        required = {'grant_status': artwork.grant_status, 'benefit_status': artwork.benefit_status}
        verified_at = timezone.localtime().strftime('%Y-%m-%dT%H:%M')
        for _ in range(2):
            artwork.refresh_from_db()
            with self.captureOnCommitCallbacks(execute=True):
                self.client.post(review_url, {
                    **required, 'expected_updated_at': artwork.updated_at.isoformat(), 'checkout_verified_at': verified_at,
                })
        artwork.refresh_from_db()
        self.assertEqual(artwork.status, Artwork.Status.CHECKOUT_VERIFIED)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['artista@example.com'])
        self.assertIn('Verificamos el checkout de Faro', mail.outbox[0].subject)
