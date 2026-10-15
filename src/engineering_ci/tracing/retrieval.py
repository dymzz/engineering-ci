"""Portable query interpretation with the logging package's archive error contract."""

from types import FunctionType

from vesryn.tracing import retrieval as portable
from vesryn.tracing.retrieval import *

from ._compat import archive_errors

for _name, _function in vars(portable).items():
    if (
        isinstance(_function, FunctionType)
        and _function.__module__ == portable.__name__
    ):
        globals()[_name] = archive_errors(_function)
