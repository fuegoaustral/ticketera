"""El formulario principal guarda en su propia dirección aunque la página venga de un formulario en línea."""
import re
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .estafa import ESTAFA_SLUG
from .models import ArtProgram, Artwork
from events.models import Event
from teams.models import Team, TeamMembership
from user_profile.models import Profile


def form_action(response, form_id):
    html = response.content.decode()
    tag = re.search(r'<form[^>]*\bid="%s"[^>]*>' % re.escape(form_id), html)
    assert tag, f'No form #{form_id} in the page'
    action = re.search(r'\baction="([^"]*)"', tag.group(0))
    return action and action.group(1)


class MainFormActionTest(TestCase):
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
            registration_opens=now - timedelta(days=30), registration_closes=now + timedelta(days=10),
            grant_opens=now - timedelta(days=30), grant_deadline=now + timedelta(days=2),
            proposal_deadline=now + timedelta(days=2),
            grant_report_deadline=now + timedelta(days=30),
        )
        self.artwork = Artwork.objects.create(event=self.event, owner=self.owner, title='Faro')
        self.edit_url = reverse('artwork_edit', args=[self.artwork.pk])

    def test_team_add_error_keeps_the_main_form_posting_to_the_artwork(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse('artwork_team_add', args=[self.artwork.pk]), {'identifier': ''})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(form_action(response, 'artwork-form'), self.edit_url)

        # "Guardar" desde esa página guarda la propuesta.
        self.artwork.refresh_from_db()
        saved = self.client.post(form_action(response, 'artwork-form'), {
            'title': 'Faro nuevo', 'expected_version': self.artwork.version, 'action': 'save',
        })
        self.assertEqual(saved.status_code, 302)
        self.artwork.refresh_from_db()
        self.assertEqual(self.artwork.title, 'Faro nuevo')

    def test_photo_upload_error_keeps_the_main_form_posting_to_the_artwork(self):
        self.program.gallery_opens = timezone.now() - timedelta(days=1)
        self.program.save(update_fields=['gallery_opens'])
        self.client.force_login(self.owner)
        response = self.client.post(reverse('artwork_photo_upload', args=[self.artwork.pk]), {})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(form_action(response, 'artwork-form'), self.edit_url)

    def test_new_artwork_form_posts_to_create(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse('artwork_create', args=[self.event.slug]))
        self.assertEqual(form_action(response, 'artwork-form'), reverse('artwork_create', args=[self.event.slug]))

    def test_grant_item_error_keeps_the_grant_form_posting_to_the_grant_page(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse('grant_item_create', args=[self.artwork.pk, 'budget']), {})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'art/grant.html')
        self.assertEqual(form_action(response, 'grant-form'), reverse('artwork_grant', args=[self.artwork.pk]))

    def test_review_inline_error_keeps_the_review_form_posting_to_the_review(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse('artwork_team_add', args=[self.artwork.pk]), {'identifier': '', 'return_to': 'review'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'art/review.html')
        form_id = response.context['form'].FORM_ID
        self.assertEqual(
            form_action(response, form_id), reverse('artwork_review', args=[self.event.slug, self.artwork.pk]),
        )


class LiveStepCountTest(TestCase):
    """La pantalla recuenta cada paso con las reglas de FIELD_CHECKS, que llegan desde el servidor."""

    def test_carta_live_rules_mirror_missing(self):
        from .steps import OPEN, Step, _missing
        artwork = Artwork(title='Faro')
        step = Step('carta', 'Declaración', 'Declaración', OPEN, 'Abierto', missing=_missing('carta', artwork))
        live = step.live_fields
        self.assertEqual(live['unit'], 'paso')
        self.assertEqual(
            [rule['name'] for rule in live['rules']], ['third_party_liability_accepted', 'understanding_letter'],
        )
        self.assertFalse(any(rule['saved'] for rule in live['rules']))
        # La copia física la marca ESTAFA: queda como llegó.
        self.assertEqual(live['fixed'], ['Entregar la copia física'])
        self.assertEqual(step.missing_count, 'Faltan 3 pasos')

    def test_steps_without_checks_have_no_live_rules(self):
        from .steps import OPEN, Step
        self.assertIsNone(Step('equipo', 'Equipo', 'Equipo', OPEN, 'Abierto').live_fields)
