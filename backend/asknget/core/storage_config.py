"""STORAGES for settings.py, from environment variables. A plain function so it can be tested without touching the environment.

FILE_STORAGE        filesystem | database | s3     (default: database on Vercel, filesystem elsewhere)

S3 (AWS S3, Cloudflare R2, MinIO), needs `uv add "django-storages[s3]"` in the image that uses it:
  AWS_STORAGE_BUCKET_NAME     public media (product pictures, banners, avatars, review media)
  AWS_PRIVATE_BUCKET_NAME     seller documents and category-request files; served only through the API's signed links.
                              Use a SEPARATE bucket with all public access blocked. Unset = the same bucket (do not do this in production).
  AWS_S3_REGION_NAME          e.g. eu-west-1
  AWS_S3_ENDPOINT_URL         only for R2 / MinIO (leave unset on AWS)
  AWS_S3_CUSTOM_DOMAIN        e.g. media.asknget.com, a CloudFront distribution in front of the public bucket (recommended)
  AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY   not needed on ECS/EC2 (the task role is used); set them on Vercel
"""

import importlib.util
from collections.abc import Mapping

from django.core.exceptions import ImproperlyConfigured

BACKENDS = ("filesystem", "database", "s3")


def file_storage_name(env: Mapping, on_vercel: bool) -> str:
    name = (env.get("FILE_STORAGE") or ("database" if on_vercel else "filesystem")).strip().lower()
    if name not in BACKENDS:
        raise ImproperlyConfigured(f"FILE_STORAGE must be one of {', '.join(BACKENDS)} (got {name!r}).")
    return name


def build_storages(env: Mapping, on_vercel: bool, *, signed_ttl: int = 3600) -> dict:
    name = file_storage_name(env, on_vercel)
    staticfiles = {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"}
    if name == "filesystem":
        default = {"BACKEND": "core.storage.UrlFriendlyStorage"}
        return {"default": default, "private": default, "staticfiles": staticfiles}
    if name == "database":
        default = {"BACKEND": "core.storage.DatabaseStorage"}
        return {"default": default, "private": default, "staticfiles": staticfiles}

    if importlib.util.find_spec("storages") is None or importlib.util.find_spec("boto3") is None:
        raise ImproperlyConfigured('FILE_STORAGE=s3 needs django-storages and boto3: add `uv add "django-storages[s3]"`.')
    bucket = env.get("AWS_STORAGE_BUCKET_NAME", "").strip()
    if not bucket:
        raise ImproperlyConfigured("FILE_STORAGE=s3 needs AWS_STORAGE_BUCKET_NAME.")
    common = {"region_name": env.get("AWS_S3_REGION_NAME") or None, "endpoint_url": env.get("AWS_S3_ENDPOINT_URL") or None,
              "file_overwrite": False, "default_acl": None}
    public = {"BACKEND": "core.storage_s3.PublicS3Storage",
              "OPTIONS": {**common, "bucket_name": bucket, "custom_domain": env.get("AWS_S3_CUSTOM_DOMAIN") or None, "querystring_auth": False}}
    private = {"BACKEND": "core.storage_s3.PrivateS3Storage",
              "OPTIONS": {**common, "bucket_name": env.get("AWS_PRIVATE_BUCKET_NAME", "").strip() or bucket, "querystring_auth": True,
                          "querystring_expire": signed_ttl}}
    return {"default": public, "private": private, "staticfiles": staticfiles}
