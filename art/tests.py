from datetime import timedelta
from decimal import Decimal
from importlib import import_module
from unittest.mock import patch

from allauth.account.models import EmailAddress
from auditlog.models import LogEntry
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django.contrib.messages import get_messages

from .forms import (
    ArtworkContactForm, ArtworkForm, ArtworkGrantItemForm, ArtworkPhotoUploadForm, ArtworkProviderForm, ArtworkReviewForm,
)
from .estafa import ESTAFA_SLUG
from .reminders import send_art_reminders
from .steps import artwork_steps, next_step
from .models import (
    ArtProgram, Artwork, ArtworkGrantItem,
    ArtworkCheckoutPhoto, ArtworkPhoto, ArtworkProvider,
    ArtworkProviderVehicle,
)
from events.models import Event
from tickets.models import NewTicket, Order, TicketType
from teams.models import Team, TeamMembership
from user_profile.models import Profile


class ArtworkFlowTest(TestCase):
    @staticmethod
    def image(name):
        return SimpleUploadedFile(name, b'GIF87a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;', content_type='image/gif')

    def setUp(self):
        now = timezone.now()
        self.owner = User.objects.create_user(username='artista', email='artista@example.com')
        self.collaborator = User.objects.create_user(username='colab', email='colab@example.com')
        self.admin = User.objects.create_user(username='coord', email='coord@example.com')
        self.stranger = User.objects.create_user(username='otra', email='otra@example.com')
        for index, user in enumerate((self.owner, self.collaborator, self.admin, self.stranger), start=1):
            user.profile.document_number = f'1000000{index}'
            user.profile.phone = f'+54911000000{index:02d}'
            user.profile.profile_completion = Profile.COMPLETE
            user.profile.save()
        Event.objects.filter(is_main=True).update(is_main=False)
        self.event = Event.objects.create(
            name='FA Carnaval', slug='fa-carnaval', active=True, is_main=True,
            start=now + timedelta(days=20), end=now + timedelta(days=24),
            transfers_enabled_until=now + timedelta(days=10), header_image='events/heros/no-image.jpg', title='FA', description='Evento',
            has_volunteers=True,
        )
        # self.admin coordina Arte como miembro de ESTAFA, no como admin del evento.
        TeamMembership.objects.create(
            team=Team.objects.get(slug=ESTAFA_SLUG), user=self.admin, started_on=now.date() - timedelta(days=30),
        )
        self.program = ArtProgram.objects.create(
            event=self.event, is_current=True, grants_enabled=True,
            registration_closes=now - timedelta(days=1),
            grant_deadline=now + timedelta(days=2),
            proposal_deadline=now + timedelta(days=2),
            grant_report_deadline=now + timedelta(days=30),
        )

    def artwork_form(self, data, artwork=None, actor=None):
        artwork = artwork or Artwork(event=self.event, owner=self.owner)
        return ArtworkForm(
            data,
            instance=artwork,
            program=self.program,
            owner=artwork.owner,
            actor=actor or self.owner,
        )

    def complete_profile(self, user):
        user.profile.document_number = f'2000000{user.pk}'
        user.profile.phone = f'+54911100000{user.pk:02d}'
        user.profile.profile_completion = Profile.COMPLETE
        user.profile.save()
        return user

    def test_estafa_access_is_for_active_members_on_fuego_austral_events(self):
        dashboard_url = reverse('art_admin_dashboard', args=[self.event.slug])
        event_admin = self.complete_profile(User.objects.create_user(username='evadmin', email='evadmin@example.com'))
        self.event.admins.add(event_admin)
        self.client.force_login(event_admin)
        self.assertEqual(self.client.get(dashboard_url).status_code, 403)
        self.assertEqual(self.client.get(reverse('estafa_home')).status_code, 403)

        former = self.complete_profile(User.objects.create_user(username='ex', email='ex@example.com'))
        today = timezone.localdate()
        TeamMembership.objects.create(
            team=Team.objects.get(slug=ESTAFA_SLUG), user=former,
            started_on=today - timedelta(days=400), ended_on=today - timedelta(days=1),
        )
        self.client.force_login(former)
        self.assertEqual(self.client.get(dashboard_url).status_code, 403)

        self.client.force_login(self.admin)
        self.assertRedirects(self.client.get(reverse('estafa_home')), dashboard_url)
        dashboard = self.client.get(dashboard_url)
        self.assertContains(dashboard, 'ESTAFA')
        for removed_filter in ('art-filter-grant', 'art-filter-fire', 'art-filter-sound'):
            self.assertNotContains(dashboard, removed_filter)
        self.assertRedirects(
            self.client.get(f'/mi-fuego/mis-eventos/{self.event.slug}/arte/?status=draft'),
            f'{dashboard_url}?status=draft', status_code=301,
        )
        self.event.has_volunteers = False
        self.event.save(update_fields=['has_volunteers'])
        self.assertEqual(self.client.get(dashboard_url).status_code, 403)

        superuser = User.objects.create_superuser(username='root', email='root@example.com', password='x')
        self.client.force_login(superuser)
        self.assertEqual(self.client.get(dashboard_url).status_code, 200)

    def test_estafa_contact_choices_are_active_members_and_the_current_contact(self):
        artwork = Artwork.objects.create(event=self.event, owner=self.owner, title='Faro')
        self.assertEqual(list(ArtworkContactForm(instance=artwork).fields['estafa_contact'].queryset), [self.admin])
        # Quien dejó ESTAFA sigue siendo el contacto hasta que se elija a otra persona.
        artwork.estafa_contact = self.stranger
        self.assertEqual(
            set(ArtworkContactForm(instance=artwork).fields['estafa_contact'].queryset), {self.admin, self.stranger},
        )
        self.assertNotIn('estafa_contact', self.artwork_form({}, artwork=artwork, actor=self.admin).fields)

    @patch('art.views.send_mail')
    def test_estafa_assigns_a_contact_the_team_sees(self, send_mail):
        contact = self.complete_profile(User.objects.create_user(
            username='juana', email='juana@example.com', first_name='Juana', last_name='Coord',
        ))
        TeamMembership.objects.create(team=Team.objects.get(slug=ESTAFA_SLUG), user=contact, started_on=timezone.localdate())
        artwork = Artwork.objects.create(event=self.event, owner=self.owner, title='Faro')
        artwork.collaborators.add(self.collaborator)
        contact_url = reverse('artwork_contact', args=[self.event.slug, artwork.pk])
        edit_url = reverse('artwork_edit', args=[artwork.pk])
        self.client.force_login(self.owner)
        self.assertNotContains(self.client.get(edit_url), 'Tu contacto en ESTAFA')

        self.assertEqual(self.client.post(contact_url, {'estafa_contact': self.admin.pk}).status_code, 403)
        artwork.refresh_from_db()
        self.assertIsNone(artwork.estafa_contact)

        self.client.force_login(self.admin)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(contact_url, {'estafa_contact': contact.pk})
        self.assertRedirects(response, reverse('artwork_review', args=[self.event.slug, artwork.pk]))
        artwork.refresh_from_db()
        self.assertEqual(artwork.estafa_contact, contact)
        # Sólo se avisa al equipo de la instalación; responder le escribe al contacto.
        send_mail.assert_called_once()
        sent = send_mail.call_args.kwargs
        self.assertEqual(sent['template_name'], 'art_contact_assigned')
        self.assertEqual(set(sent['recipient_list']), {'artista@example.com', 'colab@example.com'})
        self.assertEqual(sent['headers'], {'Reply-To': 'juana@example.com'})
        self.assertContains(self.client.get(reverse('art_admin_dashboard', args=[self.event.slug])), '<td>Juana Coord</td>', html=True)

        # Guardar el mismo contacto no vuelve a avisar.
        send_mail.reset_mock()
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(contact_url, {'estafa_contact': contact.pk})
        send_mail.assert_not_called()

        self.client.force_login(contact)
        dashboard = self.client.get(reverse('art_admin_dashboard', args=[self.event.slug]))
        self.assertEqual(list(dashboard.context['assigned_artworks']), [artwork])

        self.client.force_login(self.collaborator)
        response = self.client.get(edit_url)
        self.assertContains(response, 'Tu contacto en ESTAFA')
        self.assertContains(response, '<strong>Juana Coord</strong>')
        self.assertContains(response, 'href="mailto:juana@example.com"')
        self.assertNotContains(response, 'name="estafa_contact"')
        mine = self.client.get(reverse('art_dashboard'))
        self.assertContains(mine, '<span class="text-muted">Contacto de ESTAFA:</span> Juana Coord')
        self.assertNotContains(mine, 'Responsable:')
        self.assertNotContains(mine, 'Instalación inscripta')

        self.client.force_login(self.admin)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(contact_url, {'estafa_contact': ''})
        send_mail.assert_not_called()
        artwork.refresh_from_db()
        self.assertIsNone(artwork.estafa_contact)
        self.assertContains(self.client.get(reverse('art_admin_dashboard', args=[self.event.slug])), '<td><span class="text-muted">Sin contacto</span></td>', html=True)

        self.client.post(contact_url, {'estafa_contact': self.stranger.pk})
        artwork.refresh_from_db()
        self.assertIsNone(artwork.estafa_contact)

    def test_participant_dashboard_has_no_coordination_content(self):
        Artwork.objects.create(event=self.event, owner=self.owner, title='Instalación ajena')
        self.client.force_login(self.admin)
        response = self.client.get(reverse('art_dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Instalación ajena')
        self.assertNotContains(response, 'Instalaciones para revisar')
        self.assertContains(response, 'Todavía no tenés instalaciones.')

    def test_primary_fields_are_associated_with_artwork_form(self):
        form = self.artwork_form({})
        self.assertNotIn('kind', form.fields)
        self.assertEqual(form.fields['public_description'].max_length, 200)
        self.assertEqual(form.fields['public_description'].widget.attrs['maxlength'], 200)
        for field in form.fields.values():
            self.assertEqual(field.widget.attrs.get('form'), 'artwork-form')

    def open_logistics(self):
        self.program.logistics_opens = timezone.now() - timedelta(hours=1)
        self.program.save(update_fields=['logistics_opens'])

    def open_registration(self):
        self.program.registration_closes = timezone.now() + timedelta(days=1)
        self.program.save(update_fields=['registration_closes'])

    def test_new_artwork_form_is_blank_until_the_first_valid_save(self):
        self.open_registration()
        self.client.force_login(self.owner)
        url = reverse('artwork_create', args=[self.event.slug])
        for _ in range(3):
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Podés cambiar el nombre cuando quieras.')
        self.assertContains(response, 'Guardá la instalación para subir archivos.')
        self.assertFalse(Artwork.objects.exists())

        response = self.client.post(url, {'title': '', 'action': 'save'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].has_error('title'))
        self.assertFalse(Artwork.objects.exists())

    def test_public_submission_requires_only_title_and_is_planned(self):
        self.open_registration()
        incomplete = self.artwork_form({})
        self.assertFalse(incomplete.is_valid())
        self.client.force_login(self.owner)
        response = self.client.post(reverse('artwork_create', args=[self.event.slug]), {
            'kind': Artwork.Kind.POPUP, 'title': 'Faro',
        })
        artwork = Artwork.objects.get(owner=self.owner)
        self.assertRedirects(response, reverse('artwork_edit', args=[artwork.pk]))
        self.assertEqual(artwork.event, self.event)
        self.assertEqual(artwork.kind, Artwork.Kind.PLANNED)
        self.assertEqual(artwork.status, Artwork.Status.PENDING)
        self.assertEqual(artwork.proposal, '')
        self.assertEqual(artwork.operations_group.lider, self.owner)

    def test_first_save_creates_a_draft(self):
        self.open_registration()
        self.client.force_login(self.owner)
        response = self.client.post(reverse('artwork_create', args=[self.event.slug]), {
            'title': 'Borrador', 'action': 'save',
        })
        artwork = Artwork.objects.get(owner=self.owner)
        self.assertRedirects(response, reverse('artwork_edit', args=[artwork.pk]))
        self.assertEqual(artwork.title, 'Borrador')
        self.assertEqual(artwork.status, Artwork.Status.PENDING)
        self.assertEqual([str(message) for message in get_messages(response.wsgi_request)], [
            'La instalación quedó inscripta. Queda pendiente de aprobación por ESTAFA. '
            'Podés seguir modificándola libremente a medida que la instalación avance.',
        ])

    def test_save_keeps_the_status_and_confirms_the_changes(self):
        artwork = Artwork.objects.create(event=self.event, owner=self.owner, title='Faro')
        self.client.force_login(self.owner)
        response = self.client.post(reverse('artwork_edit', args=[artwork.pk]), {
            'title': 'Borrador', 'expected_version': artwork.version, 'action': 'save',
        })

        self.assertEqual(response.status_code, 302)
        artwork.refresh_from_db()
        self.assertEqual(artwork.title, 'Borrador')
        self.assertEqual(artwork.status, Artwork.Status.PENDING)
        self.assertEqual([str(message) for message in get_messages(response.wsgi_request)], ['Cambios guardados.'])

    def test_estafa_edits_another_persons_artwork_from_within_estafa(self):
        artwork = Artwork.objects.create(event=self.event, owner=self.owner, title='Faro')
        artwork.collaborators.add(self.collaborator)
        url = reverse('artwork_edit', args=[artwork.pk])
        review_url = reverse('artwork_review', args=[self.event.slug, artwork.pk])
        notice = 'Estás viendo la instalación de <strong>artista@example.com</strong> como ESTAFA.'

        for user in (self.owner, self.collaborator):
            self.client.force_login(user)
            page = self.client.get(url)
            self.assertNotContains(page, notice, html=False)
            self.assertContains(page, 'Volver a Arte')
            self.assertContains(page, 'ESTAFA está revisando tu inscripción.')

        self.client.force_login(self.admin)
        page = self.client.get(url)
        self.assertContains(page, notice, html=False)
        self.assertContains(page, f'href="{review_url}"><i class="fas fa-arrow-left me-1" aria-hidden="true"></i> Volver a ESTAFA')
        self.assertContains(page, 'Menú de ESTAFA')
        self.assertNotContains(page, 'ESTAFA está revisando tu inscripción.')

        response = self.client.post(url, {'title': 'Faro nuevo', 'expected_version': artwork.version, 'action': 'save'})
        self.assertRedirects(response, url, fetch_redirect_response=False)
        artwork.refresh_from_db()
        self.assertEqual(artwork.title, 'Faro nuevo')
        entry = LogEntry.objects.get_for_object(artwork).filter(action=LogEntry.Action.UPDATE).latest('timestamp')
        self.assertEqual(entry.actor, self.admin)

        # ESTAFA sólo coordina Fuego Austral; en otros eventos, sólo superusers.
        self.event.has_volunteers = False
        self.event.save(update_fields=['has_volunteers'])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(User.objects.create_superuser(username='root', email='root@example.com', password='x'))
        self.assertContains(self.client.get(url), notice, html=False)

    def test_save_confirmation_is_rendered_in_an_accessible_status_line(self):
        artwork = Artwork.objects.create(event=self.event, owner=self.owner, title='Faro')
        self.client.force_login(self.owner)
        response = self.client.post(reverse('artwork_edit', args=[artwork.pk]), {
            'title': 'Faro', 'expected_version': artwork.version, 'action': 'save',
        }, follow=True)

        self.assertContains(response, 'role="status" aria-live="polite"')
        self.assertContains(response, 'Cambios guardados.')
        self.assertNotContains(response, 'Guardar borrador')
        self.assertNotContains(response, 'Guardar y enviar propuesta')
        self.assertNotContains(self.client.get(reverse('artwork_edit', args=[artwork.pk])), 'Cambios guardados.')

    def test_saving_a_fire_artwork_requires_its_safety_details(self):
        artwork = Artwork.objects.create(event=self.event, owner=self.owner, title='Faro')
        self.client.force_login(self.owner)
        response = self.client.post(reverse('artwork_edit', args=[artwork.pk]), {
            'title': 'Faro', 'uses_fire': 'on', 'expected_version': artwork.version,
        })

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].has_error('extinguishing_plan'))
        artwork.refresh_from_db()
        self.assertFalse(artwork.uses_fire)

    def test_creation_can_include_safety_budget_gallery_and_team(self):
        self.program.registration_closes = timezone.now() + timedelta(days=1)
        self.program.save(update_fields=['registration_closes'])
        self.client.force_login(self.owner)
        self.client.post(reverse('artwork_create', args=[self.event.slug]), {'title': 'Faro'})
        artwork = Artwork.objects.get(owner=self.owner)
        self.open_logistics()
        editor = self.client.get(reverse('artwork_edit', args=[artwork.pk]))
        self.assertContains(editor, 'Agregar proveedor')
        self.assertContains(editor, 'Agregar vehículo', count=0)
        response = self.client.post(reverse('artwork_edit', args=[artwork.pk]), {
            'title': 'Faro', 'uses_fire': 'on', 'fire_details': 'Leña controlada',
            'extinguishing_plan': 'Matafuegos ABC', 'safety_responsible_email': self.collaborator.email,
            'expected_version': artwork.version,
        })
        self.assertEqual(response.status_code, 302)
        artwork.refresh_from_db()
        self.client.post(reverse('grant_item_create', args=[artwork.pk, 'budget']), {
            'item_type': 'materials', 'concept': 'Hierro', 'amount': '1500', 'currency': 'ARS',
            'exchange_rate': '1', 'rate_date': timezone.localdate(),
        })
        self.client.post(reverse('artwork_file_upload', args=[artwork.pk]), {'files': self.image('inicio.gif')})
        self.client.post(reverse('artwork_team_add', args=[artwork.pk]), {'identifier': '10000002'})
        self.assertEqual(artwork.safety_responsible, self.collaborator)
        self.assertEqual(artwork.grant_items.count(), 1)
        self.assertEqual(artwork.files.count(), 1)
        self.assertEqual(
            set(artwork.team_members().values_list('user__email', flat=True)),
            {self.owner.email, self.collaborator.email},
        )

    def steps_by_key(self, artwork):
        return {step.key: step for step in artwork_steps(artwork, self.program)}

    def test_steps_follow_the_program_dates_and_fall_back_to_the_event(self):
        artwork = Artwork.objects.create(event=self.event, owner=self.owner, title='Faro')
        steps = self.steps_by_key(artwork)
        self.assertEqual(list(steps), ['detalles', 'equipo', 'desplegable', 'carta', 'ingreso', 'galeria', 'checkout'])
        self.assertEqual(steps['detalles'].state, 'open')
        self.assertEqual(steps['detalles'].missing, ['Descripción', 'Dimensiones', 'Materiales'])
        # Sin cierre propio, el desplegable cierra cuando empieza el evento y lo dice.
        self.assertEqual(steps['desplegable'].deadline, self.event.start)
        self.assertTrue(steps['desplegable'].when.startswith('Abierto · hasta el '))
        # Ingreso anticipado espera la fecha de ESTAFA y nunca la anuncia.
        self.assertEqual(steps['ingreso'].state, 'upcoming')
        self.assertEqual(steps['ingreso'].when, 'Te avisamos cuando se habilite')
        self.program.logistics_opens = timezone.now() + timedelta(days=3)
        self.program.save(update_fields=['logistics_opens'])
        self.assertEqual(self.steps_by_key(artwork)['ingreso'].when, 'Te avisamos cuando se habilite')
        self.assertEqual(steps['galeria'].state, 'upcoming')
        self.assertEqual(steps['checkout'].state, 'upcoming')

        self.program.proposal_deadline = timezone.now() - timedelta(days=1)
        self.program.save(update_fields=['proposal_deadline'])
        steps = self.steps_by_key(artwork)
        self.assertEqual(steps['detalles'].state, 'closed')
        self.assertEqual(steps['detalles'].missing, [])
        self.assertEqual(next_step(list(steps.values())).key, 'desplegable')

    def test_burning_asks_when_and_with_whom(self):
        artwork = Artwork.objects.create(
            event=self.event, owner=self.owner, title='Faro', proposal='Una pata', dimensions='3 m', materials='Madera',
        )
        self.client.force_login(self.owner)
        response = self.client.post(reverse('artwork_edit', args=[artwork.pk]), {
            'title': 'Faro', 'proposal': 'Una pata', 'dimensions': '3 m', 'materials': 'Madera',
            'burns': 'on', 'expected_version': artwork.version,
        })
        self.assertEqual(response.status_code, 302)
        artwork.refresh_from_db()
        self.assertTrue(artwork.burns)
        self.assertEqual(self.steps_by_key(artwork)['detalles'].missing, ['Cuándo preferís quemarla', 'Si la quemás sola o con otras'])
        response = self.client.post(reverse('artwork_edit', args=[artwork.pk]), {
            'title': 'Faro', 'proposal': 'Una pata', 'dimensions': '3 m', 'materials': 'Madera',
            'burns': 'on', 'burn_preferred_time': 'Sábado a la noche', 'burn_company': Artwork.BurnCompany.SHARED,
            'expected_version': artwork.version,
        })
        self.assertEqual(response.status_code, 302)
        artwork.refresh_from_db()
        self.assertEqual(artwork.burn_company, Artwork.BurnCompany.SHARED)
        self.assertTrue(self.steps_by_key(artwork)['detalles'].complete)

        # La galería usa su propio cierre si ESTAFA lo carga.
        self.program.gallery_deadline = self.event.end + timedelta(days=10)
        self.program.save(update_fields=['gallery_deadline'])
        self.assertEqual(self.steps_by_key(artwork)['galeria'].deadline, self.program.gallery_deadline)

    def test_timeline_shows_open_steps_as_cards_and_the_rest_as_rows(self):
        artwork = Artwork.objects.create(event=self.event, owner=self.owner, title='Faro')
        self.client.force_login(self.owner)
        response = self.client.get(reverse('artwork_edit', args=[artwork.pk]))
        self.assertContains(response, 'Lo próximo')
        self.assertContains(response, 'Completá Detalles de la instalación')
        self.assertContains(response, '<section class="step-card" id="detalles"', html=False)
        self.assertContains(response, 'Te avisamos cuando se habilite')
        self.assertNotContains(response, 'Agregar proveedor')
        self.assertNotContains(response, 'Editar como ESTAFA')
        self.assertContains(response, 'Guardar primero')

        # ESTAFA ve lo mismo: puede corregir un paso cerrado, pero lo que no se habilitó no lo edita nadie.
        self.client.force_login(self.admin)
        response = self.client.get(reverse('artwork_edit', args=[artwork.pk]))
        self.assertNotContains(response, 'Lo próximo')
        self.assertNotContains(response, 'Editar como ESTAFA')
        self.assertNotContains(response, 'Agregar proveedor')
        self.program.proposal_deadline = timezone.now() - timedelta(days=1)
        self.program.save(update_fields=['proposal_deadline'])
        response = self.client.get(reverse('artwork_edit', args=[artwork.pk]))
        self.assertContains(response, 'Editar como ESTAFA', count=1)
        self.assertContains(response, 'Este paso ya cerró para el equipo.')

    def test_nobody_edits_a_step_before_it_opens(self):
        artwork = Artwork.objects.create(event=self.event, owner=self.owner, title='Faro', status=Artwork.Status.ACTIVE)
        self.client.force_login(self.admin)
        provider = {
            'company_name': 'Fletes', 'contact_first_name': 'Luz', 'contact_last_name': 'Ríos',
            'email': 'fletes@example.com', 'phone': '+5491199999999', 'service_description': 'Traslado',
            'for_entry': 'on', 'early_entry_at': '2030-01-01T09:00', 'early_exit_at': '2030-01-01T18:00',
        }
        self.assertEqual(self.client.post(reverse('artwork_provider_create', args=[artwork.pk]), provider).status_code, 403)
        photo = {'stage': ArtworkPhoto.Stage.PROCESS, 'images': self.image('armado.gif')}
        self.assertEqual(self.client.post(reverse('artwork_photo_upload', args=[artwork.pk]), photo).status_code, 403)
        form = self.artwork_form({
            'title': 'Faro', 'checkout_notes': 'Antes de tiempo', 'expected_version': artwork.version,
        }, artwork=artwork, actor=self.admin)
        self.assertTrue(form.fields['checkout_notes'].disabled)
        self.assertFalse(form.is_valid())
        # Un paso cerrado, en cambio, ESTAFA lo puede corregir.
        self.program.proposal_deadline = timezone.now() - timedelta(days=1)
        self.program.save(update_fields=['proposal_deadline'])
        form = self.artwork_form({'title': 'Faro corregido', 'expected_version': artwork.version}, artwork=artwork, actor=self.admin)
        self.assertFalse(form.fields['title'].disabled)
        self.assertTrue(form.is_valid(), form.errors)

    def test_logistics_and_gallery_wait_for_their_dates(self):
        artwork = Artwork.objects.create(event=self.event, owner=self.owner, title='Faro', status=Artwork.Status.ACTIVE)
        self.client.force_login(self.owner)
        provider = {
            'company_name': 'Fletes', 'contact_first_name': 'Luz', 'contact_last_name': 'Ríos',
            'email': 'fletes@example.com', 'phone': '+5491199999999', 'service_description': 'Traslado',
            'for_entry': 'on', 'early_entry_at': '2030-01-01T09:00', 'early_exit_at': '2030-01-01T18:00',
        }
        self.assertEqual(self.client.post(reverse('artwork_provider_create', args=[artwork.pk]), provider).status_code, 403)
        photo = {'stage': ArtworkPhoto.Stage.PROCESS, 'images': self.image('armado.gif')}
        self.assertEqual(self.client.post(reverse('artwork_photo_upload', args=[artwork.pk]), photo).status_code, 403)

        self.open_logistics()
        self.assertEqual(self.client.post(reverse('artwork_provider_create', args=[artwork.pk]), provider).status_code, 302)
        # Cuando empieza el evento se abre la galería y cierra el ingreso anticipado.
        self.event.start = timezone.now() - timedelta(hours=1)
        self.event.save(update_fields=['start'])
        self.assertEqual(self.client.post(reverse('artwork_provider_create', args=[artwork.pk]), provider).status_code, 403)
        photo['images'] = self.image('armado.gif')
        self.assertEqual(self.client.post(reverse('artwork_photo_upload', args=[artwork.pk]), photo).status_code, 302)
        self.assertEqual(artwork.artwork_providers.count(), 1)
        self.assertEqual(artwork.photos.count(), 1)

    def test_proposal_files_accept_images_and_pdfs_only(self):
        artwork = Artwork.objects.create(event=self.event, owner=self.owner, title='Faro')
        self.client.force_login(self.owner)
        url = reverse('artwork_file_upload', args=[artwork.pk])
        pdf = SimpleUploadedFile('plano.pdf', b'%PDF-1.4 plano', content_type='application/pdf')
        response = self.client.post(url, {'files': [self.image('boceto.gif'), pdf]})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(sorted(artwork.files.values_list('name', flat=True)), ['boceto.gif', 'plano.pdf'])
        self.assertEqual({f.name: f.is_image for f in artwork.files.all()}, {'boceto.gif': True, 'plano.pdf': False})

        response = self.client.post(url, {'files': SimpleUploadedFile('notas.docx', b'x')})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'no es una imagen ni un PDF')
        self.assertEqual(artwork.files.count(), 2)

        artwork_file = artwork.files.get(name='plano.pdf')
        self.client.post(reverse('artwork_file_delete', args=[artwork.pk, artwork_file.pk]))
        self.assertEqual(artwork.files.count(), 1)

    def test_grant_page_and_main_form_never_blank_each_other(self):
        artwork = Artwork.objects.create(
            event=self.event, owner=self.owner, title='Faro', proposal='Una pata',
            grant_requested=True, grant_justification='Madera',
        )
        self.client.force_login(self.owner)
        response = self.client.post(reverse('artwork_edit', args=[artwork.pk]), {
            'title': 'Faro', 'proposal': 'Una pata gigante', 'expected_version': artwork.version, 'action': 'beca',
        })
        self.assertRedirects(response, reverse('artwork_grant', args=[artwork.pk]), fetch_redirect_response=False)
        artwork.refresh_from_db()
        self.assertEqual((artwork.proposal, artwork.grant_justification), ('Una pata gigante', 'Madera'))

        response = self.client.post(reverse('artwork_grant', args=[artwork.pk]), {
            'grant_requested': 'on', 'grant_justification': 'Madera y tela', 'expected_version': artwork.version,
        })
        self.assertRedirects(response, reverse('artwork_grant', args=[artwork.pk]), fetch_redirect_response=False)
        artwork.refresh_from_db()
        self.assertEqual((artwork.proposal, artwork.grant_justification), ('Una pata gigante', 'Madera y tela'))

    def test_step_status_counts_what_is_on_screen(self):
        # Instalación nueva: sin nombre no puede estar completa.
        self.open_registration()
        self.client.force_login(self.owner)
        response = self.client.get(reverse('artwork_create', args=[self.event.slug]))
        self.assertContains(response, 'Faltan 4 campos')
        self.assertNotContains(response, 'Completo')

        # Lo guardado está completo, pero el nombre se borró y volvió con error.
        artwork = Artwork.objects.create(
            event=self.event, owner=self.owner, title='Faro', proposal='Una pata', dimensions='3 m', materials='Madera',
        )
        steps = self.steps_by_key(artwork)
        self.assertTrue(steps['detalles'].complete)
        response = self.client.post(reverse('artwork_edit', args=[artwork.pk]), {
            'title': '', 'proposal': 'Una pata', 'dimensions': '3 m', 'materials': 'Madera', 'expected_version': artwork.version,
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Falta 1 campo')
        self.assertContains(response, '<a href="#id_title">Nombre de la instalación</a>', html=True)

    def test_errors_are_summarized_and_marked_on_the_field(self):
        artwork = Artwork.objects.create(event=self.event, owner=self.owner, title='Faro')
        self.client.force_login(self.owner)
        response = self.client.post(reverse('artwork_edit', args=[artwork.pk]), {
            'title': 'Faro', 'uses_fire': 'on', 'expected_version': artwork.version,
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No se guardó. Revisá los campos marcados en rojo')
        self.assertContains(response, 'aria-invalid="true"')
        self.assertContains(response, 'id="id_fire_details-error"')

    def test_physical_copy_is_dated_when_estafa_receives_it(self):
        artwork = Artwork.objects.create(event=self.event, owner=self.owner, title='Faro')
        form = ArtworkReviewForm({
            'understanding_letter_physical_received': 'on', 'understanding_letter_physical_custodian': 'Juan Pérez',
            'grant_status': artwork.grant_status, 'benefit_status': artwork.benefit_status,
            'expected_updated_at': artwork.updated_at.isoformat(),
        }, instance=artwork)
        self.assertTrue(form.is_valid(), form.errors)
        artwork = form.save()
        self.assertEqual(artwork.understanding_letter_physical_received_at, timezone.localdate())
        self.client.force_login(self.owner)
        response = self.client.get(reverse('artwork_edit', args=[artwork.pk]))
        self.assertContains(response, 'ESTAFA recibió la copia física')
        self.assertContains(response, f"La recibió Juan Pérez el {timezone.localdate():%d/%m/%Y}.")

    def team_artwork(self):
        self.client.force_login(self.owner)
        self.program.registration_closes = timezone.now() + timedelta(days=1)
        self.program.save(update_fields=['registration_closes'])
        self.client.post(reverse('artwork_create', args=[self.event.slug]), {'title': 'Faro'})
        return Artwork.objects.get(owner=self.owner)

    def give_ticket(self, user):
        ticket_type = TicketType.objects.create(event=self.event, name='General', price=Decimal('10.00'), ticket_count=10)
        order = Order.objects.create(
            first_name='A', last_name='B', email=user.email, phone='1111111111', dni='30111222',
            amount=Decimal('10.00'), event=self.event, user=user, status=Order.OrderStatus.CONFIRMED,
        )
        return NewTicket.objects.create(event=self.event, order=order, ticket_type=ticket_type, owner=user, holder=user)

    @patch('art.views.send_mail')
    def test_team_early_entry_and_late_checkout_per_person(self, send_mail):
        artwork = self.team_artwork()
        self.client.post(reverse('artwork_team_add', args=[artwork.pk]), {'identifier': self.collaborator.email})
        group = artwork.operations_group
        group.ingreso_anticipado_amount, group.late_checkout_amount = 1, 1
        group.save(update_fields=['ingreso_anticipado_amount', 'late_checkout_amount'])
        leader = group.miembros.get(user=self.owner)
        collaborator = group.miembros.get(user=self.collaborator)
        url = reverse('artwork_team_benefits', args=[artwork.pk])
        day = (timezone.localtime(self.event.start) - timedelta(days=2)).date()

        # Cerrado hasta que ESTAFA lo habilita.
        self.assertEqual(self.client.post(url, {f'early_{leader.pk}': day}).status_code, 403)
        self.open_logistics()
        self.give_ticket(self.owner)
        page = self.client.get(reverse('artwork_edit', args=[artwork.pk]))
        self.assertContains(page, 'Quién entra antes y quién se queda después')
        self.assertContains(page, 'Necesita su bono', count=1)

        # Fuera de rango: error en el campo, nada guardado.
        response = self.client.post(url, {f'early_{leader.pk}': self.event.start.date() + timedelta(days=3)})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'cuando empieza el evento')
        leader.refresh_from_db()
        self.assertIsNone(leader.ingreso_anticipado_fecha)

        # Quien no tiene bono no se puede marcar, aunque se envíe.
        response = self.client.post(url, {
            f'early_{leader.pk}': day, f'late_{leader.pk}': 'on', f'late_{collaborator.pk}': 'on',
        })
        self.assertEqual(response.status_code, 302)
        leader.refresh_from_db()
        collaborator.refresh_from_db()
        self.assertEqual((leader.ingreso_anticipado_fecha, leader.ingreso_anticipado, leader.late_checkout), (day, True, True))
        self.assertFalse(collaborator.late_checkout)

        # Los cupos se respetan.
        self.give_ticket(self.collaborator)
        response = self.client.post(url, {f'early_{leader.pk}': day, f'early_{collaborator.pk}': day})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'cupos de ingreso anticipado')

    @patch('art.views.send_mail')
    def test_team_is_the_artwork_group_and_people_join_by_email_or_document(self, send_mail):
        artwork = self.team_artwork()
        self.assertEqual(artwork.operations_group.lider, self.owner)
        self.assertEqual(artwork.operations_group.tipo.nombre, 'ARTE')
        self.assertTrue(artwork.is_team_member(self.owner))

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse('artwork_team_add', args=[artwork.pk]), {'identifier': '10.000.002'})
        self.assertEqual(response.url, f"{reverse('artwork_edit', args=[artwork.pk])}#equipo")
        self.assertTrue(artwork.is_team_member(self.collaborator))
        self.assertFalse(artwork.can_edit(self.collaborator))
        self.assertEqual(send_mail.call_args.kwargs['template_name'], 'art_team_member_added')
        self.assertEqual(send_mail.call_args.kwargs['recipient_list'], [self.collaborator.email])
        self.assertFalse(send_mail.call_args.kwargs['context']['can_edit'])
        self.assertFalse(send_mail.call_args.kwargs['context']['missing_ticket'])

        EmailAddress.objects.create(user=self.stranger, email='otra.cuenta@example.com', verified=True)
        self.client.post(reverse('artwork_team_add', args=[artwork.pk]), {'identifier': 'OTRA.cuenta@example.com'})
        self.assertTrue(artwork.is_team_member(self.stranger))

        duplicate = self.client.post(reverse('artwork_team_add', args=[artwork.pk]), {'identifier': self.collaborator.email})
        self.assertContains(duplicate, 'Esa persona ya es parte del equipo de la instalación.')
        missing = self.client.post(reverse('artwork_team_add', args=[artwork.pk]), {'identifier': 'nadie@example.com'})
        self.assertContains(missing, 'No encontramos una cuenta con ese email o DNI.')
        self.assertContains(missing, 'aria-invalid="true"')
        self.assertEqual(artwork.team_members().count(), 3)

        self.client.force_login(self.stranger)
        self.assertContains(self.client.get(reverse('art_dashboard')), 'Faro')
        page = self.client.get(reverse('artwork_edit', args=[artwork.pk]))
        self.assertContains(page, 'Puede ver')
        self.assertContains(page, 'podés ver la instalación. Para editarla, pedile permiso')
        self.assertNotContains(page, reverse('artwork_team_add', args=[artwork.pk]))
        self.assertTrue(page.context['form'].fields['title'].disabled)
        # Solo ver no es lo mismo que una sección cerrada: sin insignias, sin Guardar, sin cargas.
        self.assertNotContains(page, 'badge bg-secondary')
        self.assertNotContains(page, 'value="save"')
        self.assertNotContains(page, reverse('artwork_photo_upload', args=[artwork.pk]))
        self.assertNotContains(page, 'Agregar ítem')
        response = self.client.post(reverse('grant_item_create', args=[artwork.pk, 'budget']), {
            'item_type': 'materials', 'concept': 'Hierro', 'amount': '1500', 'currency': 'ARS',
            'exchange_rate': '1', 'rate_date': timezone.localdate(),
        })
        self.assertEqual(response.status_code, 403)
        self.assertFalse(artwork.grant_items.exists())

    def test_team_roles_limit_who_adds_removes_and_grants_editing(self):
        artwork = self.team_artwork()
        self.client.post(reverse('artwork_team_add', args=[artwork.pk]), {'identifier': self.collaborator.email})
        self.client.post(reverse('artwork_team_add', args=[artwork.pk]), {'identifier': self.stranger.email})
        editor = artwork.team_members().get(user=self.collaborator)
        viewer = artwork.team_members().get(user=self.stranger)
        leader = artwork.team_members().get(user=self.owner)

        self.client.force_login(self.stranger)
        extra = User.objects.create_user(username='extra', email='extra@example.com')
        self.assertEqual(self.client.post(reverse('artwork_team_add', args=[artwork.pk]), {'identifier': extra.email}).status_code, 403)
        self.assertEqual(self.client.post(reverse('artwork_team_access', args=[artwork.pk, viewer.pk]), {'can_edit': 'on'}).status_code, 403)

        self.client.force_login(self.owner)
        self.client.post(reverse('artwork_team_access', args=[artwork.pk, editor.pk]), {'can_edit': 'on'})
        self.assertTrue(artwork.can_edit(self.collaborator))
        self.assertEqual(self.client.post(reverse('artwork_team_remove', args=[artwork.pk, leader.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse('artwork_team_access', args=[artwork.pk, leader.pk])).status_code, 403)

        self.client.force_login(self.collaborator)
        self.assertEqual(self.client.post(reverse('artwork_team_add', args=[artwork.pk]), {'identifier': extra.email}).status_code, 302)
        self.assertEqual(self.client.post(reverse('artwork_team_access', args=[artwork.pk, viewer.pk]), {'can_edit': 'on'}).status_code, 403)
        page = self.client.get(reverse('artwork_edit', args=[artwork.pk]))
        self.assertNotContains(page, f'id="team-edit-{viewer.pk}"')
        self.assertEqual(self.client.post(reverse('artwork_team_remove', args=[artwork.pk, viewer.pk])).status_code, 302)
        self.assertFalse(artwork.is_team_member(self.stranger))

        self.client.force_login(self.admin)
        self.client.post(reverse('artwork_team_remove', args=[artwork.pk, editor.pk]))
        self.assertFalse(artwork.is_team_member(self.collaborator))
        self.assertFalse(artwork.can_edit(self.collaborator))

    def test_team_flags_missing_tickets_once_sales_start(self):
        artwork = self.team_artwork()
        self.client.post(reverse('artwork_team_add', args=[artwork.pk]), {'identifier': self.collaborator.email})
        page = self.client.get(reverse('artwork_edit', args=[artwork.pk]))
        self.assertNotContains(page, 'bono para este evento')

        TicketType.objects.create(
            event=self.event, name='Futura', price=Decimal('10.00'), ticket_count=10,
            date_from=timezone.now() + timedelta(days=1),
        )
        self.assertNotContains(self.client.get(reverse('artwork_edit', args=[artwork.pk])), 'bono para este evento')

        self.give_ticket(self.collaborator)
        page = self.client.get(reverse('artwork_edit', args=[artwork.pk]))
        self.assertContains(page, 'Todavía no tenés tu bono para este evento')
        self.assertContains(page, reverse('event_home', args=[self.event.slug]))
        self.assertNotContains(page, 'Todavía no tiene bono para este evento')

    def test_joining_a_group_needs_no_ticket_but_early_entry_does(self):
        from events.models import GrupoMiembro

        artwork = self.team_artwork()
        member = GrupoMiembro.objects.create(grupo=artwork.operations_group, user=self.collaborator)
        member.ingreso_anticipado = True
        with self.assertRaisesMessage(ValidationError, 'Para tener ingreso anticipado o late checkout necesita uno.'):
            member.save()
        ticket = self.give_ticket(self.collaborator)
        member.save()
        member.late_checkout = True
        member.save()

        # Desvincular el bono saca los beneficios, pero la persona sigue en el equipo.
        self.complete_profile(self.collaborator)
        self.client.force_login(self.collaborator)
        self.client.post(reverse('unassign_ticket', args=[ticket.key]))
        member.refresh_from_db()
        self.assertFalse(member.ingreso_anticipado or member.late_checkout)
        self.assertTrue(artwork.is_team_member(self.collaborator))

    def test_public_description_limit_is_configurable(self):
        self.program.public_description_max_length = 10
        self.program.save(update_fields=['public_description_max_length'])
        form = self.artwork_form({'title': 'Faro', 'public_description': '12345678901'})
        self.assertFalse(form.is_valid())
        self.assertIn('public_description', form.errors)

        existing = Artwork.objects.create(
            event=self.event, owner=self.owner, title='Existente', public_description='12345678901',
        )
        unchanged = self.artwork_form({
            'title': 'Existente editada', 'public_description': existing.public_description,
            'expected_version': existing.version,
        }, artwork=existing)
        self.assertTrue(unchanged.is_valid(), unchanged.errors)

    def test_planned_registration_is_closed(self):
        self.client.force_login(self.owner)
        url = reverse('artwork_create', args=[self.event.slug])
        self.assertRedirects(self.client.get(url), reverse('art_dashboard'))
        response = self.client.post(url, {'title': 'Faro'})
        self.assertRedirects(response, reverse('art_dashboard'))
        self.assertFalse(Artwork.objects.filter(owner=self.owner).exists())

    def test_stale_collaborator_update_is_rejected(self):
        artwork = Artwork.objects.create(event=self.event, owner=self.owner, title='Original', proposal='Texto')
        artwork.collaborators.add(self.collaborator)
        owner_form = self.artwork_form({
            'kind': Artwork.Kind.POPUP, 'title': 'Primero', 'proposal': 'Texto', 'expected_version': 1,
        }, artwork=artwork)
        self.assertTrue(owner_form.is_valid(), owner_form.errors)
        owner_form.save()

        stale = self.artwork_form({
            'kind': Artwork.Kind.POPUP, 'title': 'Pisa cambios', 'proposal': 'Texto', 'expected_version': 1,
        }, artwork=artwork, actor=self.collaborator)
        self.assertFalse(stale.is_valid())
        self.assertIn('Otra persona guardó', str(stale.non_field_errors()))

    def test_itemized_grant_uses_frozen_decimal_exchange_rate(self):
        artwork = Artwork.objects.create(event=self.event, owner=self.owner, grant_requested=True)
        self.assertTrue(ArtworkGrantItemForm(
            instance=ArtworkGrantItem(artwork=artwork), phase=ArtworkGrantItem.Phase.BUDGET,
        ).fields['images'].widget.allow_multiple_selected)
        self.assertTrue(ArtworkPhotoUploadForm().fields['images'].widget.allow_multiple_selected)
        ars_form = ArtworkGrantItemForm({
            'item_type': 'materials', 'concept': 'Hierro', 'amount': '1000.25', 'currency': 'ARS',
            'exchange_rate': '999', 'rate_date': timezone.localdate(), 'rate_source': 'No aplica',
        }, instance=ArtworkGrantItem(artwork=artwork, created_by=self.owner), phase=ArtworkGrantItem.Phase.BUDGET)
        self.assertTrue(ars_form.is_valid(), ars_form.errors)
        ars = ars_form.save()
        self.assertEqual(ars.exchange_rate, Decimal('1'))
        self.assertEqual(ars.amount_ars, Decimal('1000.25'))

        localized = ArtworkGrantItemForm({
            'item_type': 'materials', 'concept': 'Placa', 'amount': '150.000,50', 'currency': 'ARS',
            'exchange_rate': '1', 'rate_date': timezone.localdate(), 'rate_source': '',
        }, instance=ArtworkGrantItem(artwork=artwork, created_by=self.owner), phase=ArtworkGrantItem.Phase.BUDGET)
        self.assertTrue(localized.is_valid(), localized.errors)
        self.assertEqual(localized.cleaned_data['amount'], Decimal('150000.50'))

        large = ArtworkGrantItemForm({
            'item_type': 'other', 'concept': 'Equipo', 'amount': '1.000.000,00', 'currency': 'ARS',
            'exchange_rate': '1', 'rate_date': timezone.localdate(), 'rate_source': '',
        }, instance=ArtworkGrantItem(artwork=artwork), phase=ArtworkGrantItem.Phase.BUDGET)
        self.assertFalse(large.is_valid())
        self.assertIn('confirm_large_amount', large.errors)

        usd_form = ArtworkGrantItemForm({
            'item_type': 'service', 'concept': 'LEDs', 'amount': '10.50', 'currency': 'USD',
            'exchange_rate': '1234.5678', 'rate_date': timezone.localdate(), 'rate_source': 'BNA vendedor',
        }, instance=ArtworkGrantItem(artwork=artwork, created_by=self.owner), phase=ArtworkGrantItem.Phase.BUDGET)
        self.assertTrue(usd_form.is_valid(), usd_form.errors)
        usd = usd_form.save()
        self.assertEqual(usd.amount_ars, Decimal('12962.96'))
        self.assertEqual(artwork.budget_total_ars, Decimal('13963.21'))

        missing_source = ArtworkGrantItemForm({
            'item_type': 'other', 'concept': 'Tela', 'amount': '2', 'currency': 'USD',
            'exchange_rate': '1000', 'rate_date': timezone.localdate(),
        }, instance=ArtworkGrantItem(artwork=artwork), phase=ArtworkGrantItem.Phase.BUDGET)
        self.assertFalse(missing_source.is_valid())

        previous_update = usd.updated_at.isoformat()
        ArtworkGrantItem.objects.filter(pk=usd.pk).update(amount=20, updated_at=timezone.now())
        usd.refresh_from_db()
        stale = ArtworkGrantItemForm({
            'item_type': usd.item_type, 'concept': usd.concept, 'amount': '30', 'currency': 'USD',
            'exchange_rate': usd.exchange_rate, 'rate_date': usd.rate_date,
            'rate_source': usd.rate_source, 'expected_updated_at': previous_update,
        }, instance=usd, phase=ArtworkGrantItem.Phase.BUDGET)
        self.assertFalse(stale.is_valid())

        artwork.owner = self.owner
        artwork.grant_requested = True
        artwork.grant_justification = 'Necesitamos apoyo para producir la obra.'
        artwork.save()
        self.client.force_login(self.owner)
        response = self.client.post(reverse('grant_submit', args=[artwork.pk]))
        self.assertEqual(response.url, f"{reverse('artwork_grant', args=[artwork.pk])}#solicitud")
        artwork.refresh_from_db()
        self.assertEqual(artwork.grant_status, Artwork.GrantStatus.PENDING)

    def test_save_bar_lifts_chat_bubble(self):
        artwork = Artwork.objects.create(event=self.event, owner=self.owner, title='Faro')
        self.client.force_login(self.owner)
        page = self.client.get(reverse('artwork_edit', args=[artwork.pk]))
        self.assertContains(page, 'class="save-bar', count=1)
        self.assertContains(page, 'data-sticky-actions>', count=1)
        self.assertContains(page, '--sticky-actions-height')

    def test_permissions_team_and_multiple_photo_upload(self):
        artwork = Artwork.objects.create(event=self.event, owner=self.owner, title='Faro', proposal='Texto')
        self.client.force_login(self.stranger)
        self.assertEqual(self.client.get(reverse('artwork_edit', args=[artwork.pk])).status_code, 404)

        artwork.collaborators.add(self.collaborator, self.admin)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse('artwork_edit', args=[artwork.pk])).status_code, 200)

        invited = self.complete_profile(User.objects.create_user(username='invitada', email='invitada@example.com'))
        response = self.client.post(reverse('artwork_team_add', args=[artwork.pk]), {'identifier': 'Invitada@Example.com'})
        self.assertEqual(response.status_code, 302)
        artwork.refresh_from_db()
        self.assertTrue(artwork.is_team_member(invited))
        self.assertFalse(artwork.can_edit(invited))
        self.client.force_login(invited)
        self.assertEqual(self.client.get(reverse('artwork_edit', args=[artwork.pk])).status_code, 200)
        self.client.force_login(self.owner)
        member = artwork.team_members().get(user=invited)
        self.client.post(reverse('artwork_team_access', args=[artwork.pk, member.pk]), {'can_edit': 'on'})
        self.assertTrue(artwork.can_edit(invited))
        self.client.force_login(invited)

        # La galería se habilita cuando empieza el evento.
        self.event.start = timezone.now() - timedelta(hours=1)
        self.event.save(update_fields=['start'])
        image = self.image('obra.gif')
        response = self.client.post(reverse('artwork_photo_upload', args=[artwork.pk]), {
            'stage': ArtworkPhoto.Stage.PROCESS,
            'caption': 'En construcción',
            'images': image,
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(artwork.photos.count(), 1)

        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('art_admin_dashboard', args=[self.event.slug])).status_code, 200)
        self.assertEqual(self.client.get(reverse('artwork_review', args=[self.event.slug, artwork.pk])).status_code, 200)

        self.assertFalse(ArtworkPhotoUploadForm({'stage': ArtworkPhoto.Stage.PROCESS}, {}).is_valid())

    def test_people_show_by_nickname_then_full_name_then_email(self):
        from art.templatetags.art_format import person_full_name, person_label, person_name

        user = User.objects.create_user(username='sin-nombre', email='sin@example.com')
        self.assertEqual((person_name(user), person_full_name(user), person_label(user)), ('sin@example.com', '', 'sin@example.com'))
        user.first_name, user.last_name = 'Juan Bautista', 'Sanchez'
        self.assertEqual((person_name(user), person_full_name(user)), ('Juan Bautista Sanchez', ''))
        user.profile.nickname = 'Juancho'
        self.assertEqual(person_name(user), 'Juancho')
        self.assertEqual(person_label(user), 'Juancho (Juan Bautista Sanchez)')
        self.assertEqual(person_name(None), '')

    def test_admin_list_shows_people_by_name_and_searches_them(self):
        self.owner.first_name, self.owner.last_name = 'Ana', 'Artista'
        self.owner.save()
        self.owner.profile.nickname = 'Anita'
        self.owner.profile.save()
        Artwork.objects.create(event=self.event, owner=self.owner, title='Faro')
        self.client.force_login(self.admin)
        url = reverse('art_admin_dashboard', args=[self.event.slug])
        dashboard = self.client.get(url)
        self.assertContains(dashboard, 'Anita')
        self.assertNotContains(dashboard, self.owner.email)
        self.assertNotContains(dashboard, 'No solicitada')
        self.assertNotContains(dashboard, '<th>Beca</th>', html=True)
        for query in ('anita', 'artista', self.owner.email):
            self.assertEqual([a.title for a in self.client.get(url, {'q': query}).context['artworks']], ['Faro'], query)

    def test_review_sections_mark_complete_when_required_fields_are_filled(self):
        from art.review_sections import COMPLETE, MISSING, UPCOMING, review_sections

        artwork = Artwork.objects.create(event=self.event, owner=self.owner, title='Faro')

        def section(key):
            return {item.key: item for item in review_sections(artwork, self.program)}[key]

        self.assertEqual(section('propuesta').status, MISSING)
        self.assertEqual(section('propuesta').missing, ['Descripción', 'Dimensiones', 'Materiales'])
        artwork.proposal, artwork.dimensions, artwork.materials = 'Una torre', '3 × 3', 'Madera'
        self.assertEqual(section('propuesta').status, COMPLETE)
        artwork.uses_fire = True
        self.assertIn('Plan de extinción', section('propuesta').missing)

        self.assertEqual(section('declaracion').missing, ['Declaración digital', 'Declaración física'])
        artwork.understanding_letter_physical_waiver = True
        self.assertEqual(section('declaracion').missing, ['Declaración digital'])

        self.assertEqual(section('checkout').status, UPCOMING)
        artwork.status = Artwork.Status.CHECKOUT_VERIFIED
        self.assertEqual((section('checkout').status, section('checkout').summary), (COMPLETE, 'Verificado'))
        self.assertEqual([item.key for item in review_sections(artwork, self.program)], [
            'declaracion', 'propuesta', 'equipo', 'galeria', 'desplegable', 'logistica', 'checkout', 'beneficio',
        ])

    def test_review_page_shows_the_team_content_and_saves_estafa_fields(self):
        artwork = Artwork.objects.create(
            event=self.event, owner=self.owner, title='Faro', proposal='Una torre de luz', dimensions='3 × 3',
        )
        self.client.force_login(self.admin)
        url = reverse('artwork_review', args=[self.event.slug, artwork.pk])
        page = self.client.get(url)
        self.assertContains(page, 'Una torre de luz')
        self.assertContains(page, 'Pendiente: materiales')
        self.assertContains(page, 'form="estafa-review"')
        self.assertNotContains(page, reverse('artwork_photo_upload', args=[artwork.pk]))

        response = self.client.post(url, {
            'expected_updated_at': artwork.updated_at.isoformat(), 'assigned_location': 'Playa norte',
            'benefit_status': artwork.benefit_status,
        })
        self.assertRedirects(response, url)
        artwork.refresh_from_db()
        self.assertEqual(artwork.assigned_location, 'Playa norte')

    def test_event_admin_edits_the_art_program_by_step(self):
        from django.contrib import admin as django_admin
        from django.test import RequestFactory

        from art.admin import ArtProgramInline

        superuser = User.objects.create_superuser(username='root', email='root@example.com', password='x')
        self.client.force_login(superuser)
        page = self.client.get(reverse('admin:events_event_change', args=[self.event.pk]))
        for title in ('Inscripción', 'Detalles', 'Desplegable y placement', 'Declaración de entendimiento',
                      'Logística', 'Galería y checkout', 'Becas', 'Recordatorios'):
            self.assertContains(page, f'<h2>{title}</h2>', html=True)
        self.assertContains(page, 'Puede ser posterior al cierre de inscripción')
        self.assertContains(page, 'Logística es el ingreso anticipado, el late checkout y los proveedores.')
        self.assertNotContains(page, 'Apertura de declaración')
        program_page = self.client.get(reverse('admin:art_artprogram_change', args=[self.program.pk]))
        self.assertContains(program_page, 'Apertura de becas')

        # Sin tocar el bloque, guardar un evento no crea un programa.
        other = Event.objects.create(
            name='Otro', slug='otro', start=self.event.start, end=self.event.end,
            transfers_enabled_until=self.event.transfers_enabled_until,
            header_image='events/heros/no-image.jpg', title='Otro', description='Evento',
        )
        request = RequestFactory().get('/')
        request.user = superuser
        formset_class = ArtProgramInline(Event, django_admin.site).get_formset(request, other)
        blank = formset_class(instance=other)
        data = {f'{blank.prefix}-{key}': value for key, value in (
            ('TOTAL_FORMS', '1'), ('INITIAL_FORMS', '0'), ('MIN_NUM_FORMS', '0'), ('MAX_NUM_FORMS', '1'),
        )}
        for name, field in blank.forms[0].fields.items():
            value = blank.forms[0].get_initial_for_field(field, name)
            if value is True:
                data[f'{blank.prefix}-0-{name}'] = 'on'
            elif value is not None and value is not False:
                data[f'{blank.prefix}-0-{name}'] = field.prepare_value(value)
            if field.show_hidden_initial:
                # El navegador devuelve el valor inicial oculto de los campos con default calculado.
                data[f'initial-{blank.prefix}-0-{name}'] = field.prepare_value(value)
        formset = formset_class(data, instance=other)
        self.assertTrue(formset.is_valid(), formset.errors)
        formset.save()
        self.assertFalse(ArtProgram.objects.filter(event=other).exists())

    def test_grants_wait_for_their_opening(self):
        self.assertEqual(self.program.checkpoint_state('grant'), 'open')
        self.program.grant_opens = timezone.now() + timedelta(days=1)
        self.assertEqual(self.program.checkpoint_state('grant'), 'upcoming')

    def test_admin_dashboard_filters_and_exports_by_estafa_contact(self):
        contact = User.objects.create_user(
            username='juana', email='juana@example.com', first_name='Juana', last_name='Coord',
        )
        TeamMembership.objects.create(team=Team.objects.get(slug=ESTAFA_SLUG), user=contact, started_on=timezone.localdate())
        Artwork.objects.create(event=self.event, owner=self.owner, title='De Juana', estafa_contact=contact)
        Artwork.objects.create(event=self.event, owner=self.owner, title='Mía', estafa_contact=self.admin)
        Artwork.objects.create(event=self.event, owner=self.owner, title='Sin nadie')
        self.client.force_login(self.admin)
        url = reverse('art_admin_dashboard', args=[self.event.slug])

        def titles(contact_filter):
            return [artwork.title for artwork in self.client.get(url, {'contact': contact_filter}).context['artworks']]

        self.assertEqual(titles(''), ['De Juana', 'Mía', 'Sin nadie'])
        self.assertEqual(titles('me'), ['Mía'])
        self.assertEqual(titles('none'), ['Sin nadie'])
        self.assertEqual(titles(str(contact.pk)), ['De Juana'])
        dashboard = self.client.get(url)
        self.assertIn((str(contact.pk), 'Juana Coord'), dashboard.context['contact_choices'])
        self.assertNotContains(dashboard, 'Etiquetas')
        self.assertNotContains(dashboard, 'Modalidad')

        exported = self.client.get(reverse('art_admin_export', args=[self.event.slug]), {
            'contact': contact.pk,
        }).content.decode('utf-8-sig')
        self.assertIn('Contacto de ESTAFA', exported)
        self.assertIn('Declaración de entendimiento digital', exported)
        self.assertIn('juana@example.com', exported)
        self.assertNotIn('Sin nadie', exported)
        self.assertNotIn('Etiquetas', exported)

    def test_structured_logistics_checkout_and_grant_item_images(self):
        artwork = Artwork.objects.create(
            event=self.event, owner=self.owner, title='Faro', proposal='Texto', grant_requested=True,
            status=Artwork.Status.ACTIVE,
        )
        self.program.checkout_opens = timezone.now() - timedelta(hours=1)
        self.program.save(update_fields=['checkout_opens'])
        self.open_logistics()
        self.client.force_login(self.owner)
        entry_at = timezone.localtime().replace(hour=9, minute=30, second=0, microsecond=0)
        early_exit_at = entry_at + timedelta(hours=8, minutes=30)
        dismantling_entry_at = entry_at + timedelta(days=4)
        dismantling_exit_at = dismantling_entry_at + timedelta(hours=9)

        response = self.client.post(reverse('artwork_team_add', args=[artwork.pk]), {'identifier': self.collaborator.email})
        self.assertEqual(response.status_code, 302)

        invalid_provider = self.client.post(reverse('artwork_provider_create', args=[artwork.pk]), {
            'company_name': 'Sin operación', 'contact_first_name': 'Luz', 'contact_last_name': 'Ríos',
            'email': 'sin-operacion@example.com', 'phone': '+5491199999999',
            'service_description': 'Traslado de estructura',
        })
        self.assertEqual(invalid_provider.status_code, 200)
        self.assertContains(invalid_provider, 'Elegí si el proveedor participa del ingreso')
        self.assertEqual(ArtworkProvider.objects.filter(artwork=artwork).count(), 0)

        incomplete_window = ArtworkProviderForm({
            'company_name': 'Ventana incompleta', 'contact_first_name': 'Luz', 'contact_last_name': 'Ríos',
            'email': 'ventana@example.com', 'phone': '+5491199999999',
            'service_description': 'Entrega', 'for_entry': 'on',
            'early_entry_at': entry_at.strftime('%Y-%m-%dT%H:%M'),
        })
        self.assertFalse(incomplete_window.is_valid())
        self.assertIn('early_exit_at', incomplete_window.errors)

        inverted_windows = ArtworkProviderForm({
            'company_name': 'Ventanas invertidas', 'contact_first_name': 'Luz', 'contact_last_name': 'Ríos',
            'email': 'invertidas@example.com', 'phone': '+5491199999999',
            'service_description': 'Entrega y retiro', 'for_entry': 'on', 'for_exit': 'on',
            'early_entry_at': entry_at.strftime('%Y-%m-%dT%H:%M'),
            'early_exit_at': (entry_at + timedelta(days=3)).strftime('%Y-%m-%dT%H:%M'),
            'dismantling_entry_at': (entry_at + timedelta(days=2)).strftime('%Y-%m-%dT%H:%M'),
            'dismantling_exit_at': (entry_at + timedelta(days=4)).strftime('%Y-%m-%dT%H:%M'),
        })
        self.assertFalse(inverted_windows.is_valid())
        self.assertIn('dismantling_entry_at', inverted_windows.errors)

        response = self.client.post(reverse('artwork_provider_create', args=[artwork.pk]), {
            'company_name': 'Grúas Sur', 'contact_first_name': 'Luz', 'contact_last_name': 'Ríos',
            'email': 'logistica@example.com', 'phone': '+5491199999999',
            'service_description': 'Traslado de estructura', 'for_entry': 'on', 'for_exit': 'on',
            'early_entry_at': entry_at.strftime('%Y-%m-%dT%H:%M'),
            'early_exit_at': early_exit_at.strftime('%Y-%m-%dT%H:%M'),
            'dismantling_entry_at': dismantling_entry_at.strftime('%Y-%m-%dT%H:%M'),
            'dismantling_exit_at': dismantling_exit_at.strftime('%Y-%m-%dT%H:%M'),
        })
        self.assertEqual(response.status_code, 302)
        provider = ArtworkProvider.objects.get(artwork=artwork)
        self.assertEqual(timezone.localtime(provider.early_entry_at).strftime('%H:%M'), '09:30')
        self.assertEqual(timezone.localtime(provider.early_exit_at).strftime('%H:%M'), '18:00')
        self.assertEqual(timezone.localtime(provider.dismantling_entry_at).strftime('%H:%M'), '09:30')
        self.assertEqual(timezone.localtime(provider.dismantling_exit_at).strftime('%H:%M'), '18:30')
        page = self.client.get(reverse('artwork_edit', args=[artwork.pk]))
        self.assertIn(provider, page.context['entry_providers'])
        self.assertIn(provider, page.context['exit_providers'])
        self.assertContains(page, reverse('artwork_provider_edit', args=[artwork.pk, provider.pk]))
        self.assertContains(page, 'Ingreso anticipado')
        self.assertContains(page, 'Desarme y retiro')
        self.assertContains(page, "const key = 'art-position';")
        with self.assertRaises(IntegrityError), transaction.atomic():
            ArtworkProvider.objects.create(
                artwork=artwork, company_name='Inválido', contact_first_name='Sin', contact_last_name='Operación',
                email='invalido@example.com', phone='+5491100000000', service_description='Ninguna',
                for_entry=False, for_exit=False,
            )
        response = self.client.post(reverse('artwork_vehicle_create', args=[artwork.pk, provider.pk]), {
            'vehicle_type': 'truck', 'plate': 'ab 123 cd', 'make_model': 'Iveco Daily',
            'driver_name': 'Luz Ríos', 'driver_document_type': 'DNI',
            'driver_document_number': '28999111', 'notes': 'Caja abierta',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ArtworkProviderVehicle.objects.get(provider=provider).plate, 'AB123CD')

        response = self.client.post(reverse('grant_item_create', args=[artwork.pk, 'budget']), {
            'item_type': 'materials', 'concept': 'Madera', 'amount': '5000', 'currency': 'ARS',
            'exchange_rate': '1', 'rate_date': timezone.localdate(), 'images': [self.image('uno.gif'), self.image('dos.gif')],
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(artwork.grant_items.get().photos.count(), 2)

        self.client.force_login(self.admin)
        review = self.client.get(reverse('artwork_review', args=[self.event.slug, artwork.pk]))
        # Las becas las gestiona otro equipo: la revisión de ESTAFA no las muestra.
        for grant_text in ('Agregar ítem', 'Agregar gasto', 'Presupuesto', 'Rendición', 'name="grant_status"'):
            self.assertNotContains(review, grant_text)
        self.assertNotIn('grant_status', review.context['form'].fields)

        artwork.refresh_from_db()
        form = self.artwork_form({
            'kind': artwork.kind, 'title': artwork.title, 'proposal': artwork.proposal,
            'checkout_team_responsible': self.collaborator.pk, 'expected_version': artwork.version,
        }, artwork=artwork)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        artwork.refresh_from_db()
        self.assertEqual(artwork.checkout_team_responsible, self.collaborator)
        outsider = self.artwork_form({
            'kind': artwork.kind, 'title': artwork.title, 'proposal': artwork.proposal,
            'checkout_team_responsible': self.stranger.pk, 'expected_version': artwork.version,
        }, artwork=artwork)
        self.assertFalse(outsider.is_valid())

    def test_security_boundaries(self):
        artwork = Artwork.objects.create(
            event=self.event, owner=self.owner, title='=IMPORTXML("evil")',
            proposal='Texto', status=Artwork.Status.ACTIVE,
        )
        self.client.force_login(self.owner)
        response = self.client.post(reverse('artwork_edit', args=[artwork.pk]), {
            'kind': Artwork.Kind.POPUP, 'title': '=IMPORTXML("evil")', 'proposal': 'Texto',
            'expected_version': artwork.version, 'action': 'submit', 'status': Artwork.Status.PENDING,
        })
        self.assertEqual(response.status_code, 302)
        artwork.refresh_from_db()
        self.assertEqual(artwork.status, Artwork.Status.ACTIVE)

        artwork.grant_requested = True
        artwork.grant_status = Artwork.GrantStatus.APPROVED
        artwork.checkout_completed = True
        artwork.checkout_verified_at = timezone.now()
        artwork.save()
        self.assertEqual(self.client.post(reverse('grant_submit', args=[artwork.pk])).status_code, 403)
        artwork.refresh_from_db()
        self.assertEqual(artwork.grant_status, Artwork.GrantStatus.APPROVED)

        verified_checkout = self.artwork_form(
            {
                'kind': artwork.kind, 'title': artwork.title, 'proposal': artwork.proposal,
                'expected_version': artwork.version, 'checkout_completed': '',
            }, artwork=artwork,
        )
        self.assertTrue(verified_checkout.is_valid(), verified_checkout.errors)
        verified_checkout.save()
        artwork.refresh_from_db()
        self.assertTrue(artwork.checkout_completed)

        artwork.collaborators.add(self.collaborator)
        artwork.refresh_from_db()
        self.program.is_current = False
        self.program.save(update_fields=['is_current', 'updated_at'])
        historical = self.artwork_form(
            {'expected_version': artwork.version}, artwork=artwork,
        )
        self.assertTrue(historical.is_valid(), historical.errors)
        historical.save()
        self.assertTrue(artwork.collaborators.filter(pk=self.collaborator.pk).exists())

        self.stranger.is_staff = True
        self.stranger.save(update_fields=['is_staff'])
        self.client.force_login(self.stranger)
        self.assertEqual(self.client.get(reverse('artwork_edit', args=[artwork.pk])).status_code, 404)

        self.client.force_login(self.admin)
        exported = self.client.get(reverse('art_admin_export', args=[self.event.slug])).content.decode('utf-8-sig')
        self.assertIn("'=IMPORTXML", exported)
        self.assertEqual(send_art_reminders({'source': 'aws.events'}), 0)

    def test_art_responsible_manages_checkout_letter_and_evidence(self):
        artwork = Artwork.objects.create(
            event=self.event, owner=self.owner, title='Faro', proposal='Texto',
            estafa_contact=self.collaborator,
        )
        review_url = reverse('artwork_review', args=[self.event.slug, artwork.pk])
        self.client.force_login(self.collaborator)
        self.assertEqual(self.client.get(review_url).status_code, 403)

        TeamMembership.objects.create(
            team=Team.objects.get(slug=ESTAFA_SLUG), user=self.collaborator, started_on=timezone.localdate(),
        )
        dashboard = self.client.get(reverse('art_admin_dashboard', args=[self.event.slug]))
        self.assertEqual(list(dashboard.context['assigned_artworks']), [artwork])
        response = self.client.get(review_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Copia física recibida')

        response = self.client.post(review_url, {
            'expected_updated_at': artwork.updated_at.isoformat(),
            'status': artwork.status, 'grant_status': artwork.grant_status, 'benefit_status': artwork.benefit_status,
            'understanding_letter_physical_received': 'on',
            'understanding_letter_physical_custodian': 'Coordinación de Arte',
            'understanding_letter_physical_notes': 'Archivo físico, estante B.',
        })
        self.assertEqual(response.status_code, 302)
        artwork.refresh_from_db()
        self.assertTrue(artwork.understanding_letter_physical_received)
        self.assertEqual(artwork.understanding_letter_physical_custodian, 'Coordinación de Arte')

        # Ni ESTAFA sube evidencia de checkout antes de que se habilite.
        self.program.checkout_opens = timezone.now() - timedelta(hours=1)
        self.program.save(update_fields=['checkout_opens'])
        response = self.client.post(reverse('artwork_checkout_photo_upload', args=[artwork.pk]), {
            'category': ArtworkCheckoutPhoto.Category.BURN_REMAINS,
            'caption': 'Revisar cenizas junto al acceso.',
            'images': self.image('checkout.gif'),
        })
        self.assertEqual(response.status_code, 302)
        evidence = artwork.checkout_photos.get()
        self.assertEqual(evidence.category, ArtworkCheckoutPhoto.Category.BURN_REMAINS)

        self.client.force_login(self.owner)
        artwork.checkout_verified_at = timezone.now()
        artwork.save()
        self.assertEqual(
            self.client.post(reverse('artwork_checkout_photo_delete', args=[artwork.pk, evidence.pk])).status_code,
            403,
        )

    def test_approved_report_is_locked_and_checkout_requires_team_request(self):
        artwork = Artwork.objects.create(
            event=self.event, owner=self.owner, title='Faro', proposal='Texto',
            grant_status=Artwork.GrantStatus.CLOSED,
        )
        expense = ArtworkGrantItem.objects.create(
            artwork=artwork, phase=ArtworkGrantItem.Phase.EXPENSE,
            concept='Flete', amount=Decimal('1000'), rate_date=timezone.localdate(),
        )
        self.client.force_login(self.admin)
        self.assertEqual(
            self.client.get(reverse('grant_item_edit', args=[artwork.pk, expense.pk])).status_code,
            403,
        )
        form = ArtworkReviewForm({
            'expected_updated_at': artwork.updated_at.isoformat(),
            'checkout_verified_at': timezone.localtime().strftime('%Y-%m-%dT%H:%M'),
        }, instance=artwork)
        self.assertFalse(form.is_valid())
        self.assertIn('checkout_verified_at', form.errors)

    def test_event_admin_reviews_each_grant_item(self):
        artwork = Artwork.objects.create(
            event=self.event, owner=self.owner, title='Faro', proposal='Texto',
            grant_status=Artwork.GrantStatus.REPORTED,
        )
        item = ArtworkGrantItem.objects.create(
            artwork=artwork, phase=ArtworkGrantItem.Phase.EXPENSE,
            concept='Flete', amount=Decimal('1000'), rate_date=timezone.localdate(),
        )
        review_url = reverse('grant_item_review', args=[self.event.slug, artwork.pk, item.pk])

        self.client.force_login(self.stranger)
        self.assertEqual(self.client.post(review_url, {'review_status': 'approved'}).status_code, 403)

        self.client.force_login(self.admin)
        response = self.client.post(review_url, {
            'review_status': ArtworkGrantItem.ReviewStatus.REJECTED,
            'review_notes': 'Falta el comprobante del pago.',
        })
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.review_status, ArtworkGrantItem.ReviewStatus.REJECTED)
        self.assertEqual(item.review_notes, 'Falta el comprobante del pago.')

    def test_understanding_letter_windows_and_distance_waiver(self):
        # La declaración no tiene apertura, sólo cierres: pasado el cierre digital, no se puede subir.
        now = timezone.now()
        self.program.understanding_letter_digital_deadline = now - timedelta(days=1)
        self.program.understanding_letter_physical_deadline = now + timedelta(days=20)
        self.program.save()
        artwork = Artwork.objects.create(event=self.event, owner=self.owner, title='Faro', proposal='Texto')
        form = self.artwork_form({}, artwork=artwork)
        self.assertTrue(form.fields['understanding_letter'].disabled)

        waiver = ArtworkReviewForm({
            'expected_updated_at': artwork.updated_at.isoformat(),
            'understanding_letter_physical_waiver': 'on',
        }, instance=artwork)
        self.assertFalse(waiver.is_valid())
        self.assertIn('understanding_letter_physical_waiver_reason', waiver.errors)

    def test_event_checkin_requires_arrival_and_placement_detail(self):
        artwork = Artwork.objects.create(event=self.event, owner=self.owner, title='Faro', proposal='Texto')
        form = ArtworkReviewForm({
            'expected_updated_at': artwork.updated_at.isoformat(),
            'checkin_art_at': timezone.localtime().strftime('%Y-%m-%dT%H:%M'),
            'checkin_placement_changed': 'on',
        }, instance=artwork)
        self.assertFalse(form.is_valid())
        self.assertIn('checkin_arrived_at', form.errors)
        self.assertIn('checkin_placed', form.errors)
        self.assertIn('checkin_placement_change_notes', form.errors)

        artwork.understanding_letter_physical_received = True
        with self.assertRaises(ValidationError):
            artwork.full_clean()

    def test_grant_report_requires_expense_and_final_photo(self):
        artwork = Artwork.objects.create(
            event=self.event, owner=self.owner, title='Faro', proposal='Texto',
            grant_requested=True, grant_status=Artwork.GrantStatus.APPROVED,
            grant_report='Fondos utilizados según el plan.',
        )
        self.client.force_login(self.owner)
        self.client.post(reverse('grant_report_submit', args=[artwork.pk]))
        artwork.refresh_from_db()
        self.assertEqual(artwork.grant_status, Artwork.GrantStatus.APPROVED)

        ArtworkGrantItem.objects.create(
            artwork=artwork, phase=ArtworkGrantItem.Phase.EXPENSE, concept='Materiales',
            amount=100, currency='ARS', exchange_rate=1, rate_date=timezone.localdate(),
        )
        ArtworkPhoto.objects.create(
            artwork=artwork, image='art/gallery/final.gif', stage=ArtworkPhoto.Stage.FINAL,
            uploaded_by=self.owner,
        )
        self.client.post(reverse('grant_report_submit', args=[artwork.pk]))
        artwork.refresh_from_db()
        self.assertEqual(artwork.grant_status, Artwork.GrantStatus.REPORTED)
        photo = artwork.photos.get(stage=ArtworkPhoto.Stage.FINAL)
        self.assertEqual(
            self.client.post(reverse('artwork_photo_delete', args=[artwork.pk, photo.pk])).status_code,
            403,
        )
        self.assertTrue(artwork.photos.filter(pk=photo.pk).exists())

    def test_grant_report_blocks_expenses_above_approved_amount(self):
        artwork = Artwork.objects.create(
            event=self.event, owner=self.owner, title='Faro', proposal='Texto',
            grant_status=Artwork.GrantStatus.APPROVED, grant_approved_amount_ars=Decimal('1000'),
            grant_report='Fondos utilizados.',
        )
        ArtworkGrantItem.objects.create(
            artwork=artwork, phase=ArtworkGrantItem.Phase.EXPENSE, concept='Materiales',
            amount=1001, currency='ARS', exchange_rate=1, rate_date=timezone.localdate(),
        )
        ArtworkPhoto.objects.create(
            artwork=artwork, image='art/gallery/final.gif', stage=ArtworkPhoto.Stage.FINAL,
            uploaded_by=self.owner,
        )
        self.client.force_login(self.owner)
        self.client.post(reverse('grant_report_submit', args=[artwork.pk]))
        artwork.refresh_from_db()
        self.assertEqual(artwork.grant_status, Artwork.GrantStatus.APPROVED)

    @patch('art.views.send_mail')
    def test_estafa_approves_rejects_and_reopens_registrations(self, send_mail):
        artwork = Artwork.objects.create(
            event=self.event, owner=self.owner, title='Faro', proposal='Texto',
            estafa_contact=self.stranger,
        )
        artwork.collaborators.add(self.collaborator)
        status_url = reverse('artwork_status', args=[self.event.slug, artwork.pk])
        list_url = f"{reverse('art_admin_dashboard', args=[self.event.slug])}?status=pending"
        for user in (self.owner, self.stranger):
            self.client.force_login(user)
            self.assertEqual(self.client.post(status_url, {'transition': 'approve'}).status_code, 403)

        self.client.force_login(self.admin)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(status_url, {
                'transition': 'approve', 'message': 'Nos encanta.', 'next': list_url,
            })
        self.assertRedirects(response, list_url, fetch_redirect_response=False)
        artwork.refresh_from_db()
        self.assertEqual(artwork.status, Artwork.Status.ACTIVE)
        self.assertEqual(artwork.status_changed_by, self.admin)
        self.assertEqual(artwork.review_feedback, 'Nos encanta.')
        self.assertEqual(artwork.operations_group.event, self.event)
        send_mail.assert_called_once()
        self.assertEqual(send_mail.call_args.kwargs['template_name'], 'art_status_changed')
        self.assertEqual(sorted(send_mail.call_args.kwargs['recipient_list']), ['artista@example.com', 'colab@example.com'])
        self.assertTrue(send_mail.call_args.kwargs['context']['approved'])

        self.client.post(status_url, {'transition': 'approve'})
        self.client.post(status_url, {'transition': 'reject', 'message': 'Tarde.'})
        artwork.refresh_from_db()
        self.assertEqual(artwork.status, Artwork.Status.ACTIVE)

        self.client.post(status_url, {'transition': 'reopen'})
        artwork.refresh_from_db()
        self.assertEqual(artwork.status, Artwork.Status.PENDING)
        self.assertEqual(artwork.review_feedback, '')

        self.client.post(status_url, {'transition': 'reject', 'message': ' '})
        artwork.refresh_from_db()
        self.assertEqual(artwork.status, Artwork.Status.PENDING)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(status_url, {'transition': 'reject', 'message': 'No entra en el predio.'})
        artwork.refresh_from_db()
        self.assertEqual(artwork.status, Artwork.Status.REJECTED)
        self.assertEqual(send_mail.call_count, 2)
        self.assertFalse(send_mail.call_args.kwargs['context']['approved'])
        self.assertEqual(send_mail.call_args.kwargs['context']['artwork'].review_feedback, 'No entra en el predio.')
        self.assertTrue(all(
            field.disabled for name, field in self.artwork_form({}, artwork=artwork).fields.items()
            if name != 'expected_version'
        ))
        self.assertFalse(any(
            field.disabled for name, field in self.artwork_form({}, artwork=artwork, actor=self.admin).fields.items()
            if name in ('title', 'proposal')
        ))

    def test_checkout_opens_with_the_event_and_the_team_submits_it(self):
        artwork = Artwork.objects.create(
            event=self.event, owner=self.owner, title='Faro', proposal='Texto',
            status=Artwork.Status.ACTIVE, estafa_contact=self.collaborator,
        )
        pending = Artwork.objects.create(event=self.event, owner=self.owner, title='Nube')
        self.assertEqual(artwork.stage, Artwork.Status.ACTIVE)
        self.client.force_login(self.owner)
        edit_url = reverse('artwork_edit', args=[artwork.pk])
        self.client.post(edit_url, {'title': 'Faro', 'expected_version': artwork.version, 'action': 'checkout'})
        artwork.refresh_from_db()
        self.assertEqual(artwork.status, Artwork.Status.ACTIVE)

        self.event.start = timezone.now() - timedelta(hours=1)
        self.event.save(update_fields=['start'])
        artwork.refresh_from_db()
        pending.refresh_from_db()
        self.assertEqual(artwork.stage, Artwork.CHECKOUT_PENDING)
        self.assertEqual(artwork.stage_label, 'Checkout pendiente')
        self.assertEqual(pending.stage, Artwork.Status.PENDING)
        self.assertNotContains(self.client.get(reverse('artwork_edit', args=[pending.pk])), 'Enviar checkout')
        self.assertContains(self.client.get(edit_url), 'Enviar checkout')

        self.client.post(edit_url, {
            'title': 'Faro', 'checkout_notes': 'Quedó limpio.', 'expected_version': artwork.version, 'action': 'checkout',
        })
        artwork.refresh_from_db()
        self.assertEqual(artwork.checkout_notes, 'Quedó limpio.')
        self.assertEqual(artwork.status, Artwork.Status.ACTIVE)

        ArtworkCheckoutPhoto.objects.create(artwork=artwork, image=self.image('final.gif'), category=ArtworkCheckoutPhoto.Category.CLEANUP)
        with patch('art.views.send_mail') as send_mail, self.captureOnCommitCallbacks(execute=True):
            self.client.post(edit_url, {'title': 'Faro', 'checkout_notes': 'Quedó limpio.', 'expected_version': artwork.version, 'action': 'checkout'})
        artwork.refresh_from_db()
        self.assertEqual(artwork.status, Artwork.Status.CHECKOUT_SUBMITTED)
        self.assertTrue(artwork.checkout_completed)
        self.assertIsNotNone(artwork.checkout_requested_at)
        # El contacto de ESTAFA recibe el aviso para verificarlo.
        send_mail.assert_called_once()
        self.assertEqual(send_mail.call_args.kwargs['template_name'], 'art_checkout_submitted')
        self.assertEqual(send_mail.call_args.kwargs['recipient_list'], ['colab@example.com'])

        # El contacto verifica el checkout como miembro de ESTAFA.
        TeamMembership.objects.create(
            team=Team.objects.get(slug=ESTAFA_SLUG), user=self.collaborator, started_on=timezone.localdate(),
        )
        self.client.force_login(self.collaborator)
        review_url = reverse('artwork_review', args=[self.event.slug, artwork.pk])
        required = {'grant_status': artwork.grant_status, 'benefit_status': artwork.benefit_status}
        self.client.post(review_url, {
            **required,
            'expected_updated_at': artwork.updated_at.isoformat(),
            'checkout_verified_at': timezone.localtime().strftime('%Y-%m-%dT%H:%M'),
        })
        artwork.refresh_from_db()
        self.assertEqual(artwork.status, Artwork.Status.CHECKOUT_VERIFIED)
        self.assertEqual(artwork.status_changed_by, self.collaborator)
        self.client.post(review_url, {**required, 'expected_updated_at': artwork.updated_at.isoformat(), 'checkout_verified_at': ''})
        artwork.refresh_from_db()
        self.assertEqual(artwork.status, Artwork.Status.CHECKOUT_SUBMITTED)

    def test_admin_list_puts_estafa_turn_first_and_filters_by_stage(self):
        for title, status in (
            ('Activa', Artwork.Status.ACTIVE), ('Enviada', Artwork.Status.CHECKOUT_SUBMITTED),
            ('Pendiente', Artwork.Status.PENDING), ('Rechazada', Artwork.Status.REJECTED),
        ):
            Artwork.objects.create(event=self.event, owner=self.owner, title=title, status=status)
        self.client.force_login(self.admin)
        url = reverse('art_admin_dashboard', args=[self.event.slug])
        titles = [artwork.title for artwork in self.client.get(url).context['artworks']]
        self.assertEqual(titles, ['Pendiente', 'Enviada', 'Activa', 'Rechazada'])
        self.assertNotContains(self.client.get(url), 'value="approve"')

        self.assertEqual([a.title for a in self.client.get(url, {'status': 'checkout_pending'}).context['artworks']], [])
        self.program.checkout_opens = timezone.now() - timedelta(hours=1)
        self.program.save(update_fields=['checkout_opens'])
        self.assertEqual([a.title for a in self.client.get(url, {'status': 'checkout_pending'}).context['artworks']], ['Activa'])
        self.assertEqual([a.title for a in self.client.get(url, {'status': 'active'}).context['artworks']], [])

    def test_status_migration_maps_previous_values(self):
        new_status = import_module('art.migrations.0004_artwork_status_lifecycle').new_status
        verified_at = timezone.now()
        for old, completed, verified, expected in (
            ('draft', False, None, 'pending'), ('submitted', False, None, 'pending'),
            ('changes', False, None, 'pending'), ('accepted', False, None, 'active'),
            ('installed', True, None, 'checkout'), ('accepted', True, verified_at, 'verified'),
            ('completed', True, verified_at, 'verified'), ('rejected', False, None, 'rejected'),
            ('cancelled', False, None, 'rejected'),
        ):
            self.assertEqual(new_status(old, completed, verified), expected, old)

    def test_only_one_current_art_program(self):
        other_event = Event.objects.create(
            name='Otro', slug='otro', start=timezone.now() + timedelta(days=50), end=timezone.now() + timedelta(days=51),
            transfers_enabled_until=timezone.now() + timedelta(days=40), header_image='events/heros/no-image.jpg', title='Otro', description='Otro',
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            ArtProgram.objects.create(event=other_event, is_current=True)
