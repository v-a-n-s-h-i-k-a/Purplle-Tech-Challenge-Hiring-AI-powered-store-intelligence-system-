import json
from app.models import StoreEvent, IngestResponse

async def ingest_events(conn, events: list[StoreEvent]) -> IngestResponse:
    accepted = rejected = duplicate = 0
    errors = []

    for event in events:
        try:
            if not (0.0 <= event.confidence <= 1.0):
                raise ValueError("confidence must be between 0.0 and 1.0")
            result = await conn.fetchval("""
                INSERT INTO events (
                    event_id, store_id, camera_id, visitor_id,
                    event_type, timestamp, zone_id, dwell_ms,
                    is_staff, confidence, metadata
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                ON CONFLICT (event_id) DO NOTHING
                RETURNING event_id
            """,
                event.event_id,
                event.store_id,
                event.camera_id,
                event.visitor_id,
                event.event_type,
                event.timestamp,
                event.zone_id,
                event.dwell_ms,
                event.is_staff,
                event.confidence,
                json.dumps(event.metadata.dict()),
            )
            if result:
                accepted += 1
            else:
                duplicate += 1
        except Exception as e:
            rejected += 1
            errors.append({"event_id": str(event.event_id), "error": str(e)})

    return IngestResponse(
        accepted=accepted,
        rejected=rejected,
        duplicate=duplicate,
        errors=errors
    )
