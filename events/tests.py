from datetime import timedelta
from decimal import Decimal

from allauth.account.models import EmailAddress
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .forms import ArtworkForm, ArtworkGrantItemForm, ArtworkPhotoUploadForm
from .art_reminders import send_art_reminders
from .models import (
    ArtProgram, Artwork, ArtworkGrantItem, ArtworkInvitation,
    ArtworkLogisticsPerson, ArtworkPhoto, ArtworkProvider,
    ArtworkProviderVehicle, Event,
)
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
        for field in form.fields.values():
            self.assertEqual(field.widget.attrs.get('form'), 'artwork-form')

    def test_draft_popup_multiple_artworks_and_submission_group(self):
        draft = self.artwork_form({'kind': Artwork.Kind.POPUP})
        self.assertTrue(draft.is_valid(), draft.errors)
        first = draft.save()
        self.assertEqual(first.status, Artwork.Status.DRAFT)

        incomplete_submit = self.artwork_form(
            {'kind': Artwork.Kind.POPUP, 'expected_version': first.version},
            artwork=first,
            action='submit',
        )
        self.assertFalse(incomplete_submit.is_valid())

        self.client.force_login(self.owner)
        response = self.client.post(reverse('artwork_create', args=[self.event.slug]), {
            'kind': Artwork.Kind.POPUP,
            'title': 'Faro',
            'proposal': 'Una propuesta completa',
            'action': 'submit',
        })
        self.assertEqual(response.status_code, 302)
        second = Artwork.objects.exclude(pk=first.pk).get()
        self.assertEqual(second.status, Artwork.Status.SUBMITTED)
        self.assertEqual(second.operations_group.event, self.event)
        self.assertEqual(Artwork.objects.filter(owner=self.owner).count(), 2)

    def test_planned_registration_is_closed(self):
        form = self.artwork_form({'kind': Artwork.Kind.PLANNED, 'title': 'Faro', 'proposal': 'Propuesta'}, action='submit')
        self.assertFalse(form.is_valid())

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
        ars_form = ArtworkGrantItemForm({
            'item_type': 'materials', 'concept': 'Hierro', 'amount': '1000.25', 'currency': 'ARS',
            'exchange_rate': '999', 'rate_date': timezone.localdate(), 'rate_source': 'No aplica',
        }, instance=ArtworkGrantItem(artwork=artwork, created_by=self.owner), phase=ArtworkGrantItem.Phase.BUDGET)
        self.assertTrue(ars_form.is_valid(), ars_form.errors)
        ars = ars_form.save()
        self.assertEqual(ars.exchange_rate, Decimal('1'))
        self.assertEqual(ars.amount_ars, Decimal('1000.25'))

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

    def test_permissions_invitation_and_multiple_photo_upload(self):
        artwork = Artwork.objects.create(event=self.event, owner=self.owner, title='Faro', proposal='Texto')
        self.client.force_login(self.stranger)
        self.assertEqual(self.client.get(reverse('artwork_edit', args=[artwork.pk])).status_code, 404)

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

    def test_structured_logistics_checkout_and_grant_item_images(self):
        artwork = Artwork.objects.create(event=self.event, owner=self.owner, title='Faro', proposal='Texto', grant_requested=True)
        self.client.force_login(self.owner)
        entry_at = timezone.localtime().replace(hour=9, minute=30, second=0, microsecond=0)
        exit_at = entry_at + timedelta(days=4, hours=9)

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

        response = self.client.post(reverse('artwork_provider_create', args=[artwork.pk]), {
            'company_name': 'Grúas Sur', 'contact_first_name': 'Luz', 'contact_last_name': 'Ríos',
            'email': 'logistica@example.com', 'phone': '+5491199999999',
            'service_description': 'Traslado de estructura', 'for_entry': 'on', 'for_exit': 'on',
            'entry_date': timezone.localtime(entry_at).strftime('%Y-%m-%dT%H:%M'),
            'departure_date': timezone.localtime(exit_at).strftime('%Y-%m-%dT%H:%M'),
        })
        self.assertEqual(response.status_code, 302)
        provider = ArtworkProvider.objects.get(artwork=artwork)
        self.assertEqual(timezone.localtime(provider.entry_date).strftime('%H:%M'), '09:30')
        self.assertEqual(timezone.localtime(provider.departure_date).strftime('%H:%M'), '18:30')
        page = self.client.get(reverse('artwork_edit', args=[artwork.pk]))
        self.assertIn(provider, page.context['entry_providers'])
        self.assertIn(provider, page.context['exit_providers'])
        self.assertContains(page, reverse('artwork_provider_edit', args=[artwork.pk, provider.pk]))
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
            proposal='Texto', status=Artwork.Status.ACCEPTED,
        )
        self.client.force_login(self.owner)
        response = self.client.post(reverse('artwork_edit', args=[artwork.pk]), {
            'kind': Artwork.Kind.POPUP, 'title': 'Reabrir', 'proposal': 'Texto',
            'expected_version': artwork.version, 'action': 'submit',
        })
        self.assertEqual(response.status_code, 200)
        artwork.refresh_from_db()
        self.assertEqual(artwork.status, Artwork.Status.ACCEPTED)

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

    def test_only_one_current_art_program(self):
        other_event = Event.objects.create(
            name='Otro', slug='otro', start=timezone.now() + timedelta(days=50), end=timezone.now() + timedelta(days=51),
            transfers_enabled_until=timezone.now() + timedelta(days=40), header_image='events/heros/no-image.jpg', title='Otro', description='Otro',
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            ArtProgram.objects.create(event=other_event, is_current=True)
