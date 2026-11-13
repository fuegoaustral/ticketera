from django.urls import reverse

from .models import Artwork
from .tests_mad import MadMobilityTest

FLAGS = {
    'wants_support_camp': 'Con camp soporte',
    'grant_requested': 'Pidió beca',
    'burns': 'Se quema',
    'uses_sound': 'Tiene sonido',
    'needs_mad_assistance': 'Necesita asistencia de MAD',
    'is_movable': 'Se mueve de lugar (sin motor)',
    'has_own_mobility': 'Movilidad propia (art car)',
}


class DashboardFlagIconsTest(MadMobilityTest):
    # Reusa el setup (evento, programa, persona de ESTAFA); no vuelve a correr los tests de MAD.
    test_edit_page_shows_both_toggles_in_the_mad_section = None
    test_saving_stores_the_answers = None
    test_answers_are_optional = None
    test_estafa_export_includes_the_mad_columns = None

    def dashboard(self):
        self.client.force_login(self.admin)
        return self.client.get(reverse('art_admin_dashboard', args=[self.event.slug]))

    def test_each_icon_shows_only_when_its_flag_is_on(self):
        for field, tooltip in FLAGS.items():
            with self.subTest(field=field):
                Artwork.objects.filter(pk=self.artwork.pk).update(**{name: name == field for name in FLAGS})
                response = self.dashboard()
                self.assertContains(response, f'title="{tooltip}"', count=1)
                self.assertContains(response, f'<span class="visually-hidden">{tooltip}</span>', count=1, html=False)
                for other in set(FLAGS.values()) - {tooltip}:
                    self.assertNotContains(response, f'title="{other}"')

    def test_grant_status_alone_also_flags_the_grant(self):
        Artwork.objects.filter(pk=self.artwork.pk).update(grant_status=Artwork.GrantStatus.APPROVED)
        self.assertContains(self.dashboard(), 'title="Pidió beca"', count=1)

    def test_icons_follow_the_agreed_order(self):
        Artwork.objects.filter(pk=self.artwork.pk).update(**{name: True for name in FLAGS})
        content = self.dashboard().content.decode()
        positions = [content.index(f'title="{tooltip}"') for tooltip in FLAGS.values()]
        self.assertEqual(positions, sorted(positions))

    def test_art_car_is_a_flying_saucer_not_a_bus(self):
        Artwork.objects.filter(pk=self.artwork.pk).update(has_own_mobility=True)
        response = self.dashboard()
        self.assertNotContains(response, 'fa-bus')
        self.assertContains(response, 'art-flag-ufo')
        self.assertContains(response, 'viewBox="0 0 640 512"')  # el OVNI usa la grilla de la pickup

    def test_artwork_without_flags_has_no_icons(self):
        response = self.dashboard()
        self.assertContains(response, 'Riesgo, camp soporte, beca y coordinación con otros equipos')
        self.assertNotContains(response, 'class="art-flag ')
