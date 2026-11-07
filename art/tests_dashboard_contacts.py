import csv
import io

from django.contrib.auth.models import User
from django.db import connection
from django.test import SimpleTestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from user_profile.phone import display_phone, whatsapp_number

from .models import Artwork
from .tests_mad import MadMobilityTest


class WhatsappNumberTest(SimpleTestCase):
    def test_normalizes_to_international_digits(self):
        cases = {
            '2215551234': '5492215551234',
            '02214551234': '5492214551234',
            '15 5555-1234': '5491155551234',
            '011 15 5555-1234': '5491155551234',
            '0221 15 455-1234': '5492214551234',
            '+54 9 11 5555 1234': '5491155551234',
            '+541131708864': '5491131708864',
            '5491155551234': '5491155551234',
            '+34 600 000 000': '34600000000',
            '+1 737 529 5031': '17375295031',
            '': '',
            None: '',
        }
        for phone, expected in cases.items():
            with self.subTest(phone=phone):
                self.assertEqual(whatsapp_number(phone), expected)

    def test_readable_form(self):
        self.assertEqual(display_phone('+541131708864'), '+54 9 11 3170-8864')
        self.assertEqual(display_phone('02214551234'), '+54 9 221 455-1234')
        self.assertEqual(display_phone('+543525419619'), '+54 9 3525 41-9619')
        self.assertEqual(display_phone('+34 600 000 000'), '+34600000000')
        self.assertEqual(display_phone(''), '')


class DashboardContactsTest(MadMobilityTest):
    # Reusa el setup (evento, programa, persona de ESTAFA); no vuelve a correr los tests de MAD.
    test_edit_page_shows_both_toggles_in_the_mad_section = None
    test_saving_stores_the_answers = None
    test_answers_are_optional = None
    test_estafa_export_includes_the_mad_columns = None

    def dashboard(self):
        self.client.force_login(self.admin)
        return self.client.get(reverse('art_admin_dashboard', args=[self.event.slug]))

    def test_contact_view_shows_the_leader_email_and_whatsapp(self):
        self.owner.first_name, self.owner.last_name = 'Ana', 'Artista'
        self.owner.save()
        self.owner.profile.nickname = 'Anita'
        self.owner.profile.save()
        response = self.dashboard()
        self.assertContains(response, 'id="art-view-proposal" value="proposal" autocomplete="off" aria-controls="art-table-wrap" checked')
        self.assertContains(response, '<label class="btn btn-outline-primary" for="art-view-contact">Contacto</label>', html=True)
        for header in ('Responsable', 'Email', 'WhatsApp'):
            self.assertContains(response, f'<th class="art-contact-col">{header}</th>', html=True)
        self.assertContains(response, '<td class="art-contact-col art-col-person">Anita (Ana Artista)</td>', html=True)
        self.assertContains(response, 'href="mailto:artista@example.com"')
        self.assertContains(response, 'href="https://web.whatsapp.com/send?phone=5491100000001"')
        self.assertContains(response, 'target="_blank" rel="noopener"')
        self.assertContains(response, 'aria-label="Escribir por WhatsApp a Anita"')
        self.assertContains(response, '+54 9 11 0000-0001')
        self.assertNotContains(response, 'text=')

    def test_proposal_columns_stay_for_the_default_view(self):
        response = self.dashboard()
        for header in ('Estado', 'Contacto', 'Declaración'):
            self.assertContains(response, f'<th class="art-proposal-col">{header}</th>', html=True)

    def test_export_always_includes_the_leader_contact(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('art_admin_export', args=[self.event.slug]))
        rows = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'))))
        row = dict(zip(rows[0], rows[1]))
        self.assertEqual(row['Responsable'], 'artista@example.com')
        self.assertEqual(row['WhatsApp del responsable'], '5491100000001')
        self.assertIn('Estado', rows[0])

    def test_missing_phone_shows_a_dash(self):
        self.owner.profile.phone = ''
        self.owner.profile.save()
        response = self.dashboard()
        self.assertNotContains(response, 'web.whatsapp.com')
        self.assertContains(response, '<span class="visually-hidden">Sin teléfono</span>', html=False)

    def test_no_extra_queries_per_artwork(self):
        before = self._queries()
        for index in range(3):
            user = User.objects.create_user(username=f'artista{index}', email=f'artista{index}@example.com')
            user.profile.phone = f'+5422100000{index:02d}'
            user.profile.save()
            Artwork.objects.create(event=self.event, owner=user, title=f'Obra {index}')
        self.assertEqual(self._queries(), before)

    def _queries(self):
        self.client.force_login(self.admin)
        with CaptureQueriesContext(connection) as ctx:
            self.client.get(reverse('art_admin_dashboard', args=[self.event.slug]))
        return len(ctx.captured_queries)

    def test_dashboard_is_estafa_only(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse('art_admin_dashboard', args=[self.event.slug]))
        self.assertEqual(response.status_code, 403)
