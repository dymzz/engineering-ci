"""Module carrying one confirmed dead function for the Vulture negative case."""


def used_function() -> int:
    """Return a constant that the entry point consumes."""
    return 1


def never_called_anywhere() -> str:
    """Return text no reachable statement references."""
    return "unused"


def main() -> None:
    """Print the used helper's result."""
    print(used_function())


if __name__ == "__main__":
    main()
