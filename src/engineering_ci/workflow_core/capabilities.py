"""Compatibility API with original CI-specific admission constraints."""

from vesryn.workflow_core.capabilities import (
    CALL,
    EVIDENCE,
    INDEX,
    REFERENCE,
    SCOPE,
    VERSION,
)
from vesryn.workflow_core.capabilities import CapabilityStore as GenericCapabilityStore
from vesryn.workflow_core.schemas import SCHEMAS

from ..workflow_adapters.contracts import APPROVAL, FEEDBACK

__all__ = [
    "APPROVAL",
    "CALL",
    "EVIDENCE",
    "FEEDBACK",
    "INDEX",
    "REFERENCE",
    "SCOPE",
    "VERSION",
    "CapabilityStore",
]

SCHEMAS.update(capability_approval=APPROVAL, capability_feedback=FEEDBACK)


class CapabilityStore(GenericCapabilityStore):
    """Reuse the generic lifecycle under the host's original strict approval Schema."""

    def __init__(self, directory):
        super().__init__(directory, approval_schema=APPROVAL)
