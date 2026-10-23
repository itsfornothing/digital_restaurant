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

import logging
import mimetypes
from datetime import datetime
from urllib.parse import urljoin

from botocore.exceptions import ClientError
from decouple import config
from django.core.exceptions import ImproperlyConfigured
from django.core.files.storage import FileSystemStorage, Storage
from django.utils.deconstruct import deconstructible

logger = logging.getLogger(__name__)


@deconstructible
class R2Storage(Storage):
    """
    Django Storage backend for Cloudflare R2 (S3-compatible).

    Falls back to local FileSystemStorage when R2 is not configured or fails —
    this allows the app to run in development and free-tier production without R2 credentials.
    """

    def __init__(self):
        self._endpoint_url = config("R2_ENDPOINT_URL", default="").strip()
        self._access_key_id = config("R2_ACCESS_KEY_ID", default="").strip()
        self._secret_access_key = config("R2_SECRET_ACCESS_KEY", default="").strip()
        self._bucket_name = config("R2_BUCKET_NAME", default="restaurant-platform").strip()
        self._custom_domain = config("R2_CUSTOM_DOMAIN", default="").strip()

        is_r2_ready = bool(
            self._endpoint_url
            and "dummy" not in self._endpoint_url.lower()
            and "placeholder" not in self._endpoint_url.lower()
            and self._access_key_id
            and "placeholder" not in self._access_key_id.lower()
            and self._secret_access_key
        )

        if not is_r2_ready:
            self._client = None
            self._fallback: FileSystemStorage | None = FileSystemStorage()
        else:
            try:
                import boto3
                self._client = boto3.client(
                    "s3",
                    endpoint_url=self._endpoint_url,
                    aws_access_key_id=self._access_key_id,
                    aws_secret_access_key=self._secret_access_key,
                    region_name="auto",
                )
                self._fallback = FileSystemStorage()
            except Exception as exc:
                logger.warning("Could not initialize R2 client: %s; falling back to local storage", exc)
                self._client = None
                self._fallback = FileSystemStorage()

    # ------------------------------------------------------------------
    # Fallback delegation helpers
    # ------------------------------------------------------------------

    def _open(self, name: str, mode: str = "rb"):
        if not self._client:
            return (self._fallback or FileSystemStorage())._open(name, mode)
        try:
            response = self._client.get_object(Bucket=self._bucket_name, Key=name)
            return response["Body"]
        except Exception:
            return (self._fallback or FileSystemStorage())._open(name, mode)

    def _save(self, name: str, content) -> str:
        if not self._client:
            return (self._fallback or FileSystemStorage())._save(name, content)

        try:
            object_name = self._generate_object_name(name)
            content_type, _ = mimetypes.guess_type(name)
            extra_args = {}
            if content_type:
                extra_args["ContentType"] = content_type
            self._client.upload_fileobj(
                content, self._bucket_name, object_name, ExtraArgs=extra_args
            )
            return object_name
        except Exception as exc:
            logger.warning("R2 upload failed (%s); falling back to local storage for %s", exc, name)
            return (self._fallback or FileSystemStorage())._save(name, content)

    def delete(self, name: str) -> None:
        if not self._client:
            return (self._fallback or FileSystemStorage()).delete(name)
        try:
            self._client.delete_object(Bucket=self._bucket_name, Key=name)
        except Exception:
            (self._fallback or FileSystemStorage()).delete(name)

    def exists(self, name: str) -> bool:
        if not self._client:
            return (self._fallback or FileSystemStorage()).exists(name)
        try:
            self._client.head_object(Bucket=self._bucket_name, Key=name)
            return True
        except Exception:
            return (self._fallback or FileSystemStorage()).exists(name)

    def url(self, name: str) -> str:
        if not self._client or not self._endpoint_url or "dummy" in self._endpoint_url.lower() or name.startswith("/"):
            return (self._fallback or FileSystemStorage()).url(name)
        if self._custom_domain:
            return urljoin(self._custom_domain.rstrip("/") + "/", name)
        try:
            return self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket_name, "Key": name},
                ExpiresIn=3600,
            )
        except Exception:
            return (self._fallback or FileSystemStorage()).url(name)

    def size(self, name: str) -> int:
        if not self._client:
            return (self._fallback or FileSystemStorage()).size(name)
        try:
            response = self._client.head_object(Bucket=self._bucket_name, Key=name)
            return response["ContentLength"]
        except Exception:
            return (self._fallback or FileSystemStorage()).size(name)

    def get_available_name(self, name: str, max_length: int = None) -> str:
        if not self._client:
            return (self._fallback or FileSystemStorage()).get_available_name(name, max_length)
        return name

    # ------------------------------------------------------------------
    # Private helper
    # ------------------------------------------------------------------

    def _generate_object_name(self, name: str) -> str:
        """Prefix object names with a date-based path to avoid collisions."""
        date_prefix = datetime.utcnow().strftime("%Y/%m/%d")
        return f"{date_prefix}/{name}"
