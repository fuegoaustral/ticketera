import re
from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from .models import (
    Artwork, ArtworkCheckoutPhoto, ArtworkFile, ArtworkGrantItem, ArtworkGrantItemPhoto, ArtworkPhoto,
)
from .tests_mad import MadMobilityTest

LIGHTBOX = '<dialog class="art-lightbox" id="art-lightbox" aria-label="Visor de fotos">'


class GalleryLightboxTest(MadMobilityTest):
    """Every art page with photo thumbnails opens them in the shared lightbox, not as the raw file."""

    # Reusa el setup (evento, programa, persona de ESTAFA); no vuelve a correr los tests de MAD.
    test_edit_page_shows_both_toggles_in_the_mad_section = None
    test_saving_stores_the_answers = None
    test_answers_are_optional = None
    test_estafa_export_includes_the_mad_columns = None

    def setUp(self):
        super().setUp()
        Artwork.objects.filter(pk=self.artwork.pk).update(
            status=Artwork.Status.ACTIVE, grant_status=Artwork.GrantStatus.APPROVED,
            grant_approved_amount_ars=Decimal('100000'),
        )
        self.artwork.refresh_from_db()
        self.program.gallery_opens = self.program.checkout_opens = timezone.now() - timedelta(days=1)
        self.program.save()
        for name in ('armado.gif', 'final.gif'):
            ArtworkPhoto.objects.create(artwork=self.artwork, image=f'art/gallery/{name}', stage=ArtworkPhoto.Stage.PROCESS, caption='Con grúa')
        ArtworkPhoto.objects.create(artwork=self.artwork, image='art/gallery/rendicion.gif', stage=ArtworkPhoto.Stage.GRANT_REPORT)
        ArtworkCheckoutPhoto.objects.create(artwork=self.artwork, image='art/checkout/limpio.gif', caption='Sin restos')
        ArtworkFile.objects.create(artwork=self.artwork, file='art/files/boceto.png', name='boceto.png')
        ArtworkFile.objects.create(artwork=self.artwork, file='art/files/plano.pdf', name='plano.pdf')
        item = ArtworkGrantItem.objects.create(
            artwork=self.artwork, phase=ArtworkGrantItem.Phase.EXPENSE, concept='Madera',
            amount=100, currency='ARS', exchange_rate=1, rate_date=timezone.localdate(),
        )
        ArtworkGrantItemPhoto.objects.create(item=item, image='art/grants/factura.gif')

    def page(self, user, name, *args):
        self.client.force_login(user)
        response = self.client.get(reverse(name, args=args))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def lightbox_links(self, html):
        return re.findall(r'<a [^>]*href="([^"]+)"[^>]*\bdata-lightbox\b[^>]*>', html)

    def assert_lightbox(self, html, *files):
        self.assertEqual(html.count(LIGHTBOX), 1)
        for label in ('aria-label="Anterior"', 'aria-label="Siguiente"', 'aria-label="Cerrar"'):
            self.assertIn(label, html)
        self.assertIn('data-lightbox-group', html)
        links = self.lightbox_links(html)
        for name in files:
            self.assertTrue(any(link.endswith(name) for link in links), f'{name} no abre en el visor: {links}')
        # El link sigue apuntando al archivo y abriéndolo en otra pestaña cuando no hay JS.
        for tag in re.findall(r'<a [^>]*\bdata-lightbox\b[^>]*>', html):
            self.assertIn('target="_blank"', tag)

    def test_owner_form_galleries_open_in_the_lightbox(self):
        html = self.page(self.owner, 'artwork_edit', self.artwork.pk)
        self.assert_lightbox(html, 'armado.gif', 'final.gif', 'limpio.gif', 'boceto.png')
        self.assertIn('data-lightbox-caption="Proceso · Con grúa"', html)
        # Los PDF siguen abriendo en otra pestaña.
        self.assertFalse(any(link.endswith('plano.pdf') for link in self.lightbox_links(html)))
        self.assertIn('plano.pdf', html)

    def test_estafa_review_galleries_open_in_the_lightbox(self):
        html = self.page(self.admin, 'artwork_review', self.event.slug, self.artwork.pk)
        self.assert_lightbox(html, 'armado.gif', 'final.gif', 'limpio.gif', 'boceto.png')

    def test_grant_item_and_report_photos_open_in_the_lightbox(self):
        html = self.page(self.owner, 'artwork_grant', self.artwork.pk)
        self.assert_lightbox(html, 'factura.gif', 'rendicion.gif')
