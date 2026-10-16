"""Compatibility configuration anchored to the original engineering-ci checkout."""

from vesryn_log.archive.config import *
from vesryn_log.archive.config import load_config as portable_config

from .._paths import ROOT


def load_config(path=None, *, root=None):
    return portable_config(path, root=ROOT if root is None else root)
