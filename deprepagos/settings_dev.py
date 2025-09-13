from deprepagos.settings import *  # noqa

MOCK_PHONE_VERIFICATION = True

CSRF_TRUSTED_ORIGINS = ['dev.fuegoaustral.org']

DEFAULT_FILE_STORAGE = 'django_s3_storage.storage.S3Storage'
STATICFILES_STORAGE = 'django_s3_storage.storage.StaticS3Storage'

AWS_STORAGE_BUCKET_NAME = 'faticketera-zappa-dev'
AWS_QUERYSTRING_AUTH = False

AWS_S3_BUCKET_NAME_STATIC = 'faticketera-zappa-dev'
AWS_S3_BUCKET_AUTH_STATIC = False

AWS_S3_BUCKET_NAME = 'faticketera-zappa-dev'
AWS_S3_BUCKET_AUTH = False
