from datetime import timedelta
from unittest.mock import patch

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
        # Entra viendo las propias (Faro), sin un bloque aparte de "Asignadas a vos".
        self.assertEqual([artwork.title for artwork in response.context['artworks']], ['Faro'])
        self.assertNotContains(response, 'assigned-title')
        self.assertNotContains(response, 'Limpiar')
        # Primeras líneas de la descripción, truncadas.
        snippet = self.artwork.proposal[:100]
        self.assertContains(response, snippet, count=1)
        self.assertNotContains(response, self.artwork.proposal.strip())
        self.assertContains(response, '…</span>', count=1)
        # "Todos" muestra el resto; sin descripción no se muestra el bloque vacío.
        everyone = self.client.get(self.url, {'contact': 'all'})
        self.assertEqual([artwork.title for artwork in everyone.context['artworks']], ['Faro', 'Sin texto'])
        self.assertContains(everyone, 'class="art-desc"', count=1)
        self.assertContains(everyone, 'Limpiar')
        # Toda la fila es un link real a la revisión.
        for artwork in (self.artwork, self.bare):
            review_url = reverse('artwork_review', args=[self.event.slug, artwork.pk])
            self.assertContains(everyone, f'<a class="art-row-link stretched-link" href="{review_url}">')

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

    def test_estafa_can_write_to_the_team_at_any_time(self):
        self.client.force_login(self.admin)
        review_url = reverse('artwork_review', args=[self.event.slug, self.artwork.pk])
        message_url = reverse('artwork_message', args=[self.event.slug, self.artwork.pk])
        Artwork.objects.filter(pk=self.artwork.pk).update(status=Artwork.Status.ACTIVE)
        self.assertContains(self.client.get(review_url), f'action="{message_url}"')
        with patch('art.views.send_mail') as send_mail:
            self.assertRedirects(self.client.post(message_url, {'message': '  '}), review_url)
            send_mail.assert_not_called()
            self.assertRedirects(self.client.post(message_url, {'message': 'Traigan el plano de luces.'}), review_url)
        self.assertEqual(send_mail.call_args.kwargs['template_name'], 'art_team_message')
        self.assertEqual(send_mail.call_args.kwargs['recipient_list'], ['artista@example.com'])
        self.assertEqual(send_mail.call_args.kwargs['context']['message'], 'Traigan el plano de luces.')
        self.artwork.refresh_from_db()
        self.assertEqual(self.artwork.status, Artwork.Status.ACTIVE)
        entry = self.artwork.history.get(kind='message')
        self.assertEqual((entry.actor, entry.detail), (self.admin, 'Traigan el plano de luces.'))
        self.assertContains(self.client.get(review_url), 'Traigan el plano de luces.')
        # El equipo de la propuesta no puede usarlo.
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(message_url, {'message': 'Hola'}).status_code, 403)

    def test_changes_by_others_since_your_last_visit_are_flagged(self):
        from . import history
        other = User.objects.create_user(username='otra', email='otra@example.com')
        self.client.force_login(self.admin)
        review_url = reverse('artwork_review', args=[self.event.slug, self.artwork.pk])
        everyone = {'contact': 'all'}
        # Lo anterior al aviso y lo que hace una misma no cuenta como novedad.
        old = history.log(self.artwork, history.EDIT, self.owner, 'Editó Detalles')
        type(old).objects.filter(pk=old.pk).update(created_at=history.NEWS_SINCE - timedelta(days=1))
        history.log(self.artwork, history.ESTAFA, self.admin, 'Marcó la propuesta como riesgosa')
        self.assertNotContains(self.client.get(self.url, everyone), 'class="art-news"')
        # Un cambio del equipo y otro de alguien más de ESTAFA.
        history.log(self.artwork, history.EDIT, self.owner, 'Editó Folleto')
        history.log(self.artwork, history.ESTAFA, other, 'Quitó el contacto de ESTAFA', important=False)
        listing = self.client.get(self.url, everyone)
        self.assertContains(listing, 'class="art-news"', count=1)
        self.assertNotContains(listing, '2 nuevos')
        self.assertEqual([a.title for a in self.client.get(self.url, {**everyone, 'news': '1'}).context['artworks']], ['Faro'])
        # Al abrirla se ven marcados y quedan vistos, sólo para quien la abrió.
        review = self.client.get(review_url)
        self.assertContains(review, '2 cambios que todavía no habías visto')
        self.assertContains(review, ' data-new>', count=2)
        self.assertNotContains(self.client.get(review_url), ' data-new>')
        self.assertNotContains(self.client.get(self.url, everyone), 'class="art-news"')
        self.assertEqual(list(self.client.get(self.url, {**everyone, 'news': '1'}).context['artworks']), [])
        history.log(self.artwork, history.EDIT, self.owner, 'Editó Checkout')
        self.assertContains(self.client.get(review_url), '1 cambio desde tu última visita')
        # "Marcar todas como leídas" apaga el aviso de todas a la vez.
        history.log(self.artwork, history.EDIT, self.owner, 'Editó Beca')
        history.log(self.bare, history.EDIT, self.owner, 'Editó Detalles')
        self.assertContains(self.client.get(self.url, everyone), 'class="art-news"', count=2)
        seen_url = reverse('art_admin_mark_seen', args=[self.event.slug])
        self.assertRedirects(self.client.post(seen_url), self.url)
        listing = self.client.get(self.url, everyone)
        self.assertNotContains(listing, 'class="art-news"')
        self.assertNotContains(listing, seen_url)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(seen_url).status_code, 403)

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
        titles = lambda flags: [a.title for a in self.client.get(self.url, {'flag': flags, 'contact': 'all'}).context['artworks']]
        self.assertEqual(titles(['fire']), ['Faro', 'Sin texto'])
        self.assertEqual(titles(['risk']), ['Faro'])
        self.assertEqual(titles(['risk', 'fire']), ['Faro'])
        self.assertEqual(titles(['sound']), [])
        self.assertEqual(self.client.get(self.url, {'flag': ['risk', 'fire']}).context['flag_summary'], '2 marcas')

    def test_history_records_who_changed_what(self):
        from art import history
        from art.digest import record_change
        review_url = reverse('artwork_review', args=[self.event.slug, self.artwork.pk])
        # Inscripta antes del cierre de inscripción: ese cierre es parte de su historia.
        Artwork.objects.filter(pk=self.artwork.pk).update(created_at=timezone.now() - timedelta(days=10))
        self.artwork.refresh_from_db()
        # Cambios seguidos de la misma persona en el mismo bloque quedan en una línea, con los campos sumados.
        record_change(self.artwork, 'Detalles', self.owner, fields=['Título'])
        record_change(self.artwork, 'Detalles', self.owner, fields=['Materiales', 'Título'])
        record_change(self.artwork, 'Galería', self.owner)
        self.client.force_login(self.admin)
        self.client.post(reverse('artwork_risk', args=[self.event.slug, self.artwork.pk]), {'risky': '1'})
        self.client.post(reverse('artwork_contact', args=[self.event.slug, self.artwork.pk]), {'estafa_contact': ''})
        entries = list(self.artwork.history.values_list('kind', 'summary', 'detail', 'important', 'actor'))
        self.assertEqual(entries, [
            ('edit', 'Editó Detalles', 'Título, Materiales', True, self.owner.pk),
            ('edit', 'Editó Galería', '', False, self.owner.pk),
            ('estafa', 'Marcó la propuesta como riesgosa', '', True, self.admin.pk),
            ('estafa', 'Quitó el contacto de ESTAFA', '', True, self.admin.pk),
        ])
        response = self.client.get(review_url)
        timeline = response.context['history']
        # Lo más nuevo arriba, con los cierres del programa que ya pasaron.
        self.assertEqual(timeline[0].summary, 'Quitó el contacto de ESTAFA')
        self.assertIn('Cierre de Inscripción', [entry.summary for entry in timeline if entry.kind == history.CLOSE])
        self.assertNotIn('Cierre de Propuesta', [entry.summary for entry in timeline])
        self.assertContains(response, 'editó Detalles')
        self.assertContains(response, 'Título, Materiales')
        self.assertContains(response, 'data-kind="close" data-important')

    def test_contact_filter_shows_how_many_each_one_has(self):
        Artwork.objects.create(
            event=self.event, owner=self.owner, title='Rechazada', estafa_contact=self.admin, status=Artwork.Status.REJECTED,
        )
        self.client.force_login(self.admin)
        response = self.client.get(self.url, {'contact': str(self.admin.pk)})
        choices = {value: total for value, _label, total in response.context['contact_choices']}
        self.assertEqual(choices[str(self.admin.pk)], 1)  # sin contar la rechazada
        self.assertEqual(response.context['no_contact_count'], 1)
        self.assertTrue(response.context['contact_summary'].endswith('(1)'))
        self.assertContains(response, 'class="filter-count"')
