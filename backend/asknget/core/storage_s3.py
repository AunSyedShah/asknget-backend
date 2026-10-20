"""S3-compatible storage (AWS S3, Cloudflare R2, MinIO). Imported only when FILE_STORAGE=s3: django-storages and boto3 are not
installed in the Vercel/default image. See core/storage_config.py for the settings."""

from storages.backends.s3 import S3Storage

from .storage import UrlFriendlyMixin


class PublicS3Storage(UrlFriendlyMixin, S3Storage):
    """Product pictures, banners, avatars, review media: public links (through CloudFront when AWS_S3_CUSTOM_DOMAIN is set)."""


class PrivateS3Storage(UrlFriendlyMixin, S3Storage):
    """Seller documents and category-request files. Their `.url` is an expiring signed link; the API normally serves them itself
    (`doc.file.open()`), so the bucket can block all public access."""
