from datetime import timedelta
from io import StringIO

from django.contrib.auth.models import User
from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from events.models import Event
from utils.email import send_mail

from .models import ArtProgram, Artwork


@override_settings(APP_URL='https://eventos.fuegoaustral.org')
class NewQuestionsEmailTest(TestCase):
    """Aviso a responsables de que sumamos preguntas al formulario de Arte."""

    def test_renders_subject_name_title_and_link(self):
        now = timezone.now()
        owner = User.objects.create_user(username='artista', email='artista@example.com', first_name='Lola')
        event = Event.objects.create(
            name='FA Carnaval', slug='fa-carnaval', active=True,
            start=now + timedelta(days=20), end=now + timedelta(days=24),
            transfers_enabled_until=now + timedelta(days=10), header_image='events/heros/no-image.jpg',
            title='FA', description='Evento',
        )
        artwork = Artwork.objects.create(event=event, owner=owner, title='Faro')

        send_mail(
            template_name='art_new_questions',
            recipient_list=[owner.email],
            context={
                'artwork': artwork,
                'first_name': owner.first_name,
                'artwork_path': reverse('artwork_edit', args=[artwork.pk]),
            },
        )

        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ['artista@example.com'])
        self.assertEqual(message.subject, 'Sumamos preguntas nuevas a tu Propuesta de Arte «Faro»')
        html = message.body
        self.assertIn('¡Hola Lola!', html)
        self.assertIn('«Faro»', html)
        self.assertIn('FA Carnaval', html)
        self.assertIn(f'https://eventos.fuegoaustral.org/mi-fuego/arte/obra/{artwork.pk}/', html)
        self.assertIn('Completar mi propuesta', html)


@override_settings(APP_URL='https://eventos.fuegoaustral.org')
class SendNewQuestionsCommandTest(TestCase):
    def setUp(self):
        now = timezone.now()
        event = Event.objects.create(
            name='FA Carnaval', slug='fa-carnaval', active=True,
            start=now + timedelta(days=20), end=now + timedelta(days=24),
            transfers_enabled_until=now + timedelta(days=10), header_image='events/heros/no-image.jpg',
            title='FA', description='Evento',
        )
        ArtProgram.objects.create(event=event, is_current=True)
        self.lola = User.objects.create_user(username='lola', email='lola@example.com', first_name='Lola')
        self.juan = User.objects.create_user(username='juan', email='juan@example.com')
        collaborator = User.objects.create_user(username='colab', email='colab@example.com')
        self.faro = Artwork.objects.create(event=event, owner=self.lola, title='Faro')
        self.faro.collaborators.add(collaborator)
        Artwork.objects.create(event=event, owner=self.juan, title='Domo')
        Artwork.objects.create(event=event, owner=User.objects.create_user(username='sin', email=''), title='Sin email')

    def run_command(self, *args):
        call_command('send_art_new_questions', *args, stdout=StringIO())

    def test_dry_run_sends_nothing(self):
        self.run_command()
        self.assertEqual(len(mail.outbox), 0)

    def test_only_sends_one_to_that_address(self):
        self.run_command('--only', 'raskovsky@gmail.com')
        self.assertEqual([m.to for m in mail.outbox], [['raskovsky@gmail.com']])
        self.assertIn('«Faro»', mail.outbox[0].subject)
        # La prueba no cuenta como enviado: el --send real igual le llega a todos.
        self.run_command('--send')
        self.assertEqual(len(mail.outbox), 3)

    def test_send_once_per_owner(self):
        self.run_command('--send')
        self.assertEqual(sorted(m.to[0] for m in mail.outbox), ['juan@example.com', 'lola@example.com'])
        self.run_command('--send')
        self.assertEqual(len(mail.outbox), 2)
