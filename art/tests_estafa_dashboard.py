from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from events.models import Event
from teams.models import Team, TeamMembership
from user_profile.models import Profile

from .estafa import ESTAFA_SLUG
from .models import ArtProgram, Artwork, ArtworkGrantItem


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


class ArtTeamsTest(TestCase):
    """MAD lee todo, Becas además gestiona las becas, ESTAFA hace todo."""

    def setUp(self):
        now = timezone.now()
        Event.objects.filter(is_main=True).update(is_main=False)
        self.event = Event.objects.create(
            name='FA Equipos', slug='fa-equipos', active=True, is_main=True,
            start=now + timedelta(days=20), end=now + timedelta(days=24),
            transfers_enabled_until=now + timedelta(days=10), header_image='events/heros/no-image.jpg',
            title='FA', description='Evento', has_volunteers=True,
        )
        self.program = ArtProgram.objects.create(
            event=self.event, is_current=True, grants_enabled=True,
            registration_opens=now - timedelta(days=30), registration_closes=now - timedelta(days=1),
            grant_opens=now - timedelta(days=30), grant_deadline=now + timedelta(days=2),
            proposal_deadline=now + timedelta(days=2), grant_report_deadline=now + timedelta(days=30),
        )
        self.owner = User.objects.create_user(username='duenia', email='duenia@example.com')
        self.artwork = Artwork.objects.create(
            event=self.event, owner=self.owner, title='Faro', grant_requested=True,
            grant_status=Artwork.GrantStatus.PENDING,
        )
        self.users = {}
        for slug in ('estafa', 'mad', 'becas'):
            user = User.objects.create_user(username=slug, email=f'{slug}@example.com')
            TeamMembership.objects.create(
                team=Team.objects.get(slug=slug), user=user, started_on=now.date() - timedelta(days=30),
            )
            self.users[slug] = user
        self.outsider = User.objects.create_user(username='otra', email='otra@example.com')
        for index, user in enumerate((self.owner, self.outsider, *self.users.values()), start=1):
            user.profile.document_number = f'2000000{index}'
            user.profile.phone = f'+54911000001{index:02d}'
            user.profile.profile_completion = Profile.COMPLETE
            user.profile.save()
        self.review_url = reverse('artwork_review', args=[self.event.slug, self.artwork.pk])
        self.grant_url = reverse('artwork_grant', args=[self.artwork.pk])
        self.decision_url = reverse('artwork_grant_decision', args=[self.event.slug, self.artwork.pk])

    def test_mad_tab_lists_only_artworks_asking_for_mad(self):
        url = reverse('art_mad_dashboard', args=[self.event.slug])
        Artwork.objects.create(event=self.event, owner=self.owner, title='Carreta', is_movable=True)
        Artwork.objects.create(
            event=self.event, owner=self.owner, title='Buque', needs_mad_assistance=True, has_own_mobility=True,
            proposal='Un buque que navega la playa',
        )
        Artwork.objects.create(event=self.event, owner=self.owner, title='Nave', has_own_mobility=True)
        Artwork.objects.create(event=self.event, owner=self.owner, title='Grua', needs_mad_assistance=True)
        Artwork.objects.create(
            event=self.event, owner=self.owner, title='Descartada', has_own_mobility=True, status=Artwork.Status.REJECTED,
        )
        for slug in ('estafa', 'becas', 'mad'):
            self.client.force_login(self.users[slug])
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, slug)
            titles = [artwork.title for artwork in response.context['artworks']]
            # Moverse sin motor (Carreta) no es un pedido a MAD.
            self.assertEqual(titles, ['Buque', 'Grua', 'Nave'], slug)
        self.assertContains(response, 'Un buque que navega la playa')
        self.assertNotContains(response, 'Se mueve')
        response = self.client.get(url, {'ask': ['car']})
        self.assertEqual([artwork.title for artwork in response.context['artworks']], ['Buque', 'Nave'])
        response = self.client.get(url, {'q': 'gru'})
        self.assertEqual([artwork.title for artwork in response.context['artworks']], ['Grua'])
        self.assertContains(response, reverse('artwork_mad', args=[self.event.slug, response.context['artworks'][0].pk]))
        # El contacto de ESTAFA es sólo del listado de propuestas.
        self.assertNotContains(response, 'Sin contacto')
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_mad_opens_a_proposal_with_the_mad_part_messages_and_the_proposal(self):
        Artwork.objects.filter(pk=self.artwork.pk).update(needs_mad_assistance=True, proposal='Un faro que gira')
        mad_url = reverse('artwork_mad', args=[self.event.slug, self.artwork.pk])
        self.client.force_login(self.users['mad'])
        # Quien es sólo de MAD no abre la revisión de ESTAFA: va a su vista.
        self.assertRedirects(self.client.get(self.review_url), mad_url)
        response = self.client.get(mad_url)
        self.assertEqual(response.status_code, 200)
        for text in ('Qué pide a MAD', 'Mensaje para el equipo', 'Volver a MAD', 'La propuesta', 'Un faro que gira', 'duenia@example.com'):
            self.assertContains(response, text)
        for text in (
            'Checkout', 'Declaración', 'Proveedores', 'Ingreso anticipado', 'Late checkout', 'Seguridad', 'Se quema',
            'Sonido', 'Galería', 'Devolución sobre la beca', 'Historial', 'Guardar contacto', 'Lo registró ESTAFA',
            'Ver la propuesta completa', 'Check-in', 'Se mueve',
        ):
            self.assertNotContains(response, text)
        with patch('art.views.send_mail'):
            sent = self.client.post(
                reverse('artwork_message', args=[self.event.slug, self.artwork.pk]),
                {'message': 'Coordinamos el traslado', 'return_to': 'mad'},
            )
        self.assertRedirects(sent, mad_url + '#mensajes')
        self.assertContains(self.client.get(mad_url), 'Coordinamos el traslado')
        # ESTAFA también la ve, con el enlace a la revisión completa.
        self.client.force_login(self.users['estafa'])
        self.assertContains(self.client.get(mad_url), 'Ver la propuesta completa')
        self.assertEqual(self.client.get(self.review_url).status_code, 200)
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(mad_url).status_code, 403)

    def test_mad_only_member_lands_on_the_mad_tab(self):
        self.client.force_login(self.users['mad'])
        self.assertRedirects(
            self.client.get(reverse('estafa_home')), reverse('art_mad_dashboard', args=[self.event.slug]),
        )
        self.client.force_login(self.users['estafa'])
        self.assertRedirects(
            self.client.get(reverse('estafa_home')), reverse('art_admin_dashboard', args=[self.event.slug]),
            fetch_redirect_response=False,
        )

    def decision(self, action='approve', **data):
        fields = {'approve': {'amount': '450.000,00', 'notes': ''}}.get(action, {})
        return {
            'action': action, **fields,
            'expected_version': Artwork.objects.get(pk=self.artwork.pk).version, **data,
        }

    def test_mad_and_becas_read_everything_without_editing(self):
        for slug in ('mad', 'becas'):
            self.client.force_login(self.users[slug])
            # Cada equipo entra por su pestaña.
            landing = 'art_grants_dashboard' if slug == 'becas' else 'art_mad_dashboard'
            self.assertRedirects(self.client.get(reverse('estafa_home')), reverse(landing, args=[self.event.slug]))
            self.assertContains(self.client.get(reverse('art_admin_dashboard', args=[self.event.slug])), 'Faro')
            self.assertEqual(self.client.get(reverse('art_admin_export', args=[self.event.slug])).status_code, 200)
            # Becas lee la revisión completa; MAD, su vista de la propuesta, sin lo de ESTAFA.
            response = self.client.get(self.review_url, follow=True)
            if slug == 'becas':
                self.assertContains(response, 'Lo registró ESTAFA')
            else:
                self.assertNotContains(response, 'Lo registró ESTAFA')
            self.assertNotContains(response, 'Guardar cambios')
            self.assertNotContains(response, 'Aprobar')
            self.assertNotContains(response, 'Guardar contacto')
            self.assertNotContains(response, 'Agregar fotos al reporte')
            self.assertEqual(self.client.get(self.grant_url).status_code, 200)
            # Nada de lo que decide o edita ESTAFA.
            self.assertEqual(self.client.get(reverse('artwork_edit', args=[self.artwork.pk])).status_code, 404)
            self.assertEqual(self.client.post(self.review_url, {}).status_code, 403)
            for name, data in (
                ('artwork_status', {'transition': 'approve'}),
                ('artwork_risk', {'risky': '1'}), ('artwork_contact', {'estafa_contact': ''}),
            ):
                self.assertEqual(self.client.post(reverse(name, args=[self.event.slug, self.artwork.pk]), data).status_code, 403, name)
        self.artwork.refresh_from_db()
        self.assertEqual(self.artwork.status, Artwork.Status.PENDING)
        self.assertFalse(self.artwork.is_risky)

    def test_mad_and_becas_write_to_the_team(self):
        message_url = reverse('artwork_message', args=[self.event.slug, self.artwork.pk])
        for slug, name in (('mad', 'MAD'), ('becas', 'Becas')):
            self.client.force_login(self.users[slug])
            # MAD escribe desde su vista de la propuesta; Becas, desde la revisión.
            page_url = reverse('artwork_mad', args=[self.event.slug, self.artwork.pk]) if slug == 'mad' else self.review_url
            self.assertContains(self.client.get(page_url), 'Mensaje para el equipo')
            self.assertContains(self.client.get(self.grant_url), 'Mensaje para el equipo')
            with patch('art.views.send_mail') as send_mail:
                self.assertRedirects(self.client.post(message_url, {'message': f'Hola de {name}'}), page_url)
            self.assertEqual(send_mail.call_args.kwargs['context']['sender_team'], name)
            self.assertEqual(send_mail.call_args.kwargs['recipient_list'], ['duenia@example.com'])
            self.assertTrue(self.artwork.history.filter(kind='message', detail=f'Hola de {name}').exists())
            # Desde la beca, vuelve a la beca.
            with patch('art.views.send_mail'):
                response = self.client.post(message_url, {'message': 'Otra', 'return_to': 'grant'})
            self.assertRedirects(response, self.grant_url + '#mensaje')
        # El equipo de la obra no se escribe a sí mismo.
        self.client.force_login(self.owner)
        self.assertNotContains(self.client.get(self.grant_url), 'Mensaje para el equipo')

    def test_mad_cannot_touch_the_grant(self):
        self.client.force_login(self.users['mad'])
        response = self.client.get(self.grant_url)
        self.assertNotContains(response, 'Decisión de la beca')
        self.assertNotContains(response, 'Agregar ítem')
        self.assertEqual(self.client.post(self.decision_url, self.decision()).status_code, 403)
        self.assertEqual(self.client.post(self.grant_url, {'grant_justification': 'x'}).status_code, 404)
        self.assertEqual(self.client.post(reverse('grant_item_create', args=[self.artwork.pk, 'budget']), {}).status_code, 403)
        self.artwork.refresh_from_db()
        self.assertEqual(self.artwork.grant_status, Artwork.GrantStatus.PENDING)

    def test_becas_and_estafa_decide_the_grant(self):
        for slug in ('becas', 'estafa'):
            self.client.force_login(self.users[slug])
            response = self.client.get(self.grant_url)
            self.assertContains(response, 'Estado de la beca')
            self.assertContains(response, 'Agregar ítem')
            # La decisión son acciones, no un desplegable de estados.
            self.assertNotContains(response, 'name="grant_status"')
            for label in ('Aprobar', 'Pedir información', 'No aprobar'):
                self.assertContains(response, label)
            self.assertNotContains(response, 'Registrar pago')
            # Aprobar pide el monto.
            response = self.client.post(self.decision_url, self.decision(amount=''))
            self.assertContains(response, 'Indicá el monto aprobado.')
            with patch('art.views.send_mail') as send_mail, self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(self.decision_url, self.decision())
            self.assertRedirects(response, self.grant_url + '#decision')
            self.assertEqual(send_mail.call_args.kwargs['template_name'], 'art_grant_decision')
            self.assertEqual(send_mail.call_args.kwargs['recipient_list'], ['duenia@example.com'])
            # Ya aprobada, aprobar de nuevo no corresponde: no cambia nada ni avisa.
            with patch('art.views.send_mail') as send_mail, self.captureOnCommitCallbacks(execute=True):
                self.client.post(self.decision_url, self.decision())
            send_mail.assert_not_called()
            self.artwork.refresh_from_db()
            self.assertEqual(self.artwork.grant_status, Artwork.GrantStatus.APPROVED)
            self.assertEqual(self.artwork.grant_approved_amount, 450000)
            self.assertIsNotNone(self.artwork.grant_decided_at)
            # El monto se muestra como se escribe.
            self.assertContains(self.client.get(self.grant_url), 'value="450.000,00"')
            Artwork.objects.filter(pk=self.artwork.pk).update(
                grant_status=Artwork.GrantStatus.PENDING, grant_approved_amount=None,
            )
        self.assertTrue(self.artwork.history.filter(summary='Aprobó la beca por ARS 450.000,00').exists())

    def test_grant_actions_follow_the_status(self):
        self.client.force_login(self.users['becas'])

        def act(action, **data):
            with patch('art.views.send_mail') as send_mail, self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(self.decision_url, self.decision(action, **data))
            self.artwork.refresh_from_db()
            return response, send_mail

        # Pedir información exige contar qué falta.
        response, _ = act('request_info', notes='')
        self.assertContains(response, 'Contale al equipo qué falta.')
        response, send_mail = act('request_info', notes='Falta el presupuesto de la estructura')
        self.assertEqual(self.artwork.grant_status, Artwork.GrantStatus.INFO_REQUIRED)
        send_mail.assert_called_once()
        # Una versión vieja de la página no pisa lo que se guardó después.
        response, _ = act('approve', expected_version=0)
        self.assertContains(response, 'Otra persona guardó cambios')
        act('approve')
        self.assertEqual(self.artwork.grant_status, Artwork.GrantStatus.APPROVED)
        self.assertEqual(self.artwork.grant_decision_notes, '')
        # Registrar el pago pide la fecha.
        response, _ = act('pay', paid_at='', reference='')
        self.assertContains(response, 'Indicá la fecha de pago.')
        _, send_mail = act('pay', paid_at='2026-12-20', reference='TRF 1')
        self.assertEqual(self.artwork.grant_status, Artwork.GrantStatus.PAID)
        send_mail.assert_called_once()
        # Corregir sólo la referencia interna no le avisa al equipo.
        _, send_mail = act('fix_payment', paid_at='2026-12-20', reference='TRF 2')
        self.assertEqual(self.artwork.grant_payment_reference, 'TRF 2')
        send_mail.assert_not_called()
        # Con la rendición enviada: pedir cambios la devuelve al equipo; aprobarla la cierra.
        Artwork.objects.filter(pk=self.artwork.pk).update(grant_status=Artwork.GrantStatus.REPORTED)
        act('request_report_changes', notes='Falta un comprobante')
        self.assertEqual(self.artwork.grant_status, Artwork.GrantStatus.PAID)
        Artwork.objects.filter(pk=self.artwork.pk).update(grant_status=Artwork.GrantStatus.REPORTED)
        act('approve_report')
        self.assertEqual(self.artwork.grant_status, Artwork.GrantStatus.CLOSED)
        act('reopen_report')
        self.assertEqual(self.artwork.grant_status, Artwork.GrantStatus.REPORTED)
        self.assertTrue(self.artwork.history.filter(summary='Registró el pago de la beca del 20/12/2026').exists())

    def test_grants_tab_lists_requests_and_only_managers_export(self):
        Artwork.objects.filter(pk=self.artwork.pk).update(
            grant_currency='USD', grant_requested_amount=800, grant_payee_name='Ana Artista',
            grant_payee_tax_id='20123456786', grant_payee_account='ana.artista.mp',
        )
        ArtworkGrantItem.objects.create(artwork=self.artwork, phase='budget', concept='Madera', amount=1000)
        ArtworkGrantItem.objects.create(artwork=self.artwork, phase='expense', concept='Gasto', amount=5)
        Artwork.objects.create(event=self.event, owner=self.owner, title='Sin beca')
        tab_url = reverse('art_grants_dashboard', args=[self.event.slug])
        export_url = reverse('art_grants_export', args=[self.event.slug])
        for slug in ('estafa', 'becas', 'mad'):
            self.client.force_login(self.users[slug])
            response = self.client.get(tab_url)
            self.assertContains(response, 'Faro')
            self.assertNotContains(response, 'Sin beca')
            self.assertContains(response, '1.000,00')
            self.assertContains(response, '800,00')
            self.assertContains(response, f'href="{self.grant_url}"')
            # Datos de cobro en la tabla: sólo para quienes gestionan becas.
            (self.assertNotContains if slug == 'mad' else self.assertContains)(response, 'ana.artista.mp')
            (self.assertNotContains if slug == 'mad' else self.assertContains)(response, '20123456786')
            self.assertContains(response, f'mailto:{self.owner.email}')
            self.assertContains(self.client.get(tab_url + '?status=approved'), 'No hay becas que coincidan con estos filtros.')
            export = self.client.get(export_url)
            if slug == 'mad':
                self.assertNotContains(response, 'Exportar CSV')
                self.assertEqual(export.status_code, 403)
                # MAD lee la beca, sin los datos de cobro.
                grant_page = self.client.get(self.grant_url)
                self.assertNotContains(grant_page, 'Ana Artista')
                self.assertNotContains(grant_page, '20123456786')
                self.assertNotContains(grant_page, 'ana.artista.mp')
            else:
                self.assertContains(response, 'Exportar CSV')
                content = export.content.decode('utf-8-sig')
                self.assertIn('Faro', content)
                self.assertIn('USD,1000.00,1000.00,800.00', content)
                self.assertIn('Ana Artista,20123456786,ana.artista.mp', content)
                self.assertNotIn('Madera', content)
                self.assertNotIn('Sin beca', content)
                self.assertContains(self.client.get(self.grant_url), 'ana.artista.mp')
        # El equipo de la propuesta ve sus propios datos de cobro.
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(self.grant_url), 'ana.artista.mp')

    def test_grants_tab_lists_incomplete_requests(self):
        # Empezó a cargar la beca sin terminarla: aparece como «Incompleta». Quien nunca la tocó, no.
        draft = Artwork.objects.create(
            event=self.event, owner=self.owner, title='Borrador', grant_justification='Para los materiales',
        )
        only_items = Artwork.objects.create(event=self.event, owner=self.owner, title='Solo items')
        ArtworkGrantItem.objects.create(artwork=only_items, phase='budget', item_type='labor', concept='Soldador', amount=10)
        untouched = Artwork.objects.create(event=self.event, owner=self.owner, title='Sin beca')
        self.assertTrue(draft.grant_incomplete and only_items.grant_incomplete)
        self.assertFalse(untouched.grant_started)
        self.assertEqual(draft.grant_status_label, 'Incompleta')
        tab_url = reverse('art_grants_dashboard', args=[self.event.slug])
        self.client.force_login(self.users['becas'])
        response = self.client.get(tab_url)
        self.assertEqual([artwork.title for artwork in response.context['artworks']], ['Borrador', 'Faro', 'Solo items'])
        self.assertContains(response, 'grant-incomplete-badge', count=3)  # dos filas y el estilo
        self.assertContains(response, 'Falta: presupuesto, monto pedido, datos de cobro, condiciones')
        self.assertEqual(response.context['status_choices'][0], ('incomplete', 'Incompleta', 2))
        filtered = self.client.get(tab_url + '?status=incomplete')
        self.assertEqual([artwork.title for artwork in filtered.context['artworks']], ['Borrador', 'Solo items'])
        both = self.client.get(tab_url + f'?status=incomplete&status={self.artwork.grant_status}')
        self.assertEqual(len(both.context['artworks']), 3)
        content = self.client.get(reverse('art_grants_export', args=[self.event.slug])).content.decode('utf-8-sig')
        self.assertIn('Borrador', content)
        self.assertIn('Incompleta', content)
        self.assertNotIn('Sin beca', content)
        # Sin decisiones mientras está incompleta; sí el mensaje al equipo.
        page = self.client.get(reverse('artwork_grant', args=[draft.pk]))
        self.assertContains(page, 'Beca · Incompleta')
        self.assertNotContains(page, 'Estado de la beca')
        self.assertContains(page, 'Mensaje para el equipo')
        # «Pidió beca» en Propuestas cuenta lo mismo que la pestaña.
        self.client.force_login(self.users['estafa'])
        listed = self.client.get(reverse('art_admin_dashboard', args=[self.event.slug]) + '?flag=grant&mine=0')
        titles = sorted(artwork.title for artwork in listed.context['artworks'])
        self.assertEqual(titles, ['Borrador', 'Faro', 'Solo items'])

    def test_grants_tab_searches_and_filters_like_the_proposals_list(self):
        other_owner = User.objects.create_user('olga', 'olga@example.com', 'x', first_name='Olga', last_name='Molinera')
        Artwork.objects.create(
            event=self.event, owner=other_owner, title='Molino', grant_status=Artwork.GrantStatus.APPROVED,
        )
        tab_url = reverse('art_grants_dashboard', args=[self.event.slug])
        export_url = reverse('art_grants_export', args=[self.event.slug])
        self.client.force_login(self.users['estafa'])

        def titles(query):
            return [artwork.title for artwork in self.client.get(tab_url + query).context['artworks']]

        self.assertEqual(titles(''), ['Faro', 'Molino'])
        self.assertEqual(titles('?q=molin'), ['Molino'])
        self.assertEqual(titles('?q=olga@example'), ['Molino'])
        self.assertEqual(titles('?q=Olga'), ['Molino'])
        self.assertEqual(titles('?status=approved'), ['Molino'])
        self.assertEqual(titles(f'?status=approved&status={self.artwork.grant_status}'), ['Faro', 'Molino'])
        # El contacto de ESTAFA es sólo del listado de propuestas: acá no filtra ni se muestra.
        self.assertEqual(titles('?contact=none'), ['Faro', 'Molino'])
        self.assertNotContains(self.client.get(tab_url), 'Sin contacto')
        self.assertEqual(titles('?q=nada-de-nada'), [])
        response = self.client.get(tab_url + '?q=molin&status=approved')
        self.assertContains(response, 'Limpiar')
        self.assertContains(response, 'name="q" value="molin"')
        self.assertContains(response, f'{export_url}?q=molin&amp;status=approved')
        self.assertNotContains(self.client.get(tab_url), 'Limpiar')
        # El export respeta la búsqueda y los filtros.
        content = self.client.get(export_url + '?q=molin').content.decode('utf-8-sig')
        self.assertIn('Molino', content)
        self.assertNotIn('Faro', content)

    def test_people_outside_the_teams_stay_out(self):
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(reverse('estafa_home')).status_code, 403)
        self.assertEqual(self.client.get(self.review_url).status_code, 403)
        self.assertEqual(self.client.get(self.grant_url).status_code, 404)
        self.assertEqual(self.client.post(self.decision_url, self.decision()).status_code, 403)
        self.assertEqual(self.client.get(reverse('art_grants_dashboard', args=[self.event.slug])).status_code, 403)
        self.assertEqual(self.client.get(reverse('art_grants_export', args=[self.event.slug])).status_code, 403)


