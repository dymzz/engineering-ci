"""Keep one archive error contract while retaining portable analysis operations."""

from functools import wraps


def archive_errors(function):
    """Translate the pinned Harness's historical archive exception at our boundary."""

    @wraps(function)
    def invoke(*args, **kwargs):
        from vesryn.tracing.store import ArchiveError as LegacyError
        from vesryn_log.archive.store import ArchiveError

        try:
            return function(*args, **kwargs)
        except LegacyError as error:
            raise ArchiveError(
                error.status, str(error), sqlstate=error.sqlstate
            ) from None

    return invoke
