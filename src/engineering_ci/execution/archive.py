"""ZIP integrity and bounded, staged extraction for downloaded results."""

from __future__ import annotations

import os
import re
import shutil
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .download import file_digest
from .models import ExecutionError


@dataclass(frozen=True)
class ArchiveLimits:
    max_bytes: int = 8 * 1024**3
    max_files: int = 100_000
    max_ratio: int = 1000


def safe_members(archive: zipfile.ZipFile, limits: ArchiveLimits):
    """Reject traversal, links, Windows device names and ZIP bombs before writes."""
    members = archive.infolist()
    if (
        len(members) > limits.max_files
        or sum(m.file_size for m in members) > limits.max_bytes
    ):
        raise ExecutionError("ZIP exceeds configured extraction limits")
    seen = set()
    paths = set()
    for member in members:
        name = member.orig_filename
        path = PurePosixPath(name)
        parts = name.rstrip("/").split("/")
        if parts[0].casefold() in (".ghwf-cache", ".ghwf-collection.lock"):
            raise ExecutionError("ZIP targets reserved collection metadata")
        if (
            not name
            or "\\" in name
            or path.is_absolute()
            or any(p in ("", ".", "..") for p in parts)
            or any(
                ":" in p or p.endswith((" ", ".")) or re.search(r"[\x00-\x1f]", p)
                for p in parts
            )
            or any(
                re.fullmatch(
                    r"(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", p, re.IGNORECASE
                )
                for p in parts
            )
        ):
            raise ExecutionError("Unsafe ZIP path")
        mode = member.external_attr >> 16
        if stat.S_ISLNK(mode) or stat.S_IFMT(mode) not in (
            0,
            stat.S_IFREG,
            stat.S_IFDIR,
        ):
            raise ExecutionError("ZIP contains a link or special file")
        if member.flag_bits & 1:
            raise ExecutionError("Encrypted ZIP is unsupported")
        if member.file_size > max(1, member.compress_size) * limits.max_ratio:
            raise ExecutionError("ZIP compression ratio exceeds configured limit")
        key = "/".join(parts).casefold()
        if key in seen:
            raise ExecutionError("ZIP has duplicate or case-colliding paths")
        if any("/".join(parts[:i]).casefold() in paths for i in range(1, len(parts))):
            raise ExecutionError("ZIP file/directory collision")
        seen.add(key)
        if not member.is_dir():
            if any(p.startswith(key + "/") for p in seen):
                raise ExecutionError("ZIP file/directory collision")
            paths.add(key)
    if archive.testzip() is not None:
        raise ExecutionError("ZIP CRC verification failed")
    return members


def validate_zip(path: Path, limits: ArchiveLimits) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            safe_members(archive, limits)
    except ExecutionError:
        raise
    except (zipfile.BadZipFile, RuntimeError, EOFError) as exc:
        raise ExecutionError("Invalid or corrupt ZIP archive") from exc


def _safe_destination(dest: Path, relative: str) -> Path:
    target = dest / relative
    current = dest
    if current.is_symlink():
        raise ExecutionError("Output directory is a symbolic link")
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise ExecutionError("Output path traverses a symbolic link")
    if not target.resolve().is_relative_to(dest.resolve()):
        raise ExecutionError("Output escapes collection directory")
    return target


def extract_zip(
    path: Path,
    dest: Path,
    *,
    prefix: str = "",
    owned: dict | None = None,
    limits: ArchiveLimits | None = None,
) -> dict[str, str]:
    """Stage and validate all files, replacing only previously owned outputs."""
    limits = limits or ArchiveLimits()
    owned = owned or {}
    dest.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".ghwf-extract-", dir=path.parent))
    try:
        with zipfile.ZipFile(path) as archive:
            for member in safe_members(archive, limits):
                if member.is_dir():
                    continue
                temp = stage / member.filename
                temp.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, temp.open("wb") as output:
                    shutil.copyfileobj(source, output)
        manifest = {}
        for source in sorted(stage.rglob("*")):
            if not source.is_file():
                continue
            relative = (
                PurePosixPath(prefix) / source.relative_to(stage).as_posix()
            ).as_posix()
            target = _safe_destination(dest, relative)
            sha = file_digest(source)
            if target.exists() and (
                not target.is_file()
                or file_digest(target) != sha
                and owned.get(relative) != file_digest(target)
            ):
                raise ExecutionError(
                    f"Refusing to overwrite unexpected output: {relative}"
                )
            manifest[relative] = sha
        for relative in manifest:
            source = stage / PurePosixPath(relative).relative_to(prefix or ".")
            target = _safe_destination(dest, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, target)
        if not manifest:
            raise ExecutionError("Artifact ZIP contains no files")
        return manifest
    except ExecutionError:
        raise
    except (zipfile.BadZipFile, RuntimeError, EOFError) as exc:
        raise ExecutionError("Invalid or corrupt ZIP archive") from exc
    finally:
        shutil.rmtree(stage)
