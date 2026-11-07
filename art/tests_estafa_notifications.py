from datetime import timedelta

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from events.models import Event
from teams.models import Team, TeamMembership
from user_profile.models import Profile

from .digest import send_estafa_change_digest
from .estafa import ESTAFA_SLUG
from .models import ArtProgram, Artwork, ArtworkChange, EstafaNotificationPreference


class EstafaNotificationsBase(TestCase):
    def setUp(self):
        now = timezone.now()
        self.owner = User.objects.create_user(username='artista', email='artista@example.com', first_name='Ana')
        self.collaborator = User.objects.create_user(username='colab', email='colab@example.com', first_name='Beto')
        self.contact = User.objects.create_user(username='contacto', email='contacto@example.com', first_name='Caro')
        self.watcher = User.objects.create_user(username='todo', email='todo@example.com', first_name='Dani')
        self.quiet = User.objects.create_user(username='nada', email='nada@example.com', first_name='Eli')
        for index, user in enumerate((self.owner, self.collaborator, self.contact, self.watcher, self.quiet), start=1):
            user.profile.document_number = f'3000000{index}'
            user.profile.phone = f'+54911200000{index:02d}'
            user.profile.profile_completion = Profile.COMPLETE
            user.profile.save()
        self.owner.profile.nickname = 'Anita'
        self.owner.profile.save()
        Event.objects.filter(is_main=True).update(is_main=False)
        self.event = Event.objects.create(
            name='FA Carnaval', slug='fa-carnaval', active=True, is_main=True,
            start=now + timedelta(days=20), end=now + timedelta(days=24),
            transfers_enabled_until=now + timedelta(days=10), header_image='events/heros/no-image.jpg',
            title='FA', description='Evento', has_volunteers=True,
        )
        team = Team.objects.get(slug=ESTAFA_SLUG)
        for user in (self.contact, self.watcher, self.quiet):
            TeamMembership.objects.create(team=team, user=user, started_on=now.date() - timedelta(days=30))
        EstafaNotificationPreference.objects.create(
            user=self.watcher, new_artworks=False, assigned_changes=False, all_changes=True,
        )
        EstafaNotificationPreference.objects.create(
            user=self.quiet, new_artworks=False, assigned_changes=False, all_changes=False,
        )
        self.program = ArtProgram.objects.create(
            event=self.event, is_current=True, grants_enabled=True,
            registration_opens=now - timedelta(days=30), registration_closes=now + timedelta(days=1),
            proposal_deadline=now + timedelta(days=2), guide_deadline=now + timedelta(days=2),
            grant_opens=now - timedelta(days=30), grant_deadline=now + timedelta(days=2),
        )

    def artwork(self, title='Vudú', contact=None):
        artwork = Artwork.objects.create(
            event=self.event, owner=self.owner, title=title, estafa_contact=contact or self.contact,
        )
        artwork.collaborators.add(self.collaborator)
        return artwork

    def save_artwork(self, artwork, user, **data):
        artwork.refresh_from_db()
        self.client.force_login(user)
        return self.client.post(reverse('artwork_edit', args=[artwork.pk]), {
            'title': artwork.title, 'expected_version': artwork.version, 'action': 'save', **data,
        })


class PreferencesTest(EstafaNotificationsBase):
    def test_defaults_without_saved_preference(self):
        pref = EstafaNotificationPreference.for_user(self.contact)
        self.assertIsNone(pref.pk)
        self.assertTrue(pref.new_artworks)
        self.assertTrue(pref.assigned_changes)
        self.assertFalse(pref.all_changes)

    def test_profile_section_only_for_estafa(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse('profile'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '¿Qué emails querés recibir de las obras?')

        self.client.force_login(self.contact)
        response = self.client.get(reverse('profile'))
        self.assertContains(response, '¿Qué emails querés recibir de las obras?')
        self.assertContains(response, 'type="checkbox" name="new_artworks"')
        self.assertContains(response, 'name="new_artworks" class="form-check-input" id="id_new_artworks" checked')
        self.assertContains(response, 'name="assigned_changes" class="form-check-input" id="id_assigned_changes" checked')
        self.assertNotContains(response, 'id="id_all_changes" checked')

    def test_saving_preferences(self):
        self.client.force_login(self.contact)
        response = self.client.post(reverse('profile'), {'update_estafa_emails': '1', 'all_changes': 'on'})
        self.assertRedirects(response, reverse('profile'), fetch_redirect_response=False)
        pref = EstafaNotificationPreference.objects.get(user=self.contact)
        self.assertEqual((pref.new_artworks, pref.assigned_changes, pref.all_changes), (False, False, True))

        self.client.post(reverse('profile'), {'update_estafa_emails': '1', 'new_artworks': 'on'})
        pref.refresh_from_db()
        self.assertEqual((pref.new_artworks, pref.assigned_changes, pref.all_changes), (True, False, False))
        self.assertEqual(EstafaNotificationPreference.objects.filter(user=self.contact).count(), 1)

    def test_non_estafa_cannot_save_preferences(self):
        self.client.force_login(self.owner)
        self.client.post(reverse('profile'), {'update_estafa_emails': '1', 'all_changes': 'on'})
        self.assertFalse(EstafaNotificationPreference.objects.filter(user=self.owner).exists())


class NewArtworkEmailTest(EstafaNotificationsBase):
    def test_new_artwork_emails_members_who_want_it(self):
        self.client.force_login(self.owner)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('artwork_create', args=[self.event.slug]), {'title': 'Vudú'})
        artwork = Artwork.objects.get(title='Vudú')
        estafa = [message for message in mail.outbox if message.subject.startswith('[ESTAFA] Nueva obra inscripta')]
        # Sólo quien tiene "Obras nuevas" (por defecto); una persona por email, sin listas en To.
        self.assertEqual([message.to for message in estafa], [['contacto@example.com']])
        message = estafa[0]
        self.assertEqual(message.subject, '[ESTAFA] Nueva obra inscripta: «Vudú»')
        self.assertIn(reverse('artwork_review', args=[self.event.slug, artwork.pk]), message.body)
        self.assertIn('Anita', message.body)
        self.assertIn('Revisar obra', message.body)
        self.assertIn(f"{reverse('profile')}#emails-estafa", message.body)

    def test_no_email_outside_fuego_austral(self):
        self.event.has_volunteers = False
        self.event.save()
        self.client.force_login(self.owner)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('artwork_create', args=[self.event.slug]), {'title': 'Vudú'})
        self.assertFalse([message for message in mail.outbox if message.subject.startswith('[ESTAFA] Nueva obra inscripta')])


