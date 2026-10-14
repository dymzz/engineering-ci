"""Compatibility trace command with checkout-anchored configuration."""

import sys

from vesryn.cli.trace import *
from vesryn.cli.trace import main as portable_main

from .._paths import ROOT


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args and not any(
        item == "--config" or item.startswith("--config=") for item in args
    ):
        args += ["--config", str(ROOT / "config/tracing.toml")]
    return portable_main(args)
