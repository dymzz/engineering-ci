"""Host bindings remain here; portable analysis is delegated to Vesryn."""

import sys

from vesryn.cli.mine import *
from vesryn.cli.mine import main as portable_main

from .._paths import ROOT


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in {"bind", "workflow"}:
        from importlib import import_module

        return import_module(".mine_" + args[0], __package__).main(args[1:])
    if (
        args
        and args[0] == "export-xes"
        and any(
            item == "--execution" or item.startswith("--execution=") for item in args
        )
    ):
        from .mine_xes import main as export_execution

        return export_execution(args[1:])
    if args and not any(
        item == "--config" or item.startswith("--config=") for item in args
    ):
        args += ["--config", str(ROOT / "config/tracing.toml")]
    return portable_main(args)
