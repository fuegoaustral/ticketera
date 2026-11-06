from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from events.models import Event
from user_profile.models import Profile

from .models import ArtProgram, Artwork
from .steps import artwork_steps

LIABILITY_TEXT = 'Asumo la responsabilidad de mi obra sobre cualquier daño que pueda causar a terceros'
LIABILITY_MISSING = 'Aceptar la responsabilidad sobre daños a terceros'


class ThirdPartyLiabilityTest(TestCase):
    def setUp(self):
        now = timezone.now()
        self.owner = User.objects.create_user(username='artista', email='artista@example.com')
        self.owner.profile.document_number = '10000001'
        self.owner.profile.phone = '+5491100000001'
        self.owner.profile.profile_completion = Profile.COMPLETE
        self.owner.profile.save()
        Event.objects.filter(is_main=True).update(is_main=False)
        self.event = Event.objects.create(
            name='FA Carnaval', slug='fa-carnaval', active=True, is_main=True,
            start=now + timedelta(days=20), end=now + timedelta(days=24),
            transfers_enabled_until=now + timedelta(days=10), header_image='events/heros/no-image.jpg',
            title='FA', description='Evento', has_volunteers=True,
        )
        self.program = ArtProgram.objects.create(
            event=self.event, is_current=True, grants_enabled=True,
            registration_opens=now - timedelta(days=30), registration_closes=now - timedelta(days=1),
            grant_opens=now - timedelta(days=30), grant_deadline=now + timedelta(days=2),
            proposal_deadline=now + timedelta(days=2),
            understanding_letter_digital_deadline=now + timedelta(days=5),
            grant_report_deadline=now + timedelta(days=30),
        )
        self.artwork = Artwork.objects.create(event=self.event, owner=self.owner, title='Faro')

    def carta_missing(self):
        steps = {step.key: step for step in artwork_steps(self.artwork, self.program)}
        return steps['carta'].missing

    def test_unchecked_liability_counts_as_missing(self):
        self.assertFalse(self.artwork.third_party_liability_accepted)
        self.assertIn(LIABILITY_MISSING, self.carta_missing())

    def test_edit_page_shows_the_checkbox(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse('artwork_edit', args=[self.artwork.pk]))
        self.assertContains(response, LIABILITY_TEXT)
        self.assertContains(response, 'name="third_party_liability_accepted"')

    def test_checking_the_box_saves_it_and_clears_the_missing_item(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse('artwork_edit', args=[self.artwork.pk]), {
            'title': 'Faro', 'third_party_liability_accepted': 'on',
            'expected_version': self.artwork.version, 'action': 'save',
        })
        self.assertEqual(response.status_code, 302)
        self.artwork.refresh_from_db()
        self.assertTrue(self.artwork.third_party_liability_accepted)
        self.assertNotIn(LIABILITY_MISSING, self.carta_missing())
