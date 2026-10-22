"""Module carrying one confirmed type error for the Pyright negative case."""


def combine(left: int, right: int) -> int:
    """Return the concatenation of two integers."""
    return f"{left}{right}"
