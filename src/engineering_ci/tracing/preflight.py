"""Compatibility import; physical archive implementation is owned by vesryn-log."""

import sys
from importlib import import_module

sys.modules[__name__] = import_module("vesryn_log.archive.preflight")
