"""Reusable execution state, recovery and transport contracts."""
from importlib import import_module

__all__ = ['ExecutionCoordinator', 'ExecutionFSM', 'InvalidTransition', 'StateStore']


def __getattr__(name):
    """Load execution dependencies only when their public capability is requested."""
    modules = {'ExecutionCoordinator': 'coordinator', 'ExecutionFSM': 'fsm',
               'InvalidTransition': 'fsm', 'StateStore': 'store'}
    if name not in modules:
        raise AttributeError(name)
    value = getattr(import_module(f'.{modules[name]}', __name__), name)
    globals()[name] = value
    return value
