from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Permission, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from events.models import Event
from tickets.models import NewTicket, Order, TicketType
from teams.models import Team, TeamMembership
from user_profile.models import Profile


class AccountMenuTest(TestCase):
    """The "Mi cuenta" dropdown in the top nav (tickets/barbu_base.html)."""

    def setUp(self):
        now = timezone.now()
        Event.objects.filter(is_main=True).update(is_main=False)
        self.event = Event.objects.create(
            name='FA Carnaval', slug='fa-carnaval', active=True, is_main=True,
            start=now + timedelta(days=20), end=now + timedelta(days=24),
            transfers_enabled_until=now + timedelta(days=10), header_image='events/heros/no-image.jpg', title='FA', description='Evento',
        )
        self.user = User.objects.create_user(
            username='ana', email='ana@example.com', first_name='Ana', last_name='Artista',
        )
        self.user.profile.document_number = '10000001'
        self.user.profile.phone = '+5491100000001'
        self.user.profile.profile_completion = Profile.COMPLETE
        self.user.profile.save()
        self.client.force_login(self.user)

    def menu(self):
        response = self.client.get(reverse('art_dashboard'))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        start = html.index('class="dropdown-menu dropdown-menu-end account-menu"')
        return html[start:html.index('Cerrar sesión', start)]

    def test_participant_sees_own_sections_and_no_administration(self):
        menu = self.menu()
        self.assertIn('Ana Artista', menu)
        self.assertIn('ana@example.com', menu)
        self.assertIn(reverse('art_dashboard'), menu)
        self.assertIn('Mis bonos y eventos', menu)
        self.assertIn('https://fuegoaustral.org/faq/', menu)
        self.assertNotIn('target="_blank"', menu)
        self.assertIn(reverse('account_logout'), menu)
        for name in ('my_events', 'estafa_home', 'scanner_events', 'caja_events', 'admin_logros'):
            self.assertNotIn(reverse(name), menu)
        self.assertNotIn(reverse('la_sede'), menu)
        self.assertNotIn('text-success', menu)
        self.assertNotIn('text-danger', menu)

    def test_current_section_is_marked(self):
        menu = self.menu()
        self.assertIn(f'href="{reverse("art_dashboard")}" aria-current="page"', menu)
        self.assertEqual(menu.count('aria-current="page"'), 1)

    def test_event_admin_sees_events_scanner_and_cajas(self):
        self.event.admins.add(self.user)
        menu = self.menu()
        for name in ('my_events', 'scanner_events', 'caja_events'):
            self.assertIn(reverse(name), menu)
        self.assertNotIn(reverse('admin_logros'), menu)
        self.assertNotIn(reverse('estafa_home'), menu)

    def test_estafa_member_sees_estafa_only_while_active(self):
        estafa = Team.objects.get(slug='estafa')
        today = timezone.localdate()
        membership = TeamMembership.objects.create(team=estafa, user=self.user, started_on=today - timedelta(days=30))
        menu = self.menu()
        self.assertIn(reverse('estafa_home'), menu)
        self.assertNotIn(reverse('my_events'), menu)
        membership.ended_on = today - timedelta(days=1)
        membership.save()
        self.assertNotIn(reverse('estafa_home'), self.menu())

    def test_scanner_role_sees_only_scanner(self):
        self.event.access_scanner.add(self.user)
        menu = self.menu()
        self.assertIn(reverse('scanner_events'), menu)
        self.assertNotIn(reverse('my_events'), menu)
        self.assertNotIn(reverse('caja_events'), menu)

    def test_figuritas_permission_sees_figuritas_admin(self):
        self.user.user_permissions.add(Permission.objects.get(codename='manage_achievements'))
        menu = self.menu()
        self.assertIn(reverse('admin_logros'), menu)
        self.assertNotIn(reverse('admin_logros_assign'), menu)
        self.assertNotIn(reverse('my_events'), menu)



