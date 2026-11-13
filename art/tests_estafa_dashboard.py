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


class EstafaDashboardListTest(TestCase):
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
            transfers_enabled_until=now + timedelta(days=10), header_image='events/heros/no-image.jpg', title='FA', description='Evento',
            has_volunteers=True,
        )
        TeamMembership.objects.create(
            team=Team.objects.get(slug=ESTAFA_SLUG), user=self.admin, started_on=now.date() - timedelta(days=30),
        )
        ArtProgram.objects.create(
            event=self.event, is_current=True, grants_enabled=True,
            registration_opens=now - timedelta(days=30), registration_closes=now - timedelta(days=1),
            grant_opens=now - timedelta(days=30), grant_deadline=now + timedelta(days=2),
            proposal_deadline=now + timedelta(days=2),
            grant_report_deadline=now + timedelta(days=30),
        )
        self.artwork = Artwork.objects.create(
            event=self.event, owner=self.owner, title='Faro', estafa_contact=self.admin,
            proposal='Una torre de luz que gira lento sobre la playa. ' * 10,
        )
        self.bare = Artwork.objects.create(event=self.event, owner=self.owner, title='Sin texto')
        self.url = reverse('art_admin_dashboard', args=[self.event.slug])

    def test_dashboard_rows(self):
        self.client.force_login(self.admin)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        # Sin columna de checkout (el CSV sigue teniéndola).
        self.assertNotContains(response, '<th>Checkout</th>', html=True)
        # Primeras líneas de la descripción, truncadas, en la tabla y en "Asignadas a vos".
        snippet = self.artwork.proposal[:100]
        self.assertContains(response, snippet, count=2)
        self.assertNotContains(response, self.artwork.proposal.strip())
        self.assertContains(response, '…</span>', count=2)
        # Sin descripción no se muestra el bloque vacío: sólo las dos de "Faro".
        self.assertContains(response, 'class="art-desc"', count=2)
        # Toda la fila es un link real a la revisión.
        for artwork in (self.artwork, self.bare):
            review_url = reverse('artwork_review', args=[self.event.slug, artwork.pk])
            self.assertContains(response, f'<a class="art-row-link stretched-link" href="{review_url}">')

    def test_risk_mark_is_set_from_the_review_and_shown_in_the_list(self):
        self.client.force_login(self.admin)
        review_url = reverse('artwork_review', args=[self.event.slug, self.artwork.pk])
        risk_url = reverse('artwork_risk', args=[self.event.slug, self.artwork.pk])
        self.assertNotContains(self.client.get(self.url), 'class="art-flag art-flag-risk"')
        updated_at = self.artwork.updated_at
        self.assertRedirects(self.client.post(risk_url, {'risky': '1'}), review_url)
        self.artwork.refresh_from_db()
        self.assertTrue(self.artwork.is_risky)
        self.assertEqual(self.artwork.updated_at, updated_at)
        self.assertContains(self.client.get(self.url), 'class="art-flag art-flag-risk"', count=1)
        self.assertContains(self.client.get(review_url), 'id="review-risk-switch" checked')
        self.client.post(risk_url, {'risky': '0'})
        self.artwork.refresh_from_db()
        self.assertFalse(self.artwork.is_risky)

    def test_risk_mark_is_only_for_estafa(self):
        self.client.force_login(self.owner)
        risk_url = reverse('artwork_risk', args=[self.event.slug, self.artwork.pk])
        self.assertEqual(self.client.post(risk_url, {'risky': '1'}).status_code, 403)
        self.artwork.refresh_from_db()
        self.assertFalse(self.artwork.is_risky)
        self.artwork.is_risky = True
        self.artwork.save()
        response = self.client.get(reverse('artwork_edit', args=[self.artwork.pk]))
        self.assertNotContains(response, 'iesgosa')

    def test_list_filters_by_flags(self):
        self.artwork.is_risky = True
        self.artwork.burns = True
        self.artwork.save()
        self.bare.burns = True
        self.bare.save()
        self.client.force_login(self.admin)
        titles = lambda flags: [a.title for a in self.client.get(self.url, {'flag': flags}).context['artworks']]
        self.assertEqual(titles(['fire']), ['Faro', 'Sin texto'])
        self.assertEqual(titles(['risk']), ['Faro'])
        self.assertEqual(titles(['risk', 'fire']), ['Faro'])
        self.assertEqual(titles(['sound']), [])
        self.assertEqual(self.client.get(self.url, {'flag': ['risk', 'fire']}).context['flag_summary'], '2 marcas')
