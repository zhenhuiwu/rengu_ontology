"""Narrow offline CDC event mapping/ingress/outbox checks. No DB, HTTP or workers."""

import asyncio
import copy
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.errors import AppError
from app.modules.action.models import TypeStatus
from app.modules.data import cdc_events as cdc
from app.modules.event.ingress import validate_fields
from app.modules.event.models import EventSource

SOURCE = Path(cdc.__file__).read_text(encoding="utf-8")


async def main():
    mapping = cdc.EventMapping(
        eventTypeId="EV-TEST",
        eventVersion="v1",
        operations=["update"],
        fields=[{"target": "score", "source": "after.score"}, {"target": "operation", "source": "op"}],
    )
    fields = [
        SimpleNamespace(api="score", name="Score", type="整数", required=True, schema=None),
        SimpleNamespace(api="operation", name="Operation", type="字符串", required=True, schema=None),
    ]
    definition = SimpleNamespace(
        status=TypeStatus.published,
        source=EventSource.sync,
        obj_kind="Device",
        version="v1",
        retired_release_id=None,
        payload=fields,
    )
    cdc.validate_mapping(mapping, definition, "Device", ["id", "score"])
    event = {
        "eventKey": "uuid:bin.000001:120:0",
        "op": "update",
        "before": {"id": 1, "score": 10},
        "after": {"id": 2, "score": 90},
        "position": {"file": "bin.000001", "position": 120},
        "at": "2026-11-03T00:00:00+00:00",
    }
    body = cdc.envelope(mapping, event, ontology_id=8, object_code="Device", primary="id")
    numeric = mapping.model_copy(deep=True)
    numeric._field_types["score"] = "小数"
    decimal_event = {
        **event,
        "after": {"id": 2, "score": "0.1"},
        "position": {**event["position"], "decimalPaths": ["after.score"]},
    }
    decimal_body = cdc.envelope(numeric, decimal_event, ontology_id=8, object_code="Device", primary="id")
    assert type(decimal_body.payload["score"]) is float and decimal_body.payload["score"] == 0.1
    assert (
        cdc.envelope(
            numeric,
            {**decimal_event, "position": event["position"]},
            ontology_id=8,
            object_code="Device",
            primary="id",
        ).payload["score"]
        == "0.1"
    )
    try:
        cdc.envelope(
            numeric,
            {**decimal_event, "after": {"id": 2, "score": "0.10000000000000000001"}},
            ontology_id=8,
            object_code="Device",
            primary="id",
        )
    except AppError as error:
        assert error.code == "CDC_EVENT_MAPPING_INVALID"
    else:
        raise AssertionError("decimal precision was silently lost")
    assert body.object_id == "2" and body.payload == {"score": 90, "operation": "update"}
    assert (
        len(body.event_id) == 64
        and body.event_id
        == cdc.envelope(
            mapping, copy.deepcopy(event), ontology_id=8, object_code="Device", primary="id"
        ).event_id
    )
    assert (
        body.event_id
        != cdc.envelope(mapping, event, ontology_id=9, object_code="Device", primary="id").event_id
    )
    assert (
        cdc.envelope(mapping, {**event, "op": "delete"}, ontology_id=8, object_code="Device", primary="id")
        is None
    )
    for changed in (dict(version="v2"), dict(obj_kind="Other"), dict(source=EventSource.external)):
        bad_definition = copy.copy(definition)
        for key, value in changed.items():
            setattr(bad_definition, key, value)
        try:
            cdc.validate_mapping(mapping, bad_definition, "Device", ["id", "score"])
        except AppError:
            pass
        else:
            raise AssertionError("changed event definition accepted")
    invalid = mapping.model_copy(deep=True)
    invalid.fields[0].source = "after.private"
    try:
        cdc.validate_mapping(invalid, definition, "Device", ["id", "score"])
    except AppError:
        pass
    else:
        raise AssertionError("unapproved source column accepted")
    calls = []

    async def receive(session, accepted):
        validate_fields(fields, accepted.payload)
        calls.append(("inbox", accepted.event_id))

    async def discover(session, event_id):
        calls.append(("outbox", event_id))

    sub = SimpleNamespace(ontology_id=8, object_code="Device", capture={"primaryKey": "id"})
    with patch.object(cdc, "receive", receive), patch.object(cdc, "discover", discover):
        await cdc.emit(object(), sub, mapping, event)
        assert calls == [("inbox", body.event_id), ("outbox", body.event_id)]
        calls.clear()
        try:
            await cdc.emit(object(), sub, mapping, {**event, "after": {"id": 2, "score": "90"}})
        except AppError as exc:
            assert exc.code == "CDC_EVENT_MAPPING_INVALID"
        else:
            raise AssertionError("strict ingress was bypassed")
        assert not calls
    with patch.object(cdc, "receive", AsyncMock()) as receive_mock:
        await cdc.emit(object(), sub, None, event)
        receive_mock.assert_not_awaited()
    assert "session.commit(" not in SOURCE  # Caller atomically commits facts, outbox and cursor.
    print(
        "CDC event offline check passed: exact schema/version/scope, stable IDs, "
        "inbox→outbox, no implicit emit."
    )


if __name__ == "__main__":
    asyncio.run(main())
