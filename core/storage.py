from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Protocol, runtime_checkable


@runtime_checkable
class Storage(Protocol):
    def put(self, path: str, data: bytes) -> None: ...
    def get(self, path: str) -> bytes: ...
    def delete(self, path: str) -> None: ...
    def list(self, prefix: str) -> List[str]: ...
    def exists(self, path: str) -> bool: ...


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _full(self, path: str) -> Path:
        path = path.lstrip("/")
        full = (self.root / path).resolve()
        if self.root.resolve() not in full.parents and full != self.root.resolve():
            raise ValueError(f"Ruta fuera del root permitido: {path}")
        return full

    def put(self, path: str, data: bytes) -> None:
        full = self._full(path)
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_bytes(data)

    def get(self, path: str) -> bytes:
        return self._full(path).read_bytes()

    def delete(self, path: str) -> None:
        full = self._full(path)
        if full.is_file():
            full.unlink()
        elif full.is_dir():
            for child in sorted(full.rglob("*"), key=lambda p: -len(p.parts)):
                if child.is_file():
                    child.unlink()
                else:
                    child.rmdir()
            full.rmdir()

    def list(self, prefix: str) -> List[str]:
        base = self._full(prefix)
        if not base.exists():
            return []
        if base.is_file():
            return [prefix.lstrip("/")]
        results: List[str] = []
        for child in base.rglob("*"):
            if child.is_file():
                rel = child.relative_to(self.root).as_posix()
                results.append(rel)
        return sorted(results)

    def exists(self, path: str) -> bool:
        try:
            return self._full(path).exists()
        except ValueError:
            return False


class S3Storage:
    def __init__(
        self,
        bucket: str,
        endpoint_url: str,
        access_key: str,
        secret_key: str,
        region: str = "auto",
    ) -> None:
        import boto3
        from botocore.config import Config

        self.bucket = bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            config=Config(signature_version="s3v4"),
        )

    def put(self, path: str, data: bytes) -> None:
        self._client.put_object(Bucket=self.bucket, Key=path.lstrip("/"), Body=data)

    def get(self, path: str) -> bytes:
        resp = self._client.get_object(Bucket=self.bucket, Key=path.lstrip("/"))
        return resp["Body"].read()

    def delete(self, path: str) -> None:
        key = path.lstrip("/")
        keys = self.list(key)
        if not keys:
            self._client.delete_object(Bucket=self.bucket, Key=key)
            return
        for batch in _chunked(keys, 1000):
            self._client.delete_objects(
                Bucket=self.bucket,
                Delete={"Objects": [{"Key": k} for k in batch]},
            )

    def list(self, prefix: str) -> List[str]:
        prefix = prefix.lstrip("/")
        keys: List[str] = []
        continuation = None
        while True:
            kwargs = {"Bucket": self.bucket, "Prefix": prefix}
            if continuation:
                kwargs["ContinuationToken"] = continuation
            resp = self._client.list_objects_v2(**kwargs)
            for obj in resp.get("Contents", []):
                keys.append(obj["Key"])
            if resp.get("IsTruncated"):
                continuation = resp.get("NextContinuationToken")
            else:
                break
        return sorted(keys)

    def exists(self, path: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self._client.head_object(Bucket=self.bucket, Key=path.lstrip("/"))
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] in {"404", "NoSuchKey"}:
                return False
            raise


def _chunked(items: Iterable[str], size: int):
    batch: List[str] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch
