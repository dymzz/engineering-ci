"""Clean module: no dead code, no type errors, no duplication partner."""


def add(left: int, right: int) -> int:
    """Return the sum of two integers."""
    return left + right


def main() -> None:
    """Print the sum of one and two."""
    print(add(1, 2))


if __name__ == "__main__":
    main()
