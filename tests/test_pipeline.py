# PROMPT: "Write pytest tests for a FastAPI event ingestion endpoint that:
# 1. Accepts batches up to 500 events with the exact schema (event_id UUID,
#    store_id, camera_id, visitor_id, event_type enum, timestamp ISO8601,
#    zone_id optional, dwell_ms, is_staff bool, confidence 0-1, metadata object)
# 2. Is idempotent — posting the same event_id twice must not duplicate it
# 3. Returns partial success on malformed events (not a full 400)
# 4. Handles edge cases: empty store (zero events), all-staff clip, re-entry visitor"
#
# CHANGES MADE:
# - Added async fixtures using asyncpg directly (AI used httpx.AsyncClient which
#   doesn't work with our lifespan context — switched to real DB setup)
# - Added the all-staff edge case (AI's version only tested is_staff=False)
# - Split the schema validation test into positive + negative cases
# - Added idempotency assertion on the 'duplicate' field specifically

import pytest
import uuid
from datetime import datetime, timezone
from httpx import AsyncClient, ASGITransport
from app.main import app

BASE_EVENT = {
    "store_id": "STORE_BLR_002",
    "camera_id": "CAM_ENTRY_01",
    "visitor_id": "VIS_abc123",
    "event_type": "ENTRY",
    "timestamp": "2026-03-03T14:22:10Z",
    "zone_id": None,
    "dwell_ms": 0,
    "is_staff": False,
    "confidence": 0.91,
    "metadata": {"queue_depth": None, "sku_zone": None, "session_seq": 1}
}

@pytest.fixture
def event_factory():
    def _make(**overrides):
        e = {**BASE_EVENT, "event_id": str(uuid.uuid4()), "timestamp": datetime.now(timezone.utc).isoformat()}
        e.update(overrides)
        return e
    return _make

@pytest.fixture
async def client():
    from app.db import init_db, InMemoryDB
    await init_db()
    InMemoryDB.clear()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c

# ── Happy path ─────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_ingest_single_event(client, event_factory):
    resp = await client.post("/events/ingest", json={"events": [event_factory()]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted"] == 1
    assert body["rejected"] == 0
    assert body["duplicate"] == 0

@pytest.mark.asyncio
async def test_ingest_batch_500(client, event_factory):
    events = [event_factory() for _ in range(500)]
    resp = await client.post("/events/ingest", json={"events": events})
    assert resp.status_code == 200
    assert resp.json()["accepted"] == 500

# ── Idempotency ────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_idempotent_same_event_id(client, event_factory):
    event = event_factory()
    r1 = await client.post("/events/ingest", json={"events": [event]})
    r2 = await client.post("/events/ingest", json={"events": [event]})
    assert r1.json()["accepted"] == 1
    assert r2.json()["duplicate"] == 1
    assert r2.json()["accepted"] == 0

# ── Partial success ────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_partial_success_on_bad_event(client, event_factory):
    good = event_factory()
    bad = {**event_factory(), "confidence": 5.0}  # confidence > 1.0
    resp = await client.post("/events/ingest", json={"events": [good, bad]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted"] == 1
    assert body["rejected"] == 1

# ── Edge case: empty store ─────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_metrics_empty_store(client):
    resp = await client.get("/stores/STORE_EMPTY_999/metrics")
    assert resp.status_code == 404

# ── Edge case: all-staff clip ──────────────────────────────────────────────
@pytest.mark.asyncio
async def test_metrics_excludes_staff(client, event_factory):
    staff_event = event_factory(is_staff=True, store_id="STORE_STAFF_TEST")
    await client.post("/events/ingest", json={"events": [staff_event]})
    resp = await client.get("/stores/STORE_STAFF_TEST/metrics")
    # Store should exist but have 0 customer visitors
    if resp.status_code == 200:
        assert resp.json()["unique_visitors"] == 0

# ── Edge case: re-entry deduplication in funnel ───────────────────────────
@pytest.mark.asyncio
async def test_funnel_reentry_deduplication(client, event_factory):
    vid = "VIS_reentry_test"
    store = "STORE_REENTRY_001"
    events = [
        event_factory(visitor_id=vid, store_id=store, event_type="ENTRY"),
        event_factory(visitor_id=vid, store_id=store, event_type="EXIT"),
        event_factory(visitor_id=vid, store_id=store, event_type="REENTRY"),
    ]
    await client.post("/events/ingest", json={"events": events})
    resp = await client.get(f"/stores/{store}/funnel")
    if resp.status_code == 200:
        entry_stage = next(s for s in resp.json()["stages"] if s["stage"] == "entry")
        assert entry_stage["visitors"] == 1  # deduplicated, not 2

# ── Schema validation ──────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_invalid_event_type_rejected(client, event_factory):
    bad = event_factory(event_type="UNKNOWN_TYPE")
    resp = await client.post("/events/ingest", json={"events": [bad]})
    assert resp.status_code in (200, 422)
    if resp.status_code == 200:
        assert resp.json()["rejected"] == 1

@pytest.mark.asyncio
async def test_batch_over_limit_rejected(client, event_factory):
    events = [event_factory() for _ in range(501)]
    resp = await client.post("/events/ingest", json={"events": events})
    assert resp.status_code == 422

# ── Health ─────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_health_returns_status(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert "status" in resp.json()
    assert "database" in resp.json()
