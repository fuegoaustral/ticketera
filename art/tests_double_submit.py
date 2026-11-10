import uuid
from datetime import timedelta
from unittest import mock

from django.contrib.auth.models import User
from django.core import mail
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from events.models import Event
from teams.models import Team, TeamMembership
from user_profile.models import Profile

from .estafa import ESTAFA_SLUG
from .models import ArtProgram, Artwork


class ArtworkCreateIdempotencyTest(TestCase):
    """Un doble toque en "Guardar" del alta no crea la obra dos veces ni repite los emails."""

    def setUp(self):
        now = timezone.now()
        self.owner = User.objects.create_user(username='artista', email='artista@example.com')
        self.other = User.objects.create_user(username='otra', email='otra@example.com')
        self.estafa = User.objects.create_user(username='coord', email='coord@example.com')
        for index, user in enumerate((self.owner, self.other, self.estafa), start=1):
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
        ArtProgram.objects.create(
            event=self.event, is_current=True,
            registration_opens=now - timedelta(days=30), registration_closes=now + timedelta(days=1),
            proposal_deadline=now + timedelta(days=2),
        )
        self.url = reverse('artwork_create', args=[self.event.slug])

    def post(self, token, **data):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(self.url, {'title': 'Faro', 'action': 'save', 'creation_token': str(token), **data})

    def test_form_carries_a_fresh_token(self):
        self.client.force_login(self.owner)
        first = self.client.get(self.url).context['creation_token']
        second = self.client.get(self.url).context['creation_token']
        self.assertIsInstance(first, uuid.UUID)
        self.assertNotEqual(first, second)
        self.assertContains(self.client.get(self.url), 'name="creation_token"')

    def test_same_token_twice_creates_one_artwork_and_one_set_of_emails(self):
        self.client.force_login(self.owner)
        token = uuid.uuid4()
        first = self.post(token)
        artwork = Artwork.objects.get(owner=self.owner)
        self.assertRedirects(first, reverse('artwork_edit', args=[artwork.pk]), fetch_redirect_response=False)
        self.assertEqual(artwork.creation_token, token)
        sent = len(mail.outbox)
        self.assertGreater(sent, 0)

        second = self.post(token)
        self.assertRedirects(second, reverse('artwork_edit', args=[artwork.pk]), fetch_redirect_response=False)
        self.assertEqual(Artwork.objects.filter(owner=self.owner).count(), 1)
        self.assertEqual(len(mail.outbox), sent)

    def test_fresh_token_creates_another_artwork(self):
        self.client.force_login(self.owner)
        self.post(uuid.uuid4())
        self.post(uuid.uuid4())
        self.assertEqual(Artwork.objects.filter(owner=self.owner).count(), 2)

    def test_concurrent_duplicate_lands_on_the_existing_artwork(self):
        # El otro envío ganó la carrera entre la verificación previa y el guardado: el índice único lo
        # frena, y sus emails no salen.
        self.client.force_login(self.owner)
        token = uuid.uuid4()
        self.post(token)
        artwork = Artwork.objects.get(owner=self.owner)
        sent = len(mail.outbox)
        real_filter = Artwork.objects.filter
        calls = []

        def miss_first_lookup(*args, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return Artwork.objects.none()
            return real_filter(*args, **kwargs)

        with mock.patch.object(Artwork.objects, 'filter', side_effect=miss_first_lookup):
            response = self.post(token)
        self.assertRedirects(response, reverse('artwork_edit', args=[artwork.pk]), fetch_redirect_response=False)
        self.assertEqual(Artwork.objects.filter(owner=self.owner).count(), 1)
        self.assertEqual(len(mail.outbox), sent)
        # La verificación previa no la vio: fue el índice único el que la frenó.
        self.assertEqual(calls[0], {'creation_token': token})
        self.assertGreater(len(calls), 1)

    def test_someone_elses_token_does_not_leak_their_artwork(self):
        self.client.force_login(self.owner)
        token = uuid.uuid4()
        self.post(token)
        mine = Artwork.objects.get(owner=self.owner)
        self.client.force_login(self.other)
        response = self.post(token, title='Otra')
        theirs = Artwork.objects.get(owner=self.other)
        self.assertNotEqual(theirs.pk, mine.pk)
        self.assertIsNone(theirs.creation_token)
        self.assertRedirects(response, reverse('artwork_edit', args=[theirs.pk]), fetch_redirect_response=False)

    def test_missing_or_bad_token_still_creates(self):
        self.client.force_login(self.owner)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(self.url, {'title': 'Faro'})
        self.post('no-es-un-uuid')
        self.assertEqual(Artwork.objects.filter(owner=self.owner).count(), 2)

    def test_token_is_unique_in_the_database(self):
        token = uuid.uuid4()
        Artwork.objects.create(event=self.event, owner=self.owner, creation_token=token)
        with self.assertRaises(IntegrityError):
            Artwork.objects.create(event=self.event, owner=self.owner, creation_token=token)
