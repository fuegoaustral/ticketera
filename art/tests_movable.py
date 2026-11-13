import csv
import io

from django.urls import reverse

from .models import Artwork
from .tests_mad import MadMobilityTest

MOVABLE = '¿Tu instalación se mueve de lugar durante el evento?'
MOVABLE_LABEL = 'Se mueve de lugar (sin motor)'


class MovableArtworkTest(MadMobilityTest):
    # Reusa el setup (evento, programa, persona de ESTAFA); no vuelve a correr los tests de MAD.
    test_edit_page_shows_both_toggles_in_the_mad_section = None
    test_saving_stores_the_answers = None
    test_answers_are_optional = None
    test_estafa_export_includes_the_mad_columns = None

    def test_edit_page_asks_it_right_before_the_art_car_question(self):
        self.client.force_login(self.owner)
        content = self.client.get(reverse('artwork_edit', args=[self.artwork.pk])).content.decode()
        self.assertIn('name="is_movable"', content)
        self.assertIn('Sin motor: se empuja, se arrastra o se lleva entre varias personas.', content)
        self.assertLess(content.index('name="needs_mad_assistance"'), content.index('name="is_movable"'))
        self.assertLess(content.index('name="is_movable"'), content.index('name="has_own_mobility"'))
        self.assertIn(MOVABLE, content)

    def test_saving_stores_it_independently_from_the_art_car(self):
        self.client.force_login(self.owner)
        url = reverse('artwork_edit', args=[self.artwork.pk])
        response = self.client.post(url, {'title': 'Faro', 'is_movable': 'on', 'expected_version': self.artwork.version})
        self.assertEqual(response.status_code, 302)
        self.artwork.refresh_from_db()
        self.assertTrue(self.artwork.is_movable)
        self.assertFalse(self.artwork.has_own_mobility)

        response = self.client.post(url, {'title': 'Faro', 'expected_version': self.artwork.version})
        self.assertEqual(response.status_code, 302)
        self.artwork.refresh_from_db()
        self.assertFalse(self.artwork.is_movable)

    def test_it_is_optional(self):
        before = self.detalles_missing()
        self.artwork.is_movable = True
        self.artwork.save()
        self.assertEqual(self.detalles_missing(), before)

    def test_summary_shows_the_answer_before_the_art_car_row(self):
        Artwork.objects.filter(pk=self.artwork.pk).update(is_movable=True)
        self.client.force_login(self.admin)
        url = reverse('artwork_review', args=[self.event.slug, self.artwork.pk])
        content = self.client.get(url).content.decode()
        self.assertIn(MOVABLE_LABEL, content)
        row = content[content.index(MOVABLE_LABEL):content.index('Movilidad propia (art car)')]
        self.assertIn('Sí', row)
        self.assertLess(content.index(MOVABLE_LABEL), content.index('Movilidad propia (art car)'))

    def test_flag_shows_only_when_movable(self):
        self.client.force_login(self.admin)
        url = reverse('art_admin_dashboard', args=[self.event.slug])
        self.assertNotContains(self.client.get(url), 'class="art-flag art-flag-movable"')
        Artwork.objects.filter(pk=self.artwork.pk).update(is_movable=True)
        response = self.client.get(url)
        self.assertContains(response, 'class="art-flag art-flag-movable"', count=1)
        self.assertContains(response, 'fa-people-carry-box', count=2)  # en la fila y en el filtro de marcas, con el mismo dibujo
        self.assertContains(response, f'title="{MOVABLE_LABEL}"', count=1)
        self.assertNotContains(response, 'class="art-flag art-flag-ufo"')

    def test_estafa_export_includes_the_column(self):
        Artwork.objects.filter(pk=self.artwork.pk).update(is_movable=True)
        Artwork.objects.create(event=self.event, owner=self.owner, title='Barco', has_own_mobility=True)
        self.client.force_login(self.admin)
        exported = self.client.get(reverse('art_admin_export', args=[self.event.slug])).content.decode('utf-8-sig')
        reader = csv.DictReader(io.StringIO(exported))
        header = reader.fieldnames
        self.assertEqual(header.index(MOVABLE_LABEL) + 1, header.index('Movilidad propia (art car)'))
        by_title = {row['Propuesta de Arte']: row for row in reader}
        self.assertEqual((by_title['Faro'][MOVABLE_LABEL], by_title['Faro']['Movilidad propia (art car)']), ('Sí', 'No'))
        self.assertEqual((by_title['Barco'][MOVABLE_LABEL], by_title['Barco']['Movilidad propia (art car)']), ('No', 'Sí'))
