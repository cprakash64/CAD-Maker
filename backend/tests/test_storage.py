"""Storage abstraction: local FS + S3 (with an injected fake client)."""
import tempfile

import pytest

from app.storage.storage import LocalStorage, S3Storage, StorageError


# --- LocalStorage ---------------------------------------------------------
def _local() -> LocalStorage:
    return LocalStorage(tempfile.mkdtemp(prefix="store-test-"))


def test_local_save_read_roundtrip():
    s = _local()
    s.save("d1/abc.stl", b"solid data")
    assert s.exists("d1/abc.stl")
    assert s.read("d1/abc.stl") == b"solid data"


def test_local_rejects_empty_write():
    s = _local()
    with pytest.raises(StorageError):
        s.save("d1/empty.stl", b"")


def test_local_rejects_path_traversal():
    s = _local()
    for bad in ("../escape.stl", "/abs/path.stl", "a/../../b.stl"):
        with pytest.raises(StorageError):
            s.save(bad, b"x")


def test_local_delete_and_missing_read():
    s = _local()
    s.save("d/x.step", b"ISO-10303")
    s.delete("d/x.step")
    assert not s.exists("d/x.step")
    with pytest.raises(StorageError):
        s.read("d/x.step")


def test_local_has_no_signed_url():
    assert _local().signed_url("d/x.stl") is None


# --- S3Storage with a fake boto3 client -----------------------------------
class FakeS3Client:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def put_object(self, Bucket, Key, Body):  # noqa: N803 - boto3 signature
        self.objects[(Bucket, Key)] = Body

    def get_object(self, Bucket, Key):  # noqa: N803
        import io

        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}

    def head_object(self, Bucket, Key):  # noqa: N803
        if (Bucket, Key) not in self.objects:
            raise KeyError("missing")

    def delete_object(self, Bucket, Key):  # noqa: N803
        self.objects.pop((Bucket, Key), None)

    def generate_presigned_url(self, op, Params, ExpiresIn):  # noqa: N803
        return f"https://s3.example.com/{Params['Bucket']}/{Params['Key']}?exp={ExpiresIn}"


def test_s3_save_read_roundtrip():
    fake = FakeS3Client()
    s = S3Storage(bucket="parts", client=fake, ttl=120)
    s.save("d1/abc.step", b"ISO-10303-21")
    assert s.exists("d1/abc.step")
    assert s.read("d1/abc.step") == b"ISO-10303-21"
    assert fake.objects[("parts", "d1/abc.step")] == b"ISO-10303-21"


def test_s3_presigned_url():
    s = S3Storage(bucket="parts", client=FakeS3Client(), ttl=99)
    url = s.signed_url("d1/abc.stl")
    assert url is not None and "parts/d1/abc.stl" in url and "exp=99" in url


def test_s3_rejects_empty_and_traversal():
    s = S3Storage(bucket="parts", client=FakeS3Client())
    with pytest.raises(StorageError):
        s.save("d/x.stl", b"")
    with pytest.raises(StorageError):
        s.save("../x.stl", b"data")


def test_s3_requires_bucket():
    with pytest.raises(StorageError):
        S3Storage(bucket="", client=FakeS3Client())
