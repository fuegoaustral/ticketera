from datetime import timedelta

from django.contrib.auth.models import User
from django.template.loader import get_template
from django.test import TestCase
from django.utils import timezone

from .forms import ArtworkForm
from .models import ArtProgram, Artwork, Event


class ArtworkFlowTest(TestCase):
    def setUp(self):
        now = timezone.now()
        self.owner = User.objects.create_user(username='artista', email='artista@example.com')
        self.collaborator = User.objects.create_user(username='colab', email='colab@example.com')
        self.event = Event.objects.create(
            name='FA', slug='fa', start=now + timedelta(days=20), end=now + timedelta(days=24),
            transfers_enabled_until=now + timedelta(days=10), header_image='event.jpg', title='FA', description='Evento',
        )
        self.program = ArtProgram.objects.create(event=self.event, registration_closes=now - timedelta(days=1))

    def test_popup_and_checkpoint_permissions(self):
        data = {'kind': Artwork.Kind.PLANNED, 'title': 'Faro', 'proposal': 'Una obra'}
        planned = ArtworkForm(data, instance=Artwork(event=self.event, owner=self.owner), program=self.program, owner=self.owner)
        self.assertFalse(planned.is_valid())

        data['kind'] = Artwork.Kind.POPUP
        popup = ArtworkForm(data, instance=Artwork(event=self.event, owner=self.owner), program=self.program, owner=self.owner)
        self.assertTrue(popup.is_valid(), popup.errors)
        artwork = popup.save()
        Artwork.objects.create(event=self.event, owner=self.owner, kind=Artwork.Kind.POPUP, title='Otra obra', proposal='Otra propuesta')
        self.assertEqual(Artwork.objects.filter(owner=self.owner).count(), 2)
        artwork.collaborators.add(self.collaborator)
        self.assertTrue(artwork.can_edit(self.collaborator))

        self.program.proposal_deadline = timezone.now() - timedelta(minutes=1)
        self.program.save(update_fields=['proposal_deadline'])
        edit = ArtworkForm(
            {'kind': Artwork.Kind.PLANNED, 'title': 'Hack', 'proposal': 'Cambio fuera de fecha'},
            instance=artwork, program=self.program, owner=self.owner,
        )
        self.assertTrue(edit.is_valid(), edit.errors)
        edit.save()
        artwork.refresh_from_db()
        self.assertEqual(artwork.title, 'Faro')

        self.program.grants_enabled = True
        self.program.grant_deadline = timezone.now() + timedelta(days=2)
        self.program.save(update_fields=['grants_enabled', 'grant_deadline'])
        grant = ArtworkForm(
            {
                'kind': Artwork.Kind.POPUP, 'title': 'Becada', 'proposal': 'Con apoyo',
                'grant_requested': 'on', 'grant_amount': '150000',
                'grant_budget': 'Materiales', 'grant_justification': 'Sin la beca no llega',
            },
            instance=Artwork(event=self.event, owner=self.owner), program=self.program, owner=self.owner,
        )
        self.assertTrue(grant.is_valid(), grant.errors)
        self.assertEqual(grant.save().grant_status, Artwork.GrantStatus.PENDING)

        get_template('mi_fuego/art/dashboard.html')
        get_template('mi_fuego/art/form.html')