class ChangeRecordingTest(EstafaNotificationsBase):
    def test_artist_save_records_changed_sections(self):
        artwork = self.artwork()
        self.save_artwork(artwork, self.collaborator, materials='Madera', public_title='Vudú de noche')
        changes = ArtworkChange.objects.filter(artwork=artwork)
        self.assertEqual(sorted(changes.values_list('section', flat=True)), ['Detalles', 'Folleto'])
        self.assertEqual(set(changes.values_list('actor', flat=True)), {self.collaborator.pk})

    def test_unchanged_save_records_nothing(self):
        artwork = self.artwork()
        response = self.save_artwork(artwork, self.owner)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ArtworkChange.objects.exists())

    def test_estafa_save_records_nothing(self):
        artwork = self.artwork()
        response = self.save_artwork(artwork, self.contact, materials='Hierro')
        self.assertEqual(response.status_code, 302)
        artwork.refresh_from_db()
        self.assertEqual(artwork.materials, 'Hierro')
        self.assertFalse(ArtworkChange.objects.exists())

    def test_grant_save_records_beca(self):
        artwork = self.artwork()
        self.client.force_login(self.owner)
        self.client.post(reverse('artwork_grant', args=[artwork.pk]), {
            'grant_requested': 'on', 'grant_justification': 'Para la madera', 'expected_version': artwork.version,
        })
        self.assertEqual(list(ArtworkChange.objects.values_list('section', flat=True)), ['Beca'])


class DigestTest(EstafaNotificationsBase):
    def setUp(self):
        super().setUp()
        self.vudu = self.artwork('Vudú')
        self.faro = self.artwork('Faro', contact=self.watcher)
        ArtworkChange.objects.create(artwork=self.vudu, section='Detalles', actor=self.owner)
        ArtworkChange.objects.create(artwork=self.vudu, section='Beca', actor=self.collaborator)
        ArtworkChange.objects.create(artwork=self.vudu, section='Detalles', actor=self.owner)
        ArtworkChange.objects.create(artwork=self.faro, section='Galería', actor=self.owner)

    def by_recipient(self):
        return {message.to[0]: message for message in mail.outbox}

    def test_one_email_per_recipient_with_assigned_vs_all(self):
        self.assertEqual(send_estafa_change_digest(), 2)
        emails = self.by_recipient()
        self.assertEqual(set(emails), {'contacto@example.com', 'todo@example.com'})

        # Asignadas: sólo Vudú, una línea con las secciones sin repetir y quiénes cambiaron.
        assigned = emails['contacto@example.com']
        self.assertEqual(assigned.subject, '[ESTAFA] Cambios en «Vudú»')
        html = assigned.body
        self.assertIn('Detalles, Beca — lo cambiaron Anita y Beto', html)
        self.assertNotIn('Faro', html)
        self.assertIn(reverse('artwork_review', args=[self.event.slug, self.vudu.pk]), html)

        # Todas: las dos obras en un solo email.
        everything = emails['todo@example.com']
        self.assertEqual(everything.subject, '[ESTAFA] Cambios en 2 obras')
        html = everything.body
        self.assertIn('Galería — lo cambió Anita', html)
        self.assertIn('Detalles, Beca', html)

        self.assertFalse(ArtworkChange.objects.filter(digested_at__isnull=True).exists())

    def test_no_self_notification(self):
        # Una persona de ESTAFA que también edita la obra como parte del equipo.
        self.vudu.collaborators.add(self.contact)
        ArtworkChange.objects.all().delete()
        ArtworkChange.objects.create(artwork=self.vudu, section='Folleto', actor=self.contact)
        send_estafa_change_digest()
        emails = self.by_recipient()
        self.assertNotIn('contacto@example.com', emails)
        self.assertIn('lo cambió Caro', emails['todo@example.com'].body)

    def test_idempotent(self):
        send_estafa_change_digest()
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(send_estafa_change_digest(), 0)
        # Aunque los cambios no hubieran quedado marcados, la reserva evita el segundo envío.
        ArtworkChange.objects.update(digested_at=None)
        self.assertEqual(send_estafa_change_digest(), 0)
        self.assertEqual(len(mail.outbox), 2)

    def test_new_changes_after_a_run_go_in_the_next_one(self):
        send_estafa_change_digest()
        mail.outbox.clear()
        ArtworkChange.objects.create(artwork=self.vudu, section='Checkout', actor=self.owner)
        self.assertEqual(send_estafa_change_digest(), 2)
        self.assertIn('Checkout — lo cambió Anita', self.by_recipient()['contacto@example.com'].body)

    def test_management_command(self):
        from django.core.management import call_command
        from io import StringIO

        out = StringIO()
        call_command('send_estafa_change_digest', stdout=out)
        self.assertIn('Resúmenes enviados: 2', out.getvalue())