class FreeTicketCheckoutTest(TestCase):
    """Bonos de monto libre (price=0), incluido comprarlos a $0."""

    def setUp(self):
        now = timezone.now()
        Event.objects.filter(is_main=True).update(is_main=False)
        self.event = Event.objects.create(
            name='Gualicho', slug='gualicho', active=True, is_main=True,
            start=now + timedelta(days=20), end=now + timedelta(days=24),
            transfers_enabled_until=now + timedelta(days=10), header_image='events/heros/no-image.jpg',
            title='Gualicho', description='Evento', max_tickets=100,
        )
        self.ticket_type = TicketType.objects.create(
            event=self.event, name='Bono libre', price=0, ticket_count=50,
        )
        self.user = User.objects.create_user(
            username='ana', email='ana@example.com', first_name='Ana', last_name='Artista',
        )
        self.user.profile.document_number = '10000001'
        self.user.profile.phone = '+5491100000001'
        self.user.profile.profile_completion = Profile.COMPLETE
        self.user.profile.save()
        self.client.force_login(self.user)

    def checkout(self, custom_amount):
        url = reverse('select_tickets') + '?event=gualicho'
        self.client.get(url)
        response = self.client.post(url, {
            f'ticket_{self.ticket_type.id}_quantity': 1,
            f'ticket_{self.ticket_type.id}_custom_amount': custom_amount,
        })
        self.assertEqual(response.status_code, 302)
        self.client.post(reverse('select_donations') + '?event=gualicho', {
            'donation_art': 0, 'donation_venue': 0, 'donation_grant': 0,
        })
        return self.client.post(reverse('order_summary') + '?event=gualicho', {})

    @patch('tickets.views.checkout.mercadopago.SDK')
    def test_zero_amount_confirms_without_mercadopago(self, sdk):
        response = self.checkout('0')

        order = Order.objects.get(user=self.user)
        self.assertRedirects(
            response, reverse('checkout_payment_callback', kwargs={'order_key': order.key}),
            fetch_redirect_response=False,
        )
        sdk.assert_not_called()
        self.assertEqual(order.status, Order.OrderStatus.CONFIRMED)
        self.assertEqual(NewTicket.objects.filter(order=order).count(), 1)

    @patch('tickets.views.checkout.mercadopago.SDK')
    def test_custom_amount_goes_to_mercadopago(self, sdk):
        sdk.return_value.preference.return_value.create.return_value = {
            'response': {'init_point': 'https://mp.example/pay'},
        }
        response = self.checkout('1500.50')

        self.assertRedirects(response, 'https://mp.example/pay', fetch_redirect_response=False)
        order = Order.objects.get(user=self.user)
        self.assertEqual(order.amount, Decimal('1500.50'))
        self.assertEqual(order.status, Order.OrderStatus.PENDING)

    @patch('tickets.views.checkout.mercadopago.SDK')
    def test_blank_price_ticket_is_free(self, sdk):
        self.ticket_type.price = None
        self.ticket_type.save()

        response = self.checkout('')

        order = Order.objects.get(user=self.user)
        self.assertRedirects(
            response, reverse('checkout_payment_callback', kwargs={'order_key': order.key}),
            fetch_redirect_response=False,
        )
        sdk.assert_not_called()
        self.assertEqual(order.amount, 0)
        self.assertEqual(order.status, Order.OrderStatus.CONFIRMED)


class CurrentEventsTest(TestCase):
    def test_ended_events_are_not_current(self):
        now = timezone.now()
        common = dict(active=True, transfers_enabled_until=now, header_image='events/heros/no-image.jpg',
                      title='E', description='E')
        upcoming = Event.objects.create(name='Gualicho', slug='gualicho', start=now + timedelta(days=1),
                                        end=now + timedelta(days=2), **common)
        Event.objects.create(name='Abracadabra', slug='abracadabra', start=now - timedelta(days=5),
                             end=now - timedelta(days=4), **common)

        self.assertEqual(list(Event.get_current_events()), [upcoming])


class TicketTypeCajaProductTest(TestCase):
    """Creating a ticket type auto-creates its caja EventProduct (stock ledger)."""

    def setUp(self):
        now = timezone.now()
        self.event = Event.objects.create(
            name='Gualicho', slug='gualicho', active=True,
            start=now + timedelta(days=20), end=now + timedelta(days=24),
            transfers_enabled_until=now + timedelta(days=10), header_image='events/heros/no-image.jpg',
            title='Gualicho', description='Evento', max_tickets=100,
        )

    def test_direct_type_hidden_from_caja_with_unlimited_count(self):
        from caja.stock import available_for_ticket_type

        ticket_type = TicketType.objects.create(
            event=self.event, name='Bono dirigido', price=200000,
            is_direct_type=True, show_in_caja=False, ticket_count=None,
        )
        self.assertTrue(hasattr(ticket_type, 'event_product'))
        self.assertIsNone(available_for_ticket_type(ticket_type))

    def test_limited_count_is_recorded(self):
        from caja.models import EventProductStockRecord

        ticket_type = TicketType.objects.create(
            event=self.event, name='General', price=1000, ticket_count=10,
        )
        record = EventProductStockRecord.objects.get(event_product=ticket_type.event_product)
        self.assertEqual(record.delta, 10)


