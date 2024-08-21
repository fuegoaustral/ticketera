from deprepagos.settings import *

CSRF_TRUSTED_ORIGINS = ['eventos.fuegoaustral.org']

DEFAULT_FILE_STORAGE = 'django_s3_storage.storage.S3Storage'
STATICFILES_STORAGE = 'deprepagos.storages.StaticS3PipelineManifestStorage'

AWS_STORAGE_BUCKET_NAME = 'faticketera-zappa-prod' 
AWS_QUERYSTRING_AUTH = False

AWS_S3_BUCKET_NAME_STATIC = 'faticketera-zappa-prod'
AWS_S3_BUCKET_AUTH_STATIC = False

AWS_S3_BUCKET_NAME = 'faticketera-zappa-prod'
AWS_S3_BUCKET_AUTH = False

EMAIL_HOST = os.environ.get('EMAIL_HOST')
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD')
EMAIL_PORT = os.environ.get('EMAIL_PORT')
EMAIL_USE_TLS = True
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', DEFAULT_FROM_EMAIL)

MERCADOPAGO = {
    'PUBLIC_KEY': os.environ.get('MERCADOPAGO_PUBLIC_KEY'),
    'ACCESS_TOKEN': os.environ.get('MERCADOPAGO_ACCESS_TOKEN'),
    'WEBHOOK_SECRET': os.environ.get('MERCADOPAGO_WEBHOOK_SECRET')
}
