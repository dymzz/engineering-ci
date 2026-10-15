"""Compatibility import; native source implementation is owned by vesryn-log."""

import sys
from importlib import import_module

sys.modules[__name__] = import_module("vesryn_log.sources.codex_rollout")