class DirectTicketTemplateTest(TestCase):
    """Bonos dirigidos: solo para cuentas existentes, asignados con un clic."""

    def setUp(self):
        from allauth.account.models import EmailAddress
        from tickets.models import DirectTicketTemplate

        self.DirectTicketTemplate = DirectTicketTemplate
        now = timezone.now()
        Event.objects.update(is_main=False, active=False)
        self.event = Event.objects.create(
            name='Gualicho', slug='gualicho', active=True, is_main=True,
            start=now + timedelta(days=20), end=now + timedelta(days=24),
            transfers_enabled_until=now + timedelta(days=10), header_image='events/heros/no-image.jpg',
            title='Gualicho', description='Evento', max_tickets=100,
        )
        self.ticket_type = TicketType.objects.create(
            event=self.event, name='Bono dirigido', price=0,
            is_direct_type=True, show_in_caja=False, ticket_count=None,
        )
        self.laura = User.objects.create_user(
            username='laura', email='laura@hotmail.com', first_name='Laura', last_name='Ch',
        )
        self.laura.profile.document_number = '10000002'
        self.laura.profile.phone = '+5491100000002'
        self.laura.profile.profile_completion = Profile.COMPLETE
        self.laura.profile.save()
        EmailAddress.objects.create(user=self.laura, email='laura@gmail.com', verified=True, primary=False)

        self.staff = User.objects.create_user(username='staff', email='staff@example.com', is_staff=True)
        self.staff.user_permissions.add(Permission.objects.get(codename='can_sell_tickets'))
        self.client.force_login(self.staff)

    def template(self, email, amount=3):
        return self.DirectTicketTemplate.objects.create(
            origin='CAMP', name=f'CAMP - Test - {email}', amount=amount, email=email, event=self.event,
        )

    def test_clean_rejects_email_without_account(self):
        from django.core.exceptions import ValidationError

        template = self.DirectTicketTemplate(origin='CAMP', name='x', amount=1, email='nadie@example.com', event=self.event)
        with self.assertRaises(ValidationError) as ctx:
            template.full_clean()
        self.assertIn('email', ctx.exception.message_dict)

    def test_clean_rejects_blank_email(self):
        from django.core.exceptions import ValidationError

        template = self.DirectTicketTemplate(origin='CAMP', name='x', amount=1, email='', event=self.event)
        with self.assertRaises(ValidationError):
            template.full_clean()

    def test_clean_accepts_primary_and_secondary_email(self):
        for email in ('Laura@Hotmail.com', 'laura@gmail.com'):
            template = self.DirectTicketTemplate(origin='CAMP', name='x', amount=1, email=email, event=self.event)
            template.full_clean()
            self.assertEqual(template.get_user(), self.laura)

    def test_import_rejects_rows_without_account(self):
        import tablib
        from tickets.admin import DirectTicketTemplateImportResource

        dataset = tablib.Dataset(headers=['origin', 'name', 'amount', 'email'])
        dataset.append(['CAMP', 'CAMP - Ok', 2, 'laura@gmail.com'])
        dataset.append(['ARTE', 'ARTE - Sin cuenta', 1, 'nadie@example.com'])
        result = DirectTicketTemplateImportResource(event=self.event).import_data(dataset, dry_run=False)
        self.assertTrue(result.has_validation_errors())
        self.assertEqual([row.number for row in result.invalid_rows], [2])
        self.assertFalse(self.DirectTicketTemplate.objects.filter(name='ARTE - Sin cuenta').exists())

    def test_import_sets_event(self):
        import tablib
        from tickets.admin import DirectTicketTemplateImportResource

        dataset = tablib.Dataset(headers=['origin', 'name', 'amount', 'email'])
        dataset.append(['VOLUNTARIOS', 'Vol - Ranger - Laura', 1, 'laura@hotmail.com'])
        result = DirectTicketTemplateImportResource(event=self.event).import_data(dataset, dry_run=False)
        self.assertFalse(result.has_errors() or result.has_validation_errors())
        self.assertEqual(self.DirectTicketTemplate.objects.get(name='Vol - Ranger - Laura').event, self.event)

    @patch('utils.direct_sales.send_mail')
    def test_assign_by_click_via_secondary_email(self, _send_mail):
        template = self.template('laura@gmail.com', amount=3)
        response = self.client.post(reverse('admin_direct_tickets_view'), {'action': 'assign', 'template_id': template.id, 'quantity': template.amount})
        template.refresh_from_db()
        self.assertRedirects(response, reverse('admin_direct_tickets_congrats_view', args=[template.order_id]),
                             fetch_redirect_response=False)
        self.assertEqual(template.status, 'ASSIGNED')
        self.assertEqual(template.amount_used, 3)
        self.assertEqual(template.order.user, self.laura)
        self.assertEqual(template.order.order_type, Order.OrderType.OTHER)
        self.assertEqual(NewTicket.objects.filter(order=template.order, holder=self.laura).count(), 3)

    def assign(self, template, quantity):
        return self.client.post(reverse('admin_direct_tickets_view'),
                                {'action': 'assign', 'template_id': template.id, 'quantity': quantity})

    @patch('utils.direct_sales.send_mail')
    def test_assign_twice_does_not_duplicate(self, _send_mail):
        template = self.template('laura@hotmail.com', amount=2)
        self.assign(template, 2)
        self.assign(template, 2)
        self.assertEqual(NewTicket.objects.filter(holder=self.laura).count(), 2)

    @patch('utils.direct_sales.send_mail')
    def test_partial_assignments_accumulate(self, _send_mail):
        template = self.template('laura@hotmail.com', amount=15)
        self.assign(template, 5)
        template.refresh_from_db()
        self.assertEqual(template.amount_used, 5)
        self.assign(template, 3)
        template.refresh_from_db()
        self.assertEqual(template.amount_used, 8)
        self.assertEqual(NewTicket.objects.filter(holder=self.laura).count(), 8)
        html = self.client.get(reverse('admin_direct_tickets_view')).content.decode()
        self.assertIn('max="7" value="7"', html)

    @patch('utils.direct_sales.send_mail')
    def test_assign_more_than_available_is_rejected(self, _send_mail):
        template = self.template('laura@hotmail.com', amount=4)
        self.assign(template, 3)
        for quantity in (2, 0, -1, 'abc'):
            self.assign(template, quantity)
        template.refresh_from_db()
        self.assertEqual(template.amount_used, 3)
        self.assign(template, 1)
        template.refresh_from_db()
        self.assertEqual(template.amount_used, 4)
        self.assertEqual(NewTicket.objects.filter(holder=self.laura).count(), 4)
        html = self.client.get(reverse('admin_direct_tickets_view')).content.decode()
        self.assertNotIn('btn-assign', html.split('<tbody', 1)[1].split('</tbody>', 1)[0])

    @patch('utils.direct_sales.send_mail')
    def test_assign_without_account_is_blocked(self, _send_mail):
        template = self.DirectTicketTemplate.objects.create(  # dirigido viejo, creado antes de la validación
            origin='ARTE', name='ARTE - Viejo', amount=1, email='nadie@example.com', event=self.event,
        )
        response = self.client.post(reverse('admin_direct_tickets_view'), {'action': 'assign', 'template_id': template.id, 'quantity': template.amount})
        self.assertRedirects(response, reverse('admin_direct_tickets_view'), fetch_redirect_response=False)
        template.refresh_from_db()
        self.assertEqual(template.status, 'AVAILABLE')
        self.assertFalse(Order.objects.filter(email='nadie@example.com').exists())
        _send_mail.assert_not_called()

    @patch('utils.direct_sales.send_mail')
    def test_assign_with_incomplete_profile_is_blocked(self, _send_mail):
        self.laura.profile.profile_completion = Profile.NONE
        self.laura.profile.save()
        template = self.template('laura@hotmail.com')
        self.client.post(reverse('admin_direct_tickets_view'), {'action': 'assign', 'template_id': template.id, 'quantity': template.amount})
        template.refresh_from_db()
        self.assertEqual(template.status, 'AVAILABLE')

    def test_list_shows_account_status_and_assign_button(self):
        self.template('laura@gmail.com')
        self.DirectTicketTemplate.objects.create(
            origin='ARTE', name='ARTE - Viejo', amount=1, email='nadie@example.com', event=self.event,
        )
        html = self.client.get(reverse('admin_direct_tickets_view')).content.decode()
        self.assertIn('Laura Ch', html)
        self.assertIn('Sin cuenta', html)
        self.assertEqual(html.count('btn-assign"'), 2)
        self.assertNotIn('name="user_email"', html)
        self.assertIn("document.documentElement.dataset.theme = 'light'", html)

    @patch('utils.direct_sales.send_mail')
    def test_partial_assignments_keep_every_order_and_show_tickets(self, _send_mail):
        template = self.template('laura@hotmail.com', amount=5)
        self.assign(template, 2)
        self.assign(template, 1)
        orders = list(template.orders.order_by('created_at'))
        self.assertEqual(len(orders), 2)
        self.assertEqual([o.new_tickets.count() for o in orders], [2, 1])
        html = self.client.get(reverse('admin_direct_tickets_view')).content.decode()
        self.assertIn('Ver (3)', html)
        for order in orders:
            self.assertIn(reverse('admin:tickets_order_change', args=[order.id]), html)
            for ticket in order.new_tickets.all():
                self.assertIn(reverse('admin:tickets_newticket_change', args=[ticket.id]), html)
        self.assertIn('<strong>Asignados:</strong> 3', html)

    @patch('utils.direct_sales.send_mail')
    def test_order_admin_lists_filters_and_shows_direct_tickets(self, _send_mail):
        superuser = User.objects.create_superuser(username='root', email='root@example.com', password='x')
        self.client.force_login(superuser)
        template = self.template('laura@hotmail.com', amount=2)
        self.assign(template, 2)
        order = template.orders.get()
        other_event = Event.objects.create(
            name='Otro', slug='otro', start=timezone.now() - timedelta(days=400),
            end=timezone.now() - timedelta(days=396), transfers_enabled_until=timezone.now() - timedelta(days=410),
            header_image='events/heros/no-image.jpg',
            title='Otro', description='x', max_tickets=10,
        )
        other = Order.objects.create(first_name='Otra', last_name='Persona', email='otra@example.com', phone='1',
                                     dni='1', amount=0, event=other_event)

        changelist = reverse('admin:tickets_order_changelist')
        html = self.client.get(changelist).content.decode()
        self.assertIn('2 × Bono dirigido', html)
        self.assertIn('otra@example.com', html)

        for params in ({'event': self.event.id}, {'event': self.event.id, 'ticket_type': self.ticket_type.id},
                       {'origen': 'dirigido'}):
            response = self.client.get(changelist, params)
            self.assertEqual(response.status_code, 200)
            self.assertEqual([o.id for o in response.context['cl'].result_list], [order.id], params)
        response = self.client.get(changelist, {'origen': 'online'})
        self.assertEqual([o.id for o in response.context['cl'].result_list], [other.id])

        html = self.client.get(reverse('admin:tickets_order_change', args=[order.id])).content.decode()
        self.assertIn(template.name, html)
        self.assertIn('Bonos emitidos', html)


