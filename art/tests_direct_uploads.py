from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError
from django.core import signing
from django.core.files.storage import default_storage
from django.test import TestCase
from django.urls import reverse

from . import direct_uploads
from .models import Artwork
from .tests import ArtworkFlowTest


class FakeS3Storage:
    """Lo que la subida directa usa de django_s3_storage, sin hablar con AWS."""

    def __init__(self):
        self.settings = SimpleNamespace(AWS_S3_BUCKET_NAME='bucket')
        self.s3_connection = MagicMock()
        self.s3_connection.generate_presigned_post.side_effect = lambda **kwargs: {
            'url': 'https://bucket.s3.amazonaws.com/', 'fields': {'key': kwargs['Key'], **kwargs['Fields']},
        }

    def get_valid_name(self, name):
        return default_storage.get_valid_name(name)

    def _get_key_name(self, name):
        return f'private/{name}'


class DirectUploadTest(TestCase):
    """Los archivos de la propuesta van directo a S3: Lambda no recibe pedidos de más de ~6 MB."""

    def setUp(self):
        ArtworkFlowTest.setUp(self)
        self.storage = FakeS3Storage()
        patcher = patch.object(direct_uploads, 's3_storage', return_value=self.storage)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.artwork = Artwork.objects.create(event=self.event, owner=self.owner, title='Faro')
        self.presign_url = reverse('artwork_file_presign', args=[self.artwork.pk])
        self.confirm_url = reverse('artwork_file_confirm', args=[self.artwork.pk])
        self.client.force_login(self.owner)

    def presign(self, files):
        return self.client.post(self.presign_url, {
            'name': [name for name, _ in files], 'size': [size for _, size in files],
        })

    def test_form_uploads_directly_when_storage_is_s3(self):
        response = self.client.get(reverse('artwork_edit', args=[self.artwork.pk]))
        self.assertContains(response, f'data-presign-url="{self.presign_url}"')
        self.assertContains(response, f'data-confirm-url="{self.confirm_url}"')

    def test_video_of_20_mb_goes_to_s3_and_is_confirmed(self):
        size = 20 * 1024 * 1024
        response = self.presign([('Recorrido final.mov', size), ('plano.pdf', 1000)])
        self.assertEqual(response.status_code, 200)
        uploads = response.json()['uploads']
        self.assertEqual(len(uploads), 2)

        video_call = self.storage.s3_connection.generate_presigned_post.call_args_list[0].kwargs
        self.assertRegex(video_call['Key'], r'^private/art/files/[0-9a-f]{32}/Recorrido_final\.mov$')
        self.assertEqual(video_call['Fields'], {'Content-Type': 'video/quicktime'})
        self.assertIn(['content-length-range', size, size], video_call['Conditions'])

        response = self.client.post(self.confirm_url, {'token': [upload['token'] for upload in uploads]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['redirect'], f"{reverse('artwork_edit', args=[self.artwork.pk])}#detalles")
        files = {f.name: f for f in self.artwork.files.all()}
        self.assertEqual(set(files), {'Recorrido final.mov', 'plano.pdf'})
        self.assertTrue(files['Recorrido final.mov'].is_video)
        self.assertEqual(f"private/{files['Recorrido final.mov'].file.name}", video_call['Key'])
        self.assertEqual(files['plano.pdf'].uploaded_by, self.owner)

        # Reintentar la confirmación no duplica los archivos.
        response = self.client.post(self.confirm_url, {'token': [upload['token'] for upload in uploads]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.artwork.files.count(), 2)

    def test_presign_rejects_what_the_form_rejects(self):
        for files, error in (
            ([('notas.docx', 10)], 'no es una imagen, un PDF ni un video'),
            ([('largo.mp4', 20 * 1024 * 1024 + 1)], 'pesa más de 20 MB'),
            ([('vacio.png', 0)], 'está vacío'),
            ([(f'foto{i}.jpg', 10) for i in range(11)], 'hasta 10 archivos por vez'),
        ):
            response = self.presign(files)
            self.assertEqual(response.status_code, 400)
            self.assertIn(error, response.json()['error'])
        self.storage.s3_connection.generate_presigned_post.assert_not_called()

    def test_confirm_rejects_tokens_of_other_artworks_and_missing_uploads(self):
        other = Artwork.objects.create(event=self.event, owner=self.owner, title='Otra')
        token = direct_uploads.presign(self.storage, other, 'boceto.png', 10)['token']
        response = self.client.post(self.confirm_url, {'token': token})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.artwork.files.count(), 0)

        response = self.client.post(self.confirm_url, {'token': signing.dumps({'artwork': self.artwork.pk}, salt='otra')})
        self.assertEqual(response.status_code, 400)

        self.storage.s3_connection.head_object.side_effect = ClientError({'Error': {'Code': '404'}}, 'HeadObject')
        token = direct_uploads.presign(self.storage, self.artwork, 'boceto.png', 10)['token']
        response = self.client.post(self.confirm_url, {'token': token})
        self.assertEqual(response.status_code, 400)
        self.assertIn('no terminó de subirse', response.json()['error'])
        self.assertEqual(self.artwork.files.count(), 0)

    def test_only_people_who_can_edit_files_get_upload_permission(self):
        self.client.force_login(self.stranger)
        self.assertEqual(self.presign([('boceto.png', 10)]).status_code, 404)
        token = direct_uploads.presign(self.storage, self.artwork, 'boceto.png', 10)['token']
        self.assertEqual(self.client.post(self.confirm_url, {'token': token}).status_code, 404)
        self.assertEqual(self.artwork.files.count(), 0)

    def test_without_s3_the_form_uploads_through_django(self):
        with patch.object(direct_uploads, 's3_storage', return_value=None):
            response = self.client.get(reverse('artwork_edit', args=[self.artwork.pk]))
            self.assertNotContains(response, 'data-presign-url="')
            self.assertEqual(self.presign([('boceto.png', 10)]).status_code, 404)
