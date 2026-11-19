"""Source-checkout paths shared by local checks and release operations."""

import os
import stat
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class SourcePathError(OSError):
    """A path cannot supply regular file bytes from the selected Source."""

    def __init__(self, kind, component):
        super().__init__(kind)
        self.kind = kind
        self.component = component


def stat_source_file(root, relative):
    """Inspect a repository-relative regular file without traversing links."""
    path = Path(relative)
    if path.anchor or not path.parts or ".." in path.parts:
        raise SourcePathError("invalid_path", path)
    current = Path(root)
    for index, component in enumerate(path.parts, start=1):
        current /= component
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or (
            getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise SourcePathError("link", Path(*path.parts[:index]))
        if index < len(path.parts) and not stat.S_ISDIR(info.st_mode):
            raise NotADirectoryError(current)
    if not stat.S_ISREG(info.st_mode):
        raise SourcePathError("non_regular", path)
    return info


def source_file_present(root, relative):
    """Return whether a declared file is present or has an unresolved type."""
    try:
        stat_source_file(root, relative)
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError:
        pass
    return True


def read_source_file(root, relative):
    """Read the inspected file only while its identity and version agree."""
    before = stat_source_file(root, relative)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(Path(root) / relative, flags), "rb") as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or not os.path.samestat(before, opened):
            raise SourcePathError("path_changed", Path(relative))
        content = stream.read()
        after = stat_source_file(root, relative)
        if not os.path.samestat(before, after) or (
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise SourcePathError("path_changed", Path(relative))
    return content


def read_source_entry(root, relative):
    """Read regular bytes or the literal target of a direct link entry."""
    try:
        return read_source_file(root, relative), False
    except SourcePathError as error:
        if error.kind != "link" or error.component != Path(relative):
            raise
        return os.fsencode(os.readlink(Path(root) / relative)), True