class AdminCajaTest(TestCase):
    """/admin/caja/: búsqueda de cuentas y emisión a la cuenta elegida."""

    def setUp(self):
        from allauth.account.models import EmailAddress

        now = timezone.now()
        Event.objects.update(is_main=False, active=False)
        self.event = Event.objects.create(
            name='Gualicho', slug='gualicho', active=True, is_main=True,
            start=now + timedelta(days=20), end=now + timedelta(days=24),
            transfers_enabled_until=now + timedelta(days=10), header_image='events/heros/no-image.jpg',
            title='Gualicho', description='Evento', max_tickets=100,
        )
        self.ticket_type = TicketType.objects.create(event=self.event, name='General', price=1000, ticket_count=None)
        self.laura = User.objects.create_user(
            username='laura', email='laura@hotmail.com', first_name='Laura', last_name='Chávez',
        )
        self.laura.profile.document_number = '30.456.789'
        self.laura.profile.phone = '+5491155554444'
        self.laura.profile.nickname = 'Lau'
        self.laura.profile.profile_completion = Profile.COMPLETE
        self.laura.profile.save()
        EmailAddress.objects.create(user=self.laura, email='laura@hotmail.com', verified=True, primary=True)
        EmailAddress.objects.create(user=self.laura, email='lau.secundario@gmail.com', verified=True, primary=False)
        self.pedro = User.objects.create_user(username='pedro', email='pedro@example.com', first_name='Pedro', last_name='Laurenti')

        self.staff = User.objects.create_user(username='staff', email='staff@example.com', is_staff=True)
        self.staff.user_permissions.add(Permission.objects.get(codename='can_sell_tickets'))
        self.client.force_login(self.staff)

    def search(self, q):
        resp = self.client.get(reverse('admin_caja_user_search'), {'q': q, 'event': self.event.id})
        self.assertEqual(resp.status_code, 200)
        return resp.json()['results']

    def test_search_by_each_field(self):
        for q in ['laura', 'CHÁVEZ', 'lau.secundario', 'hotmail', '30456789', '30.456', '5555444', 'Lau']:
            ids = [r['id'] for r in self.search(q)]
            self.assertIn(self.laura.id, ids, q)

    def test_search_combines_terms(self):
        self.assertEqual({r['id'] for r in self.search('lau')}, {self.laura.id, self.pedro.id})
        self.assertEqual([r['id'] for r in self.search('lau 30456')], [self.laura.id])

    def test_search_reports_profile_and_tickets(self):
        order = Order.objects.create(
            first_name='Laura', last_name='Chávez', email='laura@hotmail.com', phone='1', dni='1',
            amount=0, status=Order.OrderStatus.CONFIRMED, event=self.event, user=self.laura,
        )
        NewTicket.objects.create(event=self.event, ticket_type=self.ticket_type, order=order, owner=self.laura, holder=self.laura)
        results = {r['id']: r for r in self.search('lau')}
        self.assertTrue(results[self.laura.id]['profile_complete'])
        self.assertEqual(results[self.laura.id]['tickets_in_event'], 1)
        self.assertIn('lau.secundario@gmail.com', results[self.laura.id]['emails'])
        self.assertFalse(results[self.pedro.id]['profile_complete'])

    @patch('tickets.models.Order.send_confirmation_email')
    def test_emit_to_selected_user_does_not_create_account(self, _send):
        users_before = User.objects.count()
        resp = self.client.post(reverse('admin_caja_view'), {
            'action': 'order', 'event_id': self.event.id, 'user_id': self.laura.id,
            'order_type': Order.OrderType.CASH_ONSITE, f'ticket_quantity_{self.ticket_type.id}': 2,
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(User.objects.count(), users_before)
        order = Order.objects.get(user=self.laura)
        self.assertEqual(order.event, self.event)
        self.assertEqual(order.amount, 2000)
        self.assertEqual(NewTicket.objects.filter(order=order).count(), 2)

    def test_emit_rejects_incomplete_profile(self):
        resp = self.client.post(reverse('admin_caja_view'), {
            'action': 'order', 'event_id': self.event.id, 'user_id': self.pedro.id,
            'order_type': Order.OrderType.CASH_ONSITE, f'ticket_quantity_{self.ticket_type.id}': 1,
        })
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Order.objects.filter(user=self.pedro).exists())

    def test_new_user_with_existing_secondary_email_is_rejected(self):
        users_before = User.objects.count()
        resp = self.client.post(reverse('admin_caja_view'), {
            'action': 'order', 'event_id': self.event.id, 'email': 'lau.secundario@gmail.com',
            'first_name': 'Laura', 'last_name': 'X', 'document_type': 'DNI', 'document_number': '1',
            'phone': '+5491100000000', 'order_type': Order.OrderType.CASH_ONSITE,
            f'ticket_quantity_{self.ticket_type.id}': 1,
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Ya existe una cuenta con ese email')
        self.assertEqual(User.objects.count(), users_before)

    def test_page_renders(self):
        resp = self.client.get(reverse('admin_caja_view'), {'event': self.event.id})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'user-search')