class BulkTeamMessageTest(TestCase):
    """Un mismo mensaje al equipo de cada propuesta filtrada en un listado."""

    def setUp(self):
        now = timezone.now()
        Event.objects.filter(is_main=True).update(is_main=False)
        self.event = Event.objects.create(
            name='FA Mensajes', slug='fa-mensajes', active=True, is_main=True,
            start=now + timedelta(days=20), end=now + timedelta(days=24),
            transfers_enabled_until=now + timedelta(days=10), header_image='events/heros/no-image.jpg',
            title='FA', description='Evento', has_volunteers=True,
        )
        ArtProgram.objects.create(
            event=self.event, is_current=True,
            registration_opens=now - timedelta(days=30), registration_closes=now + timedelta(days=1),
        )
        self.users = {}
        for slug in ('estafa', 'mad', 'becas'):
            user = User.objects.create_user(username=f'bulk-{slug}', email=f'bulk-{slug}@example.com')
            TeamMembership.objects.create(
                team=Team.objects.get(slug=slug), user=user, started_on=now.date() - timedelta(days=30),
            )
            self.users[slug] = user
        self.outsider = User.objects.create_user(username='bulk-otra', email='bulk-otra@example.com')
        self.owners = [
            User.objects.create_user(username=f'bulk-owner{index}', email=f'owner{index}@example.com')
            for index in range(3)
        ]
        people = (self.outsider, *self.users.values(), *self.owners)
        for index, user in enumerate(people, start=1):
            user.profile.document_number = f'3000000{index}'
            user.profile.phone = f'+54911000002{index:02d}'
            user.profile.profile_completion = Profile.COMPLETE
            user.profile.save()
        self.faro = Artwork.objects.create(event=self.event, owner=self.owners[0], title='Faro', needs_mad_assistance=True)
        self.faro.collaborators.add(self.owners[2])
        self.nube = Artwork.objects.create(event=self.event, owner=self.owners[1], title='Nube')
        self.url = reverse('art_bulk_message', args=[self.event.slug])

    def _post(self, query, **data):
        page = self.client.get(self.url + query)
        payload = {
            'subject': 'Abrieron las becas', 'message': 'Hola.\nYa pueden pedirla.', 'confirm': 'on',
            'count': page.context['form'].initial['count'], 'batch': str(page.context['form'].initial['batch']),
        }
        payload.update(data)
        return self.client.post(self.url + query, payload)

    def test_outsiders_and_wrong_lists_are_refused(self):
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.client.force_login(self.users['mad'])
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.get(self.url + '?lista=mad').status_code, 200)

    def test_lists_show_the_button_and_the_screen_counts_the_filtered(self):
        self.client.force_login(self.users['estafa'])
        dashboard = self.client.get(reverse('art_admin_dashboard', args=[self.event.slug]) + '?q=Faro')
        self.assertContains(dashboard, 'Escribir a las filtradas')
        self.assertContains(dashboard, 'mensaje/?lista=artworks&amp;q=Faro')
        page = self.client.get(self.url + '?lista=artworks&q=Faro')
        self.assertContains(page, 'Se lo vas a enviar a 1 propuesta')
        self.assertContains(page, '(2 personas)')
        self.assertContains(page, 'Enviar a 1 propuesta')
        self.assertNotContains(page, 'Nube')
        everything = self.client.get(self.url + '?lista=artworks')
        self.assertContains(everything, 'Se lo vas a enviar a 2 propuestas')

    def test_sends_one_email_per_person_in_bcc_and_records_the_message_on_every_artwork(self):
        # Una misma persona en dos propuestas lo recibe una sola vez.
        self.nube.collaborators.add(self.owners[0])
        self.client.force_login(self.users['becas'])
        mail.outbox.clear()
        response = self._post('?lista=artworks')
        self.assertRedirects(response, reverse('art_admin_dashboard', args=[self.event.slug]))
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.subject, 'Abrieron las becas')
        self.assertEqual(sorted(message.bcc), ['owner0@example.com', 'owner1@example.com', 'owner2@example.com'])
        self.assertEqual(len(message.to), 1)
        self.assertNotIn('owner', message.to[0])
        # No recibe respuestas ni muestra a quién más le llegó.
        self.assertTrue(message.extra_headers['Reply-To'].startswith('no-reply@'))
        self.assertNotIn('bulk-becas@example.com', str(message.extra_headers))
        self.assertNotIn('owner1@example.com', message.body)
        self.assertNotIn('Para contestarle', message.body)
        self.assertIn('Este email no recibe respuestas', message.body)
        self.assertIn('Mensaje de Becas', message.body)
        self.assertIn('Ir a mis propuestas de Arte', message.body)
        self.assertNotIn('Faro', message.body)
        for artwork in (self.faro, self.nube):
            entry = artwork.history.get(kind='message')
            self.assertEqual(entry.actor, self.users['becas'])
            self.assertEqual(entry.detail, 'Abrieron las becas\n\nHola.\nYa pueden pedirla.')

    def test_message_html_is_sanitised_and_buttons_become_email_buttons(self):
        self.client.force_login(self.users['estafa'])
        mail.outbox.clear()
        dirty = (
            '<div onclick="steal()">Hola <b>equipo</b><script>alert(1)</script></div>'
            '<p><a href="javascript:alert(1)">trampa</a> <a href="https://becas.example.com/reglas" style="x">Reglamento</a></p>'
            '<p><a class="msg-btn" href="https://becas.example.com/pedir" onclick="x()">Pedir beca</a></p>'
            '<ul><li>uno</li><li><i>dos</i></li></ul><img src="x" onerror="alert(2)">'
        )
        self._post('?lista=artworks', message=dirty)
        body = mail.outbox[0].body
        for forbidden in ('<script', 'alert(1)', 'onclick', 'javascript:', 'onerror', 'src="x"', 'steal()', 'style="x"'):
            self.assertNotIn(forbidden, body)
        self.assertIn('<strong>equipo</strong>', body)
        self.assertIn('<em>dos</em>', body)
        self.assertIn('href="https://becas.example.com/reglas"', body)
        # El botón sale con el mismo armado que los botones de los emails del sitio.
        self.assertRegex(body, r'(?s)<table[^>]*btn[^>]*>.*href="https://becas.example.com/pedir".*Pedir beca')
        entry = self.faro.history.get(kind='message')
        self.assertEqual(
            entry.detail,
            'Abrieron las becas\n\nHola equipo\n\ntrampa Reglamento (https://becas.example.com/reglas)\n\n'
            'Pedir beca (https://becas.example.com/pedir)\n\n• uno\n• dos',
        )
        self.assertNotIn('<', entry.detail)
        # Un mensaje que queda vacío al limpiarlo no se manda.
        mail.outbox.clear()
        response = self._post('?lista=artworks', message='<script>alert(1)</script><p> </p>')
        self.assertContains(response, 'Escribí el mensaje.')
        self.assertEqual(len(mail.outbox), 0)

    def test_many_people_go_in_bcc_chunks(self):
        from art import views
        self.client.force_login(self.users['estafa'])
        for index in range(100):
            self.nube.collaborators.add(User.objects.create_user(username=f'bulk-extra{index}', email=f'extra{index:03d}@example.com'))
        mail.outbox.clear()
        self._post('?lista=artworks')
        self.assertEqual([len(message.bcc) for message in mail.outbox], [45, 45, 13])
        self.assertTrue(all(len(message.bcc) <= views.BULK_BCC_CHUNK for message in mail.outbox))
        everyone = [email for message in mail.outbox for email in message.bcc]
        self.assertEqual(len(everyone), len(set(everyone)))
        self.assertEqual(self.nube.history.filter(kind='message').count(), 1)

    def test_needs_confirmation_and_a_matching_count(self):
        self.client.force_login(self.users['estafa'])
        mail.outbox.clear()
        response = self._post('?lista=artworks', confirm='')
        self.assertContains(response, 'Confirmá el envío.')
        response = self._post('?lista=artworks', count=5)
        self.assertContains(response, 'El listado cambió: ahora son 2 propuestas')
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(self.faro.history.filter(kind='message').exists())

    def test_double_submit_does_not_resend(self):
        self.client.force_login(self.users['estafa'])
        mail.outbox.clear()
        page = self.client.get(self.url + '?lista=mad')
        payload = {
            'subject': 'MAD', 'message': 'Coordinamos.', 'confirm': 'on',
            'count': page.context['form'].initial['count'], 'batch': str(page.context['form'].initial['batch']),
        }
        self.client.post(self.url + '?lista=mad', payload)
        self.client.post(self.url + '?lista=mad', payload)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(sorted(mail.outbox[0].bcc), ['owner0@example.com', 'owner2@example.com'])
        self.assertEqual(self.faro.history.filter(kind='message').count(), 1)
        self.assertFalse(self.nube.history.filter(kind='message').exists())

    def test_sends_a_test_only_to_the_sender(self):
        self.client.force_login(self.users['estafa'])
        mail.outbox.clear()
        page = self.client.get(self.url + '?lista=artworks')
        payload = {
            'subject': 'Abrieron las becas', 'message': 'Hola a todos.', 'action': 'test',
            'count': page.context['form'].initial['count'], 'batch': str(page.context['form'].initial['batch']),
        }
        response = self.client.post(self.url + '?lista=artworks', payload)
        self.assertContains(response, 'Te mandamos una prueba a bulk-estafa@example.com.')
        self.assertContains(response, 'value="Abrieron las becas"')
        self.assertContains(response, 'Hola a todos.')
        self.assertNotContains(response, 'Confirmá el envío.')
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['bulk-estafa@example.com'])
        self.assertEqual(mail.outbox[0].subject, 'Abrieron las becas')
        self.assertEqual(mail.outbox[0].bcc, [])
        self.assertIn('Hola a todos.', mail.outbox[0].body)
        self.assertIn('Este email no recibe respuestas', mail.outbox[0].body)
        self.assertFalse(self.faro.history.filter(kind='message').exists())
        # La prueba no gasta el envío: el mismo lote se manda después a todas las personas.
        self.client.post(self.url + '?lista=artworks', {**payload, 'action': '', 'confirm': 'on'})
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(len(mail.outbox[1].bcc), 3)
        self.assertEqual(self.faro.history.filter(kind='message').count(), 1)
