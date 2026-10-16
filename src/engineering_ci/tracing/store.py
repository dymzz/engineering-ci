"""Host storage compatibility with CI feedback validation supplied explicitly."""

from contextlib import contextmanager

from vesryn_log.archive import store as portable
from vesryn_log.archive.store import *


def validate_feedback(value):
    from vesryn.workflow_core.schemas import validate

    from ..workflow_adapters.contracts import FEEDBACK
    from ..workflow_adapters.feedback_snapshot import checked_snapshot

    validate(value, FEEDBACK, label="archival capability feedback")
    checked_snapshot(value)


class ArchiveStore(portable.ArchiveStore):
    """Retain the host method while keeping domain rules out of portable storage."""

    def register_feedback(self, value):
        validate_feedback(value)
        from ..workflow_core.documents import canonical, digest

        if value["feedback_id"] != digest(
            {key: item for key, item in value.items() if key != "feedback_id"}
        ):
            raise ArchiveError("invalid_feedback", "Feedback content identity differs")
        return self.store_feedback(
            value["feedback_id"], value["capability"]["approval_id"], canonical(value)
        )


@contextmanager
def open_store(config, *, environ=None):
    with portable.open_store(config, environ=environ, store_type=ArchiveStore) as store:
        yield store
