"""File storage abstraction.

Local filesystem in dev; S3-compatible object storage in production (set
STORAGE_BACKEND=s3). Both verify writes are non-empty. Private CAD files are
never served from a guessable public path — downloads go through an
owner-checked API route, which uses ``read`` (local) or ``signed_url`` (S3).
"""
from __future__ import annotations

from pathlib import Path

from app.config import settings


class StorageError(Exception):
    pass


class Storage:
    backend = "base"

    def save(self, key: str, data: bytes) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def read(self, key: str) -> bytes:  # pragma: no cover - interface
        raise NotImplementedError

    def exists(self, key: str) -> bool:  # pragma: no cover - interface
        raise NotImplementedError

    def delete(self, key: str) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def signed_url(self, key: str, ttl: int | None = None) -> str | None:
        """A time-limited direct URL if the backend supports it, else None."""
        return None


def _validate_key(key: str) -> str:
    # Reject path traversal / absolute keys regardless of backend.
    if not key or key.startswith("/") or ".." in key.split("/"):
        raise StorageError(f"Illegal storage key: {key!r}")
    return key


class LocalStorage(Storage):
    backend = "local"

    def __init__(self, root: str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _full(self, key: str) -> Path:
        _validate_key(key)
        target = (self.root / key).resolve()
        if not str(target).startswith(str(self.root.resolve())):
            raise StorageError(f"Illegal storage key: {key}")
        return target

    def save(self, key: str, data: bytes) -> None:
        path = self._full(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if not path.exists() or path.stat().st_size == 0:
            raise StorageError(f"Wrote empty or missing file for key '{key}'")

    def read(self, key: str) -> bytes:
        path = self._full(key)
        if not path.exists():
            raise StorageError(f"No such object: {key}")
        return path.read_bytes()

    def exists(self, key: str) -> bool:
        return self._full(key).exists()

    def delete(self, key: str) -> None:
        self._full(key).unlink(missing_ok=True)


class S3Storage(Storage):
    """S3-compatible storage (AWS S3, MinIO, Cloudflare R2, ...).

    ``client`` may be injected for testing; otherwise a boto3 client is built
    from settings. Downloads use presigned URLs (``signed_url``).
    """

    backend = "s3"

    def __init__(self, bucket: str, client=None, ttl: int = 3600):
        if not bucket:
            raise StorageError("S3 storage requires S3_BUCKET")
        self.bucket = bucket
        self.ttl = ttl
        if client is not None:
            self._client = client
        else:  # pragma: no cover - exercised only with real/MinIO creds
            import boto3

            self._client = boto3.client(
                "s3",
                region_name=settings.s3_region,
                endpoint_url=settings.s3_endpoint_url,
                aws_access_key_id=settings.s3_access_key_id,
                aws_secret_access_key=settings.s3_secret_access_key,
            )

    def save(self, key: str, data: bytes) -> None:
        _validate_key(key)
        if not data:
            raise StorageError(f"Refusing to store empty object for key '{key}'")
        self._client.put_object(Bucket=self.bucket, Key=key, Body=data)

    def read(self, key: str) -> bytes:
        _validate_key(key)
        obj = self._client.get_object(Bucket=self.bucket, Key=key)
        return obj["Body"].read()

    def exists(self, key: str) -> bool:
        _validate_key(key)
        try:
            self._client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:  # noqa: BLE001 - any error means "not retrievable"
            return False

    def delete(self, key: str) -> None:
        _validate_key(key)
        self._client.delete_object(Bucket=self.bucket, Key=key)

    def signed_url(self, key: str, ttl: int | None = None) -> str | None:
        _validate_key(key)
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=ttl or self.ttl,
        )


_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        if settings.storage_backend == "s3":
            _storage = S3Storage(
                bucket=settings.s3_bucket or "",
                ttl=settings.s3_signed_url_ttl,
            )
        else:
            _storage = LocalStorage(settings.storage_dir)
    return _storage


def reset_storage_cache() -> None:
    """Force re-selection of the backend (used by tests)."""
    global _storage
    _storage = None
