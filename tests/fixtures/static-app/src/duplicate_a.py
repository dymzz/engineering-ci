"""First module of the cross-file duplication pair used by Pylint R0801."""


def build_record(name: str, count: int) -> dict:
    """Build the shared record shape that the second module copies."""
    record = {"name": name, "count": count}
    record["name"] = str(name)
    record["count"] = int(count)
    record["extra"] = True
    return record
