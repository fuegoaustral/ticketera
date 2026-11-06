from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from events.models import Event
from user_profile.models import Profile

from .models import ArtProgram, Artwork

STANDARD_BUBBLE = "type: 'standard'"
ROOM_IN_BAR = '[data-sticky-actions].chat-bubble-room'
ROOM_ABOVE_FOOTER = 'body > footer { margin-top: calc(var(--chat-bubble-size, 64px) + 2rem) !important; }'


class ChatBubbleTest(TestCase):
    """La burbuja redonda de Chatwoot se ve siempre (también en el teléfono) y las páginas le hacen lugar."""

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
        ArtProgram.objects.create(
            event=self.event, is_current=True, grants_enabled=True,
            registration_opens=now - timedelta(days=30), registration_closes=now + timedelta(days=1),
            grant_opens=now - timedelta(days=30), grant_deadline=now + timedelta(days=2),
            proposal_deadline=now + timedelta(days=2),
            understanding_letter_digital_deadline=now + timedelta(days=5),
            grant_report_deadline=now + timedelta(days=30),
        )
        self.artwork = Artwork.objects.create(event=self.event, owner=self.owner, title='Faro')
        self.client.force_login(self.owner)

    def assert_bubble_always_visible(self, response):
        self.assertContains(response, STANDARD_BUBBLE)
        self.assertNotContains(response, 'expanded_bubble')
        self.assertNotContains(response, 'hideMessageBubble')
        self.assertNotContains(response, 'toggleBubbleVisibility')
        self.assertContains(response, ROOM_ABOVE_FOOTER)

    def test_artwork_form_bar_makes_room_for_the_bubble(self):
        response = self.client.get(reverse('artwork_edit', args=[self.artwork.pk]))
        self.assert_bubble_always_visible(response)
        self.assertContains(response, 'data-sticky-actions>', count=1)
        self.assertContains(response, ROOM_IN_BAR)

    def test_grant_page_bar_makes_room_for_the_bubble(self):
        response = self.client.get(reverse('artwork_grant', args=[self.artwork.pk]))
        self.assert_bubble_always_visible(response)
        self.assertContains(response, 'data-sticky-actions>', count=1)

    def test_art_dashboard_keeps_the_bubble_without_phone_only_chat_buttons(self):
        response = self.client.get(reverse('art_dashboard'))
        self.assert_bubble_always_visible(response)
        self.assertNotContains(response, 'art-chat-button')

    def test_other_pages_get_the_same_bubble(self):
        response = self.client.get(reverse('my_ticket'), follow=True)
        self.assert_bubble_always_visible(response)
