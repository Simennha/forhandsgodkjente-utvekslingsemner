"""Object storage with object lock (WORM) for audit-log exports and backups.

There is no delete method: deleting backups is level 4. The real implementation uses
S3 Object Lock in COMPLIANCE mode, which the storage provider enforces even against the
account owner until the retention date.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from assistants.common.errors import Denied


@dataclass(frozen=True)
class StoredObject:
    key: str
    data: bytes
    retain_until: datetime


class ObjectStore(Protocol):
    def put(self, key: str, data: bytes, retain_until: datetime) -> None: ...
    def get(self, key: str) -> bytes: ...
    def list(self, prefix: str = "") -> list[str]: ...


class S3ObjectLockStore:
    """S3-compatible store with Object Lock (e.g. Backblaze B2, Wasabi, AWS). NOT verified.

    Requires boto3 (not a default dependency) and a bucket created with Object Lock enabled.
    The access key should only have PutObject/GetObject/ListBucket - not DeleteObject or
    BypassGovernanceRetention.
    """

    def __init__(self, bucket: str, endpoint_url: str, access_key: str, secret_key: str):
        import boto3  # type: ignore[import-not-found]  # noqa: PLC0415

        self._bucket = bucket
        self._s3: Any = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )

    def put(self, key: str, data: bytes, retain_until: datetime) -> None:
        self._s3.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ObjectLockMode="COMPLIANCE",
            ObjectLockRetainUntilDate=retain_until,
        )

    def get(self, key: str) -> bytes:
        body = self._s3.get_object(Bucket=self._bucket, Key=key)["Body"].read()
        return bytes(body)

    def list(self, prefix: str = "") -> list[str]:
        resp = self._s3.list_objects_v2(Bucket=self._bucket, Prefix=prefix)
        return [str(o["Key"]) for o in resp.get("Contents", [])]


@dataclass
class MemoryObjectStore:
    """Local WORM simulator: an existing key can never be overwritten."""

    objects: dict[str, StoredObject] = field(default_factory=dict)

    def put(self, key: str, data: bytes, retain_until: datetime) -> None:
        if key in self.objects:
            raise Denied(f"object lock: {key} already exists and cannot be overwritten")
        self.objects[key] = StoredObject(key, data, retain_until)

    def get(self, key: str) -> bytes:
        return self.objects[key].data

    def list(self, prefix: str = "") -> list[str]:
        return sorted(k for k in self.objects if k.startswith(prefix))
