"""Subida directa a S3 de los archivos de la propuesta.

La app corre en Lambda, que corta los pedidos de más de ~6 MB: un video de 20 MB no puede pasar
por Django. El navegador pide permiso (un POST firmado de S3 por archivo), sube cada archivo
directo al bucket y después confirma con los tokens que le dimos, y recién ahí se crean los
ArtworkFile. El token firmado ata la key a la propuesta, así nadie confirma una key ajena.

Sin S3 (en local y en los tests) no hay subida directa y el formulario sube como siempre.
"""
import mimetypes
import posixpath
import uuid

from botocore.exceptions import ClientError
from django.core import signing
from django.core.exceptions import SuspiciousFileOperation

from .models import ArtworkFile

TOKEN_SALT = 'art.artwork-file-upload'
UPLOAD_EXPIRES_SECONDS = 60 * 60
# El token tiene que durar más que el permiso de subida: confirmar llega después de subir.
TOKEN_MAX_AGE_SECONDS = 2 * UPLOAD_EXPIRES_SECONDS


class UploadNotFound(Exception):
    pass


def s3_storage():
    """El storage de ArtworkFile si es S3; None si los archivos se guardan en disco."""
    storage = ArtworkFile._meta.get_field('file').storage
    return storage if hasattr(storage, 's3_connection') else None


def _storage_name(storage, filename):
    upload_to = ArtworkFile._meta.get_field('file').upload_to
    try:
        clean_name = storage.get_valid_name(posixpath.basename(filename.replace('\\', '/')))
    except SuspiciousFileOperation:
        clean_name = ''
    if not clean_name or clean_name in {'.', '..'}:
        clean_name = 'archivo' + posixpath.splitext(filename)[1].lower()
    # Una carpeta por subida: el nombre original se mantiene y nunca pisa otro archivo.
    return posixpath.join(upload_to, uuid.uuid4().hex, clean_name[-200:])


def presign(storage, artwork, filename, size):
    """Permiso para que el navegador suba un archivo directo a S3: url, campos del POST y token."""
    name = _storage_name(storage, filename)
    content_type = mimetypes.guess_type(filename, strict=False)[0] or 'application/octet-stream'
    post = storage.s3_connection.generate_presigned_post(
        Bucket=storage.settings.AWS_S3_BUCKET_NAME,
        Key=storage._get_key_name(name),
        Fields={'Content-Type': content_type},
        Conditions=[
            {'Content-Type': content_type},
            # S3 rechaza el archivo si no pesa lo que el navegador declaró al pedir permiso.
            ['content-length-range', size, size],
        ],
        ExpiresIn=UPLOAD_EXPIRES_SECONDS,
    )
    token = signing.dumps({'artwork': artwork.pk, 'name': name, 'filename': filename[:255]}, salt=TOKEN_SALT)
    return {'url': post['url'], 'fields': post['fields'], 'token': token}


def read_token(artwork, token):
    """El archivo que el token autoriza a sumar a esta propuesta: (nombre en el storage, nombre original)."""
    data = signing.loads(token, salt=TOKEN_SALT, max_age=TOKEN_MAX_AGE_SECONDS)
    if data.get('artwork') != artwork.pk:
        raise signing.BadSignature('El token es de otra propuesta.')
    return data['name'], data['filename']


def ensure_uploaded(storage, name):
    """Falla si el archivo no llegó a S3."""
    try:
        storage.s3_connection.head_object(
            Bucket=storage.settings.AWS_S3_BUCKET_NAME, Key=storage._get_key_name(name),
        )
    except ClientError as error:
        raise UploadNotFound(name) from error
