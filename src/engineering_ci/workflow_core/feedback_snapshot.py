"""Compatibility import; implementation is owned by Vesryn."""

import sys
from importlib import import_module

sys.modules[__name__] = import_module(
    "engineering_ci.workflow_adapters.feedback_snapshot"
)
