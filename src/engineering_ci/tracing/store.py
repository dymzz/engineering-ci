"""Host storage compatibility with CI feedback validation supplied explicitly."""

from contextlib import contextmanager

from vesryn.tracing import store as portable
from vesryn.tracing.store import *


def validate_feedback(value):
    from vesryn.workflow_core.schemas import validate

    from ..workflow_adapters.contracts import FEEDBACK
    from ..workflow_adapters.feedback_snapshot import checked_snapshot

    validate(value, FEEDBACK, label="archival capability feedback")
    checked_snapshot(value)


class ArchiveStore(portable.ArchiveStore):
    """Retain the host method while keeping domain rules out of portable storage."""

    def register_feedback(self, value):
        return super().register_feedback(value, validator=validate_feedback)


@contextmanager
def open_store(config, *, environ=None):
    with portable.open_store(config, environ=environ, store_type=ArchiveStore) as store:
        yield store
