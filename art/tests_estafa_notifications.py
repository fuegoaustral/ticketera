from datetime import timedelta

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from events.models import Event
from teams.models import Team, TeamMembership
from user_profile.models import Profile

from .digest import send_estafa_change_digest, send_grant_application_email
from .estafa import BECAS_SLUG, ESTAFA_SLUG, MAD_SLUG
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
            'grant_requested': 'on', 'grant_justification': 'Para la madera', 'grant_currency': 'ARS', 'expected_version': artwork.version,
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


class ArtTeamNotificationsTest(EstafaNotificationsBase):
    """Becas sólo recibe lo de las becas, MAD sólo lo de las obras que piden MAD, y ESTAFA puede apagar las becas."""

    def setUp(self):
        super().setUp()
        self.becas = self.member('becas', 'Fede', BECAS_SLUG)
        self.mad = self.member('mad', 'Gabi', MAD_SLUG)
        # Está en ESTAFA y en MAD: le vale lo de los dos equipos, en un solo email.
        self.both = self.member('ambos', 'Hugo', ESTAFA_SLUG, MAD_SLUG)

    def member(self, username, first_name, *slugs):
        user = User.objects.create_user(username=username, email=f'{username}@example.com', first_name=first_name)
        user.profile.document_number = f'4{user.pk:07d}'
        user.profile.phone = f'+5491130{user.pk:06d}'
        user.profile.profile_completion = Profile.COMPLETE
        user.profile.save()
        for slug in slugs:
            TeamMembership.objects.create(
                team=Team.objects.get(slug=slug), user=user, started_on=timezone.localdate() - timedelta(days=30),
            )
        return user

    def recipients(self, prefix):
        return sorted(message.to[0] for message in mail.outbox if message.subject.startswith(prefix))

    def test_defaults_per_team(self):
        pref = EstafaNotificationPreference.for_user(self.mad)
        self.assertTrue(pref.grant_updates)
        self.assertTrue(pref.becas_new_applications)
        self.assertTrue(pref.becas_changes)
        self.assertTrue(pref.mad_new_artworks)
        self.assertFalse(pref.mad_changes)

    def test_profile_shows_only_the_groups_of_your_teams(self):
        self.client.force_login(self.becas)
        response = self.client.get(reverse('profile'))
        self.assertContains(response, 'Emails de Becas')
        self.assertContains(response, 'name="becas_new_applications"')
        self.assertContains(response, 'name="becas_changes"')
        self.assertNotContains(response, 'name="new_artworks"')
        self.assertNotContains(response, 'name="mad_changes"')

        self.client.force_login(self.mad)
        response = self.client.get(reverse('profile'))
        self.assertContains(response, 'Emails de MAD')
        self.assertContains(response, 'name="mad_new_artworks"')
        self.assertNotContains(response, 'name="grant_updates"')
        self.assertNotContains(response, 'name="becas_changes"')

        self.client.force_login(self.both)
        response = self.client.get(reverse('profile'))
        self.assertContains(response, 'Emails de Arte')
        self.assertContains(response, 'name="grant_updates"')
        self.assertContains(response, 'name="mad_changes"')
        self.assertNotContains(response, 'name="becas_changes"')

    def test_saving_keeps_the_options_of_other_teams(self):
        self.client.force_login(self.mad)
        self.client.post(reverse('profile'), {'update_estafa_emails': '1', 'mad_changes': 'on', 'new_artworks': ''})
        pref = EstafaNotificationPreference.objects.get(user=self.mad)
        self.assertEqual((pref.mad_new_artworks, pref.mad_changes), (False, True))
        # Las opciones de ESTAFA y Becas no están en su formulario: quedan como estaban.
        self.assertTrue(pref.new_artworks)
        self.assertTrue(pref.becas_changes)

    def test_new_artwork_asking_for_mad_reaches_mad_once_per_person(self):
        self.client.force_login(self.owner)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('artwork_create', args=[self.event.slug]), {
                'title': 'Carreta', 'needs_mad_assistance': 'on',
            })
        self.assertTrue(Artwork.objects.get(title='Carreta').needs_mad_assistance)
        self.assertEqual(self.recipients('[MAD]'), ['mad@example.com'])
        # Quien está en ESTAFA y MAD recibe un solo email, el de ESTAFA. Becas, ninguno.
        self.assertEqual(self.recipients('[ESTAFA] Nueva obra'), ['ambos@example.com', 'contacto@example.com'])
        self.assertEqual(len([m for m in mail.outbox if m.to == ['ambos@example.com']]), 1)
        self.assertFalse([m for m in mail.outbox if m.to == ['becas@example.com']])
        message = next(m for m in mail.outbox if m.to == ['mad@example.com'])
        self.assertEqual(message.subject, '[MAD] «Carreta» pide MAD')
        self.assertIn('sos parte de MAD', message.body)

    def test_new_artwork_without_mad_does_not_reach_mad(self):
        self.client.force_login(self.owner)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('artwork_create', args=[self.event.slug]), {'title': 'Vudú'})
        self.assertEqual(self.recipients('[MAD]'), [])
        self.assertFalse([m for m in mail.outbox if m.to == ['mad@example.com']])

    def test_mad_is_told_when_an_artwork_starts_asking_for_mad(self):
        artwork = self.artwork()
        with self.captureOnCommitCallbacks(execute=True):
            self.save_artwork(artwork, self.owner, details='Sin MAD todavía')
        self.assertEqual(self.recipients('[MAD]'), [])

        with self.captureOnCommitCallbacks(execute=True):
            self.save_artwork(artwork, self.owner, needs_mad_assistance='on')
        self.assertEqual(self.recipients('[MAD]'), ['ambos@example.com', 'mad@example.com'])
        self.assertIn('ahora pide MAD', mail.outbox[-1].body)

        # Ya pedía MAD: guardar de nuevo, o sumar que es un art car, no vuelve a avisar.
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            self.save_artwork(artwork, self.owner, needs_mad_assistance='on', has_own_mobility='on')
        self.assertEqual(self.recipients('[MAD]'), [])

    def test_mad_can_turn_off_the_mad_emails(self):
        EstafaNotificationPreference.objects.create(user=self.mad, mad_new_artworks=False)
        artwork = self.artwork()
        with self.captureOnCommitCallbacks(execute=True):
            self.save_artwork(artwork, self.owner, is_movable='on')
        # Moverse sin motor no es un pedido a MAD.
        self.assertEqual(self.recipients('[MAD]'), [])
        with self.captureOnCommitCallbacks(execute=True):
            self.save_artwork(artwork, self.owner, has_own_mobility='on')
        self.assertEqual(self.recipients('[MAD]'), ['ambos@example.com'])

    def test_grant_application_reaches_becas_only(self):
        artwork = self.artwork()
        self.assertEqual(send_grant_application_email(artwork, self.owner), 1)
        self.assertEqual([message.to for message in mail.outbox], [['becas@example.com']])
        message = mail.outbox[0]
        self.assertEqual(message.subject, '[Becas] Nueva solicitud de beca: «Vudú»')
        self.assertIn(reverse('artwork_grant', args=[artwork.pk]), message.body)
        self.assertIn('sos parte de Becas', message.body)

        mail.outbox.clear()
        EstafaNotificationPreference.objects.create(user=self.becas, becas_new_applications=False)
        self.assertEqual(send_grant_application_email(artwork, self.owner), 0)
        self.assertEqual(mail.outbox, [])

    def test_digest_per_team(self):
        vudu = self.artwork('Vudú')
        carreta = self.artwork('Carreta', contact=self.watcher)
        carreta.has_own_mobility = True
        carreta.save()
        ArtworkChange.objects.create(artwork=vudu, section='Detalles', actor=self.owner)
        ArtworkChange.objects.create(artwork=vudu, section='Beca', actor=self.owner)
        ArtworkChange.objects.create(artwork=vudu, section='Rendición de beca', actor=self.collaborator)
        ArtworkChange.objects.create(artwork=carreta, section='Galería', actor=self.owner)
        # La persona de ESTAFA que ve todo apaga las becas; la de MAD pide los cambios; la de los dos equipos
        # sólo quiere los cambios de las obras que piden MAD.
        EstafaNotificationPreference.objects.filter(user=self.watcher).update(grant_updates=False)
        EstafaNotificationPreference.objects.create(user=self.mad, mad_changes=True)
        EstafaNotificationPreference.objects.create(user=self.both, assigned_changes=False, mad_changes=True)

        send_estafa_change_digest()
        emails = {message.to[0]: message for message in mail.outbox}
        self.assertEqual(len(mail.outbox), len(emails))
        self.assertEqual(
            set(emails),
            {'contacto@example.com', 'todo@example.com', 'becas@example.com', 'mad@example.com', 'ambos@example.com'},
        )

        # ESTAFA con becas (por defecto): la línea trae las secciones de la beca.
        self.assertIn('Detalles, Beca, Rendición de beca', emails['contacto@example.com'].body)
        # ESTAFA sin becas: las dos obras, sin las secciones de la beca.
        watcher = emails['todo@example.com'].body
        self.assertIn('Vudú', watcher)
        self.assertIn('Galería', watcher)
        self.assertNotIn('Beca', watcher)
        self.assertNotIn('Rendición de beca', watcher)
        # Becas: sólo las becas.
        becas = emails['becas@example.com']
        self.assertEqual(becas.subject, '[Becas] Cambios en «Vudú»')
        self.assertIn('Beca, Rendición de beca — lo cambiaron Anita y Beto', becas.body)
        self.assertNotIn('Detalles', becas.body)
        self.assertNotIn('Carreta', becas.body)
        # MAD: sólo las obras que piden MAD.
        mad = emails['mad@example.com']
        self.assertEqual(mad.subject, '[MAD] Cambios en «Carreta»')
        self.assertNotIn('Vudú', mad.body)
        # En ESTAFA y MAD: un solo email, con lo que le toca por MAD.
        both = emails['ambos@example.com']
        self.assertEqual(both.subject, '[ESTAFA y MAD] Cambios en «Carreta»')
        self.assertIn('sos parte de ESTAFA y MAD', both.body)

    def test_mad_gets_no_digest_by_default(self):
        carreta = self.artwork('Carreta')
        carreta.needs_mad_assistance = True
        carreta.save()
        ArtworkChange.objects.create(artwork=carreta, section='Galería', actor=self.owner)
        send_estafa_change_digest()
        self.assertNotIn('mad@example.com', [message.to[0] for message in mail.outbox])
