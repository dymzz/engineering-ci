"""Transport boundary shared by native protocol implementations."""
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from .models import fingerprint


@dataclass(frozen=True)
class ResourceIdentity:
    """Stable remote resource metadata; never includes a signed URL."""
    key: str
    size: int | None = None
    digest: str | None = None
    immutable: bool = False

    @property
    def signature(self) -> str:
        return fingerprint(asdict(self))


@dataclass(frozen=True)
class DownloadProgress:
    completed_bytes: int | None
    total_bytes: int | None
    resumed: bool
    restart_reason: str | None = None


@dataclass(frozen=True)
class TransportResult:
    path: Path
    progress: DownloadProgress


class TransportAdapter(Protocol):
    """Execute transport and report progress; never decide workflow restart."""
    def download(self, resolve_url, identity: ResourceIdentity, target: Path) -> TransportResult: ...

# Git/LFS/OCI can implement this boundary when an actual caller needs them.
