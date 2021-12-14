from deprepagos.settings import *

STATICFILES_STORAGE = 'deprepagos.storages.S3PipelineManifestStorage'

EMAIL_HOST = os.environ.get('EMAIL_HOST')
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD')
EMAIL_PORT = os.environ.get('EMAIL_PORT')
EMAIL_USE_TLS = True

MERCADOPAGO = {
    'PUBLIC_KEY': os.environ.get('MERCADOPAGO_PUBLIC_KEY'),
    'ACCESS_TOKEN': os.environ.get('MERCADOPAGO_ACCESS_TOKEN'),
}