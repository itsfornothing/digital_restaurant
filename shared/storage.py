"""
Cloudflare R2 storage backend.

When R2_ENDPOINT_URL is not set (development), automatically falls back to
Django's FileSystemStorage so local development works without R2 credentials.

Required environment variables for production:
    R2_ENDPOINT_URL       — e.g. https://<account-id>.r2.cloudflarestorage.com
    R2_ACCESS_KEY_ID      — R2 Access Key ID
    R2_SECRET_ACCESS_KEY  — R2 Secret Access Key
    R2_BUCKET_NAME        — Target bucket name (default: restaurant-platform)
    R2_CUSTOM_DOMAIN      — (Optional) Public custom domain for file URLs
"""

import mimetypes
from datetime import datetime
from urllib.parse import urljoin

from botocore.exceptions import ClientError
from decouple import config
from django.core.exceptions import ImproperlyConfigured
from django.core.files.storage import FileSystemStorage, Storage
from django.utils.deconstruct import deconstructible


@deconstructible
class R2Storage(Storage):
    """
    Django Storage backend for Cloudflare R2 (S3-compatible).

    Falls back to local FileSystemStorage when R2 is not configured —
    this allows the app to run in development without R2 credentials.
    """

    def __init__(self):
        self._endpoint_url = config("R2_ENDPOINT_URL", default="")
        self._access_key_id = config("R2_ACCESS_KEY_ID", default="")
        self._secret_access_key = config("R2_SECRET_ACCESS_KEY", default="")
        self._bucket_name = config("R2_BUCKET_NAME", default="restaurant-platform")
        self._custom_domain = config("R2_CUSTOM_DOMAIN", default="")

        if not self._endpoint_url:
            # Development: use local filesystem storage as fallback
            self._client = None
            self._fallback: FileSystemStorage | None = FileSystemStorage()
        else:
            self._fallback = None
            import boto3
            self._client = boto3.client(
                "s3",
                endpoint_url=self._endpoint_url,
                aws_access_key_id=self._access_key_id,
                aws_secret_access_key=self._secret_access_key,
                region_name="auto",
            )

    # ------------------------------------------------------------------
    # Fallback delegation helpers
    # ------------------------------------------------------------------

    def _open(self, name: str, mode: str = "rb"):
        if self._fallback:
            return self._fallback._open(name, mode)
        response = self._client.get_object(Bucket=self._bucket_name, Key=name)
        return response["Body"]

    def _save(self, name: str, content) -> str:
        if self._fallback:
            return self._fallback._save(name, content)

        object_name = self._generate_object_name(name)
        content_type, _ = mimetypes.guess_type(name)
        extra_args = {}
        if content_type:
            extra_args["ContentType"] = content_type
        self._client.upload_fileobj(
            content, self._bucket_name, object_name, ExtraArgs=extra_args
        )
        return object_name

    def delete(self, name: str) -> None:
        if self._fallback:
            return self._fallback.delete(name)
        try:
            self._client.delete_object(Bucket=self._bucket_name, Key=name)
        except ClientError:
            pass

    def exists(self, name: str) -> bool:
        if self._fallback:
            return self._fallback.exists(name)
        try:
            self._client.head_object(Bucket=self._bucket_name, Key=name)
            return True
        except ClientError:
            return False

    def url(self, name: str) -> str:
        if self._fallback:
            return self._fallback.url(name)
        if self._custom_domain:
            return urljoin(self._custom_domain.rstrip("/") + "/", name)
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket_name, "Key": name},
            ExpiresIn=3600,
        )

    def size(self, name: str) -> int:
        if self._fallback:
            return self._fallback.size(name)
        response = self._client.head_object(Bucket=self._bucket_name, Key=name)
        return response["ContentLength"]

    def get_available_name(self, name: str, max_length: int = None) -> str:
        if self._fallback:
            return self._fallback.get_available_name(name, max_length)
        return name

    # ------------------------------------------------------------------
    # Private helper
    # ------------------------------------------------------------------

    def _generate_object_name(self, name: str) -> str:
        """Prefix object names with a date-based path to avoid collisions."""
        date_prefix = datetime.utcnow().strftime("%Y/%m/%d")
        return f"{date_prefix}/{name}"
