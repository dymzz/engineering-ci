"""CloudEvents SDK bindings for byte-preserving native records."""

from __future__ import annotations

import json
from datetime import datetime


def cloud_event(identity, record):
    """Use stable source/position IDs and only the source's actual event time."""
    from cloudevents.core.v1.event import CloudEvent

    attributes = {
        "specversion": "1.0",
        "source": identity.source,
        "id": record.event_id,
        "type": "engineering_ci.codex.rollout.fragment"
        if record.fragment
        else "engineering_ci.codex.rollout.record",
        "datacontenttype": "application/octet-stream",
        "recordoffset": str(record.offset),
        "payloadhash": record.digest,
        "parsestatus": record.parse_status,
        "sourceformat": identity.format,
    }
    if record.timestamp:
        attributes["time"] = datetime.fromisoformat(record.timestamp)
    event = CloudEvent(attributes, record.raw)
    if not record.timestamp:
        # SDK supplies current time by default; an unknown source time must stay absent.
        event.get_attributes().pop("time", None)
    return event


def attributes(identity, record):
    """Return JSON-safe attributes for jsonb; raw data belongs in bytea."""
    from cloudevents.core.formats.json import JSONFormat

    value = json.loads(JSONFormat().write(cloud_event(identity, record)))
    value.pop("data_base64", None)
    return value


def structured(identity, record):
    """Export explicit raw content using the standard data_base64 format."""
    from cloudevents.core.formats.json import JSONFormat

    return JSONFormat().write(cloud_event(identity, record))
