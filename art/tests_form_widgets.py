import re
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from events.models import Event
from user_profile.models import Profile

from .models import ArtProgram, Artwork


class FormWidgetsTest(TestCase):
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
            registration_opens=now - timedelta(days=30), registration_closes=now + timedelta(days=1),
            grant_opens=now - timedelta(days=30), grant_deadline=now + timedelta(days=2),
            proposal_deadline=now + timedelta(days=2),
            understanding_letter_digital_deadline=now + timedelta(days=5),
            grant_report_deadline=now + timedelta(days=30),
        )
        self.client.force_login(self.owner)

    @staticmethod
    def detalles(response):
        return {step.key: step for step in response.context['steps']}['detalles']

    def test_saving_an_empty_new_form_does_not_count_the_title_twice(self):
        url = reverse('artwork_create', args=[self.event.slug])
        before = self.detalles(self.client.get(url))
        response = self.client.post(url, {'title': '', 'action': 'save'})
        self.assertEqual(response.status_code, 200)
        after = self.detalles(response)
        self.assertEqual(len(after.missing), len(before.missing), after.missing)
        self.assertEqual(after.missing_count, before.missing_count)

    def test_saving_an_existing_form_without_title_does_not_add_a_field(self):
        artwork = Artwork.objects.create(event=self.event, owner=self.owner, title='')
        url = reverse('artwork_edit', args=[artwork.pk])
        before = self.detalles(self.client.get(url))
        response = self.client.post(url, {'title': '', 'expected_version': artwork.version, 'action': 'save'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.detalles(response).missing, before.missing)

    def test_every_checkbox_on_the_edit_page_is_a_toggle(self):
        artwork = Artwork.objects.create(event=self.event, owner=self.owner, title='Faro')
        page = self.client.get(reverse('artwork_edit', args=[artwork.pk])).content.decode()
        checkboxes = re.findall(r'<input type="checkbox"[^>]*>', page)
        names = {re.search(r'name="([^"]+)"', box).group(1) for box in checkboxes}
        for name in ('wants_support_camp', 'third_party_liability_accepted', 'uses_fire', 'uses_sound', 'burns'):
            self.assertIn(name, names)
        for box in checkboxes:
            self.assertIn('role="switch"', box)
        for name in ('wants_support_camp', 'third_party_liability_accepted'):
            self.assertRegex(page, rf'<div class="form-check form-switch fire-switch">\s*<input type="checkbox" name="{name}"')
