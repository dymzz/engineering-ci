"""Admit trace provenance without importing tracing/mining into the workflow core."""

from ..mining.bindings import bind


def selected_source(evidence, candidate_id):
    """Reuse the verified candidate admission; mapping stays an explicit user choice."""
    return bind(evidence, candidate_id)["source"]
