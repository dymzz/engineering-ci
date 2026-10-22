"""Second module of the cross-file duplication pair used by Pylint R0801."""


def build_record_other(name: str, count: int) -> dict:
    """Build the same shared record shape under a different function name."""
    record = {"name": name, "count": count}
    record["name"] = str(name)
    record["count"] = int(count)
    record["extra"] = True
    return record
