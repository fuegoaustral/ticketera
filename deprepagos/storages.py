from django.conf import settings
from django.contrib.staticfiles.storage import ManifestFilesMixin
from pipeline.storage import PipelineMixin
from storages.backends.s3boto3 import S3Boto3Storage, S3StaticStorage


class StaticS3PipelineManifestStorage(PipelineMixin, S3StaticStorage):
    pass


class S3PipelineManifestStorage(PipelineMixin, S3Boto3Storage):
    pass
