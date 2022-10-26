from deprepagos.settings import *

# CSRF_TRUSTED_ORIGINS = ['bonos.fuegoaustral.org']

STATICFILES_STORAGE = 'deprepagos.storages.S3PipelineManifestStorage'
AWS_STORAGE_BUCKET_NAME = 'faticketera-zappa-dev' 

# EMAIL_HOST = os.environ.get('EMAIL_HOST')
# EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER')
# EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD')
# EMAIL_PORT = os.environ.get('EMAIL_PORT')
# EMAIL_USE_TLS = True
# DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', DEFAULT_FROM_EMAIL)

# MERCADOPAGO = {
#     'PUBLIC_KEY': os.environ.get('MERCADOPAGO_PUBLIC_KEY'),
#     'ACCESS_TOKEN': os.environ.get('MERCADOPAGO_ACCESS_TOKEN'),
# }