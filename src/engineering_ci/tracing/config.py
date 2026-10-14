"""Compatibility configuration anchored to the original engineering-ci checkout."""

from vesryn.tracing.config import *
from vesryn.tracing.config import load_config as portable_config

from .._paths import ROOT


def load_config(path=None, *, root=None):
    return portable_config(path, root=ROOT if root is None else root)
