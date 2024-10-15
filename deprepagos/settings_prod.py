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




# override development settings
PIPELINE['PIPELINE_ENABLED'] = True
