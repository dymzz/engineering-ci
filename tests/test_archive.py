"""P0-2: ZIP structural validation and protected extraction."""

import stat
import zipfile

import pytest

from engineering_ci.execution.archive import ArchiveLimits, extract_zip, validate_zip
from engineering_ci.execution.models import ExecutionError


def make_zip(path, files):
    with zipfile.ZipFile(path, "w") as archive:
        for name, body in files.items():
            info = zipfile.ZipInfo("placeholder")
            info.filename = name
            archive.writestr(info, body)
    return path


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        "/absolute",
        "C:/file",
        "dir\\file",
        "CON.txt",
        "trailing./file",
        "a//b",
        "a/../b",
    ],
)
def test_reject_unsafe_zip_paths(tmp_path, name):
    path = make_zip(tmp_path / "bad.zip", {name: "bad"})
    with pytest.raises(ExecutionError, match="Unsafe ZIP"):
        extract_zip(path, tmp_path / "out")
    assert not (tmp_path / "escape").exists()


def test_corrupt_archive_and_limits(tmp_path):
    path = tmp_path / "bad.zip"
    path.write_bytes(b"broken")
    with pytest.raises(ExecutionError):
        validate_zip(path, ArchiveLimits())
    make_zip(path, {"large.txt": "X" * 100})
    with pytest.raises(ExecutionError, match="limits"):
        validate_zip(path, ArchiveLimits(max_bytes=10))


def test_no_unexpected_overwrite_and_idempotent_extraction(tmp_path):
    path = make_zip(tmp_path / "ok.zip", {"sub/test.txt": "hello"})
    out = tmp_path / "out"
    manifest = extract_zip(path, out, prefix="artifact")
    assert extract_zip(path, out, prefix="artifact", owned=manifest) == manifest
    (out / "artifact/sub/test.txt").write_text("user content")
    with pytest.raises(ExecutionError, match="overwrite"):
        extract_zip(path, out, prefix="artifact", owned=manifest)


def test_symlink_duplicate_and_case_collisions(tmp_path):
    path = tmp_path / "bad.zip"
    info = zipfile.ZipInfo("link")
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(info, "target")
    with pytest.raises(ExecutionError, match="link"):
        validate_zip(path, ArchiveLimits())
    make_zip(path, {"Foo": "1", "foo": "2"})
    with pytest.raises(ExecutionError, match="colliding"):
        validate_zip(path, ArchiveLimits())


def test_zip_cannot_write_collection_metadata(tmp_path):
    path = make_zip(tmp_path / "bad.zip", {".ghwf-cache/resource.json": "fake"})
    with pytest.raises(ExecutionError, match="reserved"):
        extract_zip(path, tmp_path / "out")
