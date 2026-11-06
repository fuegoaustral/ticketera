import csv
import io
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from events.models import Event
from teams.models import Team, TeamMembership
from user_profile.models import Profile

from .estafa import ESTAFA_SLUG
from .models import ArtProgram, Artwork
from .steps import artwork_steps

MAD_ASSISTANCE = '¿Necesitás asistencia de MAD para mover materiales durante el evento?'
OWN_MOBILITY = '¿Tu instalación tiene movilidad propia? (art car)'


class MadMobilityTest(TestCase):
    def setUp(self):
        now = timezone.now()
        self.owner = User.objects.create_user(username='artista', email='artista@example.com')
        self.admin = User.objects.create_user(username='coord', email='coord@example.com')
        for index, user in enumerate((self.owner, self.admin), start=1):
            user.profile.document_number = f'1000000{index}'
            user.profile.phone = f'+54911000000{index:02d}'
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
            team=Team.objects.get(slug=ESTAFA_SLUG), user=self.admin, started_on=now.date() - timedelta(days=30),
        )
        self.program = ArtProgram.objects.create(
            event=self.event, is_current=True, grants_enabled=True,
            registration_opens=now - timedelta(days=30), registration_closes=now - timedelta(days=1),
            grant_opens=now - timedelta(days=30), grant_deadline=now + timedelta(days=2),
            proposal_deadline=now + timedelta(days=2),
            grant_report_deadline=now + timedelta(days=30),
        )
        self.artwork = Artwork.objects.create(event=self.event, owner=self.owner, title='Faro')

    def detalles_missing(self):
        steps = {step.key: step for step in artwork_steps(self.artwork, self.program)}
        return steps['detalles'].missing

    def test_edit_page_shows_both_toggles_in_the_mad_section(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse('artwork_edit', args=[self.artwork.pk]))
        self.assertContains(response, '<legend>Movilidad (MAD)</legend>', html=True)
        self.assertContains(response, MAD_ASSISTANCE)
        self.assertContains(response, OWN_MOBILITY)
        self.assertContains(response, 'MAD va a coordinar con vos la validación de seguridad')
        for name in ('needs_mad_assistance', 'has_own_mobility'):
            self.assertContains(response, f'name="{name}"')
        content = response.content.decode()
        self.assertLess(content.index('<legend>Camp soporte</legend>'), content.index('<legend>Movilidad (MAD)</legend>'))

    def test_saving_stores_the_answers(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse('artwork_edit', args=[self.artwork.pk]), {
            'title': 'Faro', 'needs_mad_assistance': 'on', 'has_own_mobility': 'on',
            'expected_version': self.artwork.version,
        })
        self.assertEqual(response.status_code, 302)
        self.artwork.refresh_from_db()
        self.assertTrue(self.artwork.needs_mad_assistance)
        self.assertTrue(self.artwork.has_own_mobility)

        response = self.client.post(reverse('artwork_edit', args=[self.artwork.pk]), {
            'title': 'Faro', 'expected_version': self.artwork.version,
        })
        self.assertEqual(response.status_code, 302)
        self.artwork.refresh_from_db()
        self.assertFalse(self.artwork.needs_mad_assistance)
        self.assertFalse(self.artwork.has_own_mobility)

    def test_answers_are_optional(self):
        before = self.detalles_missing()
        self.assertEqual(before, ['Descripción', 'Dimensiones', 'Materiales'])
        self.artwork.needs_mad_assistance = True
        self.artwork.has_own_mobility = True
        self.artwork.save()
        self.assertEqual(self.detalles_missing(), before)

    def test_estafa_export_includes_the_mad_columns(self):
        self.artwork.needs_mad_assistance = True
        self.artwork.save()
        Artwork.objects.create(event=self.event, owner=self.owner, title='Barco', has_own_mobility=True)
        Artwork.objects.create(event=self.event, owner=self.owner, title='Quieta')
        self.client.force_login(self.admin)
        exported = self.client.get(reverse('art_admin_export', args=[self.event.slug])).content.decode('utf-8-sig')
        rows = list(csv.DictReader(io.StringIO(exported)))
        by_title = {row['Propuesta de Arte']: row for row in rows}
        self.assertEqual(
            (by_title['Faro']['Asistencia de MAD'], by_title['Faro']['Movilidad propia (art car)']), ('Sí', 'No'),
        )
        self.assertEqual(
            (by_title['Barco']['Asistencia de MAD'], by_title['Barco']['Movilidad propia (art car)']), ('No', 'Sí'),
        )
        self.assertEqual(
            (by_title['Quieta']['Asistencia de MAD'], by_title['Quieta']['Movilidad propia (art car)']), ('No', 'No'),
        )
