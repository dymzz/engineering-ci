"""Log-owned serialization; projection and mining validation remain in the Harness."""

from vesryn_log.archive.results import *

from ._compat import archive_errors


@archive_errors
def prepare_result(value, input_digest, *, origin="computed"):
    """Validate a derived Harness result without delegating physical persistence."""
    from vesryn.tracing.results import prepare_result as prepare

    return prepare(value, input_digest, origin=origin)
