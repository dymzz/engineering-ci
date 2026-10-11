"""Reusable Engineering CI capabilities."""


def main() -> None:
    from .cli import main as invoke

    raise SystemExit(invoke())
