from datetime import timedelta
from decimal import Decimal
from importlib import import_module
from unittest.mock import patch

from allauth.account.models import EmailAddress
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django.contrib.messages import get_messages

from .forms import ArtworkForm, ArtworkGrantItemForm, ArtworkPhotoUploadForm, ArtworkProviderForm, ArtworkReviewForm
from .reminders import send_art_reminders
from .views import _checkpoints
from .models import (
    ArtProgram, Artwork, ArtworkGrantItem, ArtworkInvitation,
    ArtworkCheckoutPhoto, ArtworkLogisticsPerson, ArtworkPhoto, ArtworkProvider,
    ArtworkProviderVehicle,
)
from events.models import Event
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
        )
        self.event.admins.add(self.admin)
        self.program = ArtProgram.objects.create(
            event=self.event, is_current=True, grants_enabled=True,
            registration_closes=now - timedelta(days=1),
            grant_deadline=now + timedelta(days=2),
            proposal_deadline=now + timedelta(days=2),
            grant_report_deadline=now + timedelta(days=30),
        )

    def artwork_form(self, data, artwork=None, action='save', actor=None):
        artwork = artwork or Artwork(event=self.event, owner=self.owner)
        return ArtworkForm(
            data,
            instance=artwork,
            program=self.program,
            owner=artwork.owner,
            actor=actor or self.owner,
            action=action,
        )

    def test_primary_fields_are_associated_with_artwork_form(self):
        form = self.artwork_form({})
        self.assertNotIn('kind', form.fields)
        self.assertEqual(form.fields['public_description'].max_length, 200)
        self.assertEqual(form.fields['public_description'].widget.attrs['maxlength'], 200)
        for field in form.fields.values():
            self.assertEqual(field.widget.attrs.get('form'), 'artwork-form')
        self.assertTrue(all(checkpoint['anchor'] for checkpoint in _checkpoints(self.program)))

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
        self.assertContains(response, 'Guardá la instalación para subir fotos.')
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
            'kind': Artwork.Kind.POPUP, 'title': 'Faro', 'action': 'submit',
        })
        artwork = Artwork.objects.get(owner=self.owner)
        self.assertRedirects(response, reverse('artwork_edit', args=[artwork.pk]))
        self.assertEqual(artwork.event, self.event)
        self.assertEqual(artwork.kind, Artwork.Kind.PLANNED)
        self.assertEqual(artwork.status, Artwork.Status.PENDING)
        self.assertEqual(artwork.proposal, '')
        self.assertIsNone(artwork.operations_group)

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

    def test_draft_save_does_not_claim_the_proposal_was_sent(self):
        artwork = Artwork.objects.create(event=self.event, owner=self.owner, title='Faro')
        self.client.force_login(self.owner)
        response = self.client.post(reverse('artwork_edit', args=[artwork.pk]), {
            'title': 'Borrador', 'expected_version': artwork.version, 'action': 'save',
        })

        self.assertEqual(response.status_code, 302)
        artwork.refresh_from_db()
        self.assertEqual(artwork.title, 'Borrador')
        self.assertEqual(artwork.status, Artwork.Status.PENDING)
        self.assertEqual([str(message) for message in get_messages(response.wsgi_request)], ['El borrador quedó guardado.'])

    def test_creation_can_include_safety_budget_gallery_and_early_entry(self):
        self.program.registration_closes = timezone.now() + timedelta(days=1)
        self.program.save(update_fields=['registration_closes'])
        self.client.force_login(self.owner)
        self.client.post(reverse('artwork_create', args=[self.event.slug]), {'title': 'Faro'})
        artwork = Artwork.objects.get(owner=self.owner)
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
        self.client.post(reverse('artwork_photo_upload', args=[artwork.pk]), {
            'images': self.image('inicio.gif'), 'stage': ArtworkPhoto.Stage.PROPOSAL,
        })
        self.client.post(reverse('logistics_person_create', args=[artwork.pk]), {
            'first_name': 'Ada', 'last_name': 'Sur', 'email': 'ada@example.com', 'phone': '+5491112345678',
            'document_type': 'DNI', 'document_number': '30111222', 'early_entry': 'on',
            'early_entry_date': timezone.localdate(),
        })
        self.assertEqual(artwork.safety_responsible, self.collaborator)
        self.assertEqual(artwork.grant_items.count(), 1)
        self.assertEqual(artwork.photos.count(), 1)
        self.assertEqual(artwork.logistics_people.count(), 1)

    def test_cleanup_migration_deletes_only_empty_drafts(self):
        from importlib import import_module

        from django.apps import apps
        cleanup = import_module('art.migrations.0003_delete_empty_artwork_drafts').delete_empty_drafts
        # Filas previas a la migración de estados, cuando existía 'draft'.
        empty = Artwork.objects.create(event=self.event, owner=self.owner, status='draft')
        titled = Artwork.objects.create(event=self.event, owner=self.owner, title='Faro', status='draft')
        with_photo = Artwork.objects.create(event=self.event, owner=self.owner, status='draft')
        ArtworkPhoto.objects.create(artwork=with_photo, image=self.image('foto.gif'), stage=ArtworkPhoto.Stage.PROPOSAL)
        edited = Artwork.objects.create(event=self.event, owner=self.owner, version=2, status='draft')
        with_collaborator = Artwork.objects.create(event=self.event, owner=self.owner, status='draft')
        with_collaborator.collaborators.add(self.collaborator)

        cleanup(apps, None)

        self.assertFalse(Artwork.objects.filter(pk=empty.pk).exists())
        self.assertEqual(
            set(Artwork.objects.values_list('pk', flat=True)),
            {titled.pk, with_photo.pk, edited.pk, with_collaborator.pk},
        )

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
        form = self.artwork_form({'title': 'Faro'}, action='submit')
        self.assertFalse(form.is_valid())
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
        self.assertEqual(response.url, f"{reverse('artwork_edit', args=[artwork.pk])}#beca")
        artwork.refresh_from_db()
        self.assertEqual(artwork.grant_status, Artwork.GrantStatus.PENDING)

    def test_save_bar_lifts_chat_bubble(self):
        artwork = Artwork.objects.create(event=self.event, owner=self.owner, title='Faro')
        self.client.force_login(self.owner)
        page = self.client.get(reverse('artwork_edit', args=[artwork.pk]))
        self.assertContains(page, 'class="save-bar', count=1)
        self.assertContains(page, 'data-sticky-actions>', count=1)
        self.assertContains(page, '--sticky-actions-height')

    def test_permissions_invitation_and_multiple_photo_upload(self):
        artwork = Artwork.objects.create(event=self.event, owner=self.owner, title='Faro', proposal='Texto')
        self.client.force_login(self.stranger)
        self.assertEqual(self.client.get(reverse('artwork_edit', args=[artwork.pk])).status_code, 404)

        artwork.collaborators.add(self.collaborator, self.admin)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse('artwork_edit', args=[artwork.pk])).status_code, 200)

        form = self.artwork_form({
            'kind': Artwork.Kind.POPUP, 'title': artwork.title, 'proposal': artwork.proposal,
            'collaborator_emails': 'invitada@example.com', 'expected_version': artwork.version,
        }, artwork=artwork)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        invitation = ArtworkInvitation.objects.get(artwork=artwork, email='invitada@example.com')
        invited = User.objects.create_user(username='invitada', email='invitada@example.com')
        invited.profile.document_number = '20000001'
        invited.profile.phone = '+5491100000099'
        invited.profile.profile_completion = Profile.COMPLETE
        invited.profile.save()
        EmailAddress.objects.create(user=invited, email=invited.email, verified=True, primary=True)
        self.client.force_login(invited)
        self.assertEqual(self.client.get(reverse('art_invitation_accept', args=[invitation.token])).status_code, 200)
        self.assertFalse(artwork.collaborators.filter(pk=invited.pk).exists())
        self.assertEqual(self.client.post(reverse('art_invitation_accept', args=[invitation.token])).status_code, 302)
        self.assertTrue(artwork.collaborators.filter(pk=invited.pk).exists())

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

    def test_admin_dashboard_filters_and_exports_operational_tags(self):
        tagged = Artwork.objects.create(
            event=self.event, owner=self.owner, title='Escultura sonora',
            uses_fire=True, uses_sound=True,
        )
        Artwork.objects.create(event=self.event, owner=self.owner, title='Obra silenciosa')
        self.client.force_login(self.admin)

        dashboard = self.client.get(reverse('art_admin_dashboard', args=[self.event.slug]), {
            'fire': 'yes', 'sound': 'yes',
        })
        self.assertContains(dashboard, tagged.title)
        self.assertNotContains(dashboard, 'Obra silenciosa')
        self.assertContains(dashboard, 'Tiene fuego')
        self.assertContains(dashboard, 'Tiene sonido')

        exported = self.client.get(reverse('art_admin_export', args=[self.event.slug]), {
            'fire': 'yes', 'sound': 'yes',
        }).content.decode('utf-8-sig')
        self.assertIn('Etiquetas', exported)
        self.assertIn('Tiene fuego, Tiene sonido', exported)
        self.assertNotIn('Obra silenciosa', exported)

    def test_structured_logistics_checkout_and_grant_item_images(self):
        artwork = Artwork.objects.create(
            event=self.event, owner=self.owner, title='Faro', proposal='Texto', grant_requested=True,
            status=Artwork.Status.ACTIVE,
        )
        self.program.checkout_opens = timezone.now() - timedelta(hours=1)
        self.program.save(update_fields=['checkout_opens'])
        self.client.force_login(self.owner)
        entry_at = timezone.localtime().replace(hour=9, minute=30, second=0, microsecond=0)
        early_exit_at = entry_at + timedelta(hours=8, minutes=30)
        dismantling_entry_at = entry_at + timedelta(days=4)
        dismantling_exit_at = dismantling_entry_at + timedelta(hours=9)

        response = self.client.post(reverse('logistics_person_create', args=[artwork.pk]), {
            'first_name': 'Ada', 'last_name': 'Sur', 'email': 'ada@example.com', 'phone': '+5491112345678',
            'document_type': 'DNI', 'document_number': '30111222', 'early_entry': 'on',
            'early_entry_date': timezone.localdate(), 'dismantling': 'on',
            'dismantling_date': timezone.localdate() + timedelta(days=4),
        })
        self.assertEqual(response.status_code, 302)
        person = ArtworkLogisticsPerson.objects.get(artwork=artwork)
        response = self.client.post(reverse('logistics_person_edit', args=[artwork.pk, person.pk]), {
            'first_name': 'Ada', 'last_name': 'Sur', 'email': 'ada@example.com', 'phone': '+5491112345678',
            'document_type': 'DNI', 'document_number': '30111222',
            'early_entry_date': timezone.localdate(), 'dismantling': 'on',
            'dismantling_date': timezone.localdate() + timedelta(days=4),
        })
        self.assertEqual(response.status_code, 302)
        person.refresh_from_db()
        self.assertIsNone(person.early_entry_date)

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
        self.assertContains(page, f'artwork-position-{artwork.pk}')
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
        self.assertContains(review, 'Agregar ítem')
        self.assertContains(review, 'Agregar gasto')
        self.assertContains(review, reverse('grant_item_edit', args=[artwork.pk, artwork.grant_items.get().pk]))
        response = self.client.post(reverse('grant_item_create', args=[artwork.pk, 'expense']), {
            'item_type': 'service', 'concept': 'Flete', 'amount': '2000', 'currency': 'ARS',
            'exchange_rate': '1', 'rate_date': timezone.localdate(), 'return_to': 'review',
        })
        self.assertEqual(
            response.url,
            f"{reverse('artwork_review', args=[self.event.slug, artwork.pk])}#admin-expenses",
        )

        artwork.refresh_from_db()
        form = self.artwork_form({
            'kind': artwork.kind, 'title': artwork.title, 'proposal': artwork.proposal,
            'checkout_team_responsible': person.pk, 'expected_version': artwork.version,
        }, artwork=artwork)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        artwork.refresh_from_db()
        self.assertEqual(artwork.checkout_team_responsible, person)

    def test_security_boundaries(self):
        artwork = Artwork.objects.create(
            event=self.event, owner=self.owner, title='=IMPORTXML("evil")',
            proposal='Texto', status=Artwork.Status.ACTIVE,
        )
        self.client.force_login(self.owner)
        response = self.client.post(reverse('artwork_edit', args=[artwork.pk]), {
            'kind': Artwork.Kind.POPUP, 'title': 'Reabrir', 'proposal': 'Texto',
            'expected_version': artwork.version, 'action': 'submit',
        })
        self.assertEqual(response.status_code, 200)
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

        form = self.artwork_form({
            'kind': Artwork.Kind.POPUP, 'title': artwork.title, 'proposal': artwork.proposal,
            'collaborator_emails': self.collaborator.email, 'expected_version': artwork.version,
        }, artwork=artwork)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertFalse(artwork.collaborators.filter(pk=self.collaborator.pk).exists())
        self.assertTrue(ArtworkInvitation.objects.filter(artwork=artwork, email=self.collaborator.email).exists())

        too_many = ','.join(f'persona{index}@example.com' for index in range(21))
        limited = self.artwork_form({
            'kind': Artwork.Kind.POPUP, 'title': artwork.title, 'proposal': artwork.proposal,
            'collaborator_emails': too_many, 'expected_version': artwork.version,
        }, artwork=artwork)
        self.assertFalse(limited.is_valid())

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
            checkout_art_responsible=self.collaborator,
        )
        self.client.force_login(self.collaborator)
        review_url = reverse('artwork_review', args=[self.event.slug, artwork.pk])
        response = self.client.get(review_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Carta física recibida')
        self.assertNotContains(response, 'Estado de la instalación')

        response = self.client.post(review_url, {
            'expected_updated_at': artwork.updated_at.isoformat(),
            'understanding_letter_physical_received': 'on',
            'understanding_letter_physical_custodian': 'Coordinación de Arte',
            'understanding_letter_physical_notes': 'Archivo físico, estante B.',
        })
        self.assertEqual(response.status_code, 302)
        artwork.refresh_from_db()
        self.assertTrue(artwork.understanding_letter_physical_received)
        self.assertEqual(artwork.understanding_letter_physical_custodian, 'Coordinación de Arte')

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
        now = timezone.now()
        self.program.understanding_letter_digital_opens = now + timedelta(days=1)
        self.program.understanding_letter_digital_deadline = now + timedelta(days=10)
        self.program.understanding_letter_physical_opens = now + timedelta(days=2)
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
            checkout_art_responsible=self.stranger,
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
            status=Artwork.Status.ACTIVE, checkout_art_responsible=self.collaborator,
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
        self.client.post(edit_url, {'title': 'Faro', 'checkout_notes': 'Quedó limpio.', 'expected_version': artwork.version, 'action': 'checkout'})
        artwork.refresh_from_db()
        self.assertEqual(artwork.status, Artwork.Status.CHECKOUT_SUBMITTED)
        self.assertTrue(artwork.checkout_completed)
        self.assertIsNotNone(artwork.checkout_requested_at)

        self.client.force_login(self.collaborator)
        review_url = reverse('artwork_review', args=[self.event.slug, artwork.pk])
        self.client.post(review_url, {
            'expected_updated_at': artwork.updated_at.isoformat(),
            'checkout_verified_at': timezone.localtime().strftime('%Y-%m-%dT%H:%M'),
        })
        artwork.refresh_from_db()
        self.assertEqual(artwork.status, Artwork.Status.CHECKOUT_VERIFIED)
        self.assertEqual(artwork.status_changed_by, self.collaborator)
        self.client.post(review_url, {'expected_updated_at': artwork.updated_at.isoformat(), 'checkout_verified_at': ''})
        artwork.refresh_from_db()
        self.assertEqual(artwork.status, Artwork.Status.CHECKOUT_SUBMITTED)

    def test_admin_list_puts_estafa_turn_first_and_filters_by_stage(self):
        for title, status in (
            ('Activa', Artwork.Status.ACTIVE), ('Enviada', Artwork.Status.CHECKOUT_SUBMITTED),
            ('Pendiente', Artwork.Status.PENDING), ('Rechazada', Artwork.Status.REJECTED), ('', Artwork.Status.PENDING),
        ):
            Artwork.objects.create(event=self.event, owner=self.owner, title=title, status=status)
        self.client.force_login(self.admin)
        url = reverse('art_admin_dashboard', args=[self.event.slug])
        titles = [artwork.title for artwork in self.client.get(url).context['artworks']]
        self.assertEqual(titles, ['Pendiente', 'Enviada', 'Activa', 'Rechazada'])
        self.assertContains(self.client.get(url), 'value="approve"', count=1)

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
