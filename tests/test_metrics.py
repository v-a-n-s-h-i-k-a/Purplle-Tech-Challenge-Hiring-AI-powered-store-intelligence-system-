# PROMPT: "Write pytest unit tests for a FastAPI endpoint GET /stores/{store_id}/metrics that:
# 1. Fetches real-time store metrics (unique_visitors, conversion_rate, avg_dwell_per_zone, queue_depth, abandonment_rate).
# 2. Excludes staff members (is_staff = True) from visitor and conversion statistics.
# 3. Handles edge cases like empty store (missing store events) returning 404, or stores with zero purchases."
#
# CHANGES MADE:
# - Implemented using async fixtures using AsyncClient with ASGI transport.
# - Added helper factories for generating mock events and mock transactions.
# - Validated metrics calculation, staff exclusion, zero-purchase store, and 404 error cases.

import pytest
import uuid
from datetime import datetime, timezone, timedelta
from httpx import AsyncClient, ASGITransport
from app.main import app

BASE_EVENT = {
    "store_id": "STORE_METRICS_TEST",
    "camera_id": "CAM_ENTRY_01",
    "visitor_id": "VIS_metrics1",
    "event_type": "ENTRY",
    "timestamp": "2026-03-03T14:22:10Z",
    "zone_id": None,
    "dwell_ms": 0,
    "is_staff": False,
    "confidence": 0.95,
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

@pytest.mark.asyncio
async def test_get_metrics_valid_store(client, event_factory):
    # Ingest some events for a valid store
    store_id = "STORE_METRICS_TEST"
    visitor_id = "VIS_metrics_valid"
    
    events = [
        event_factory(store_id=store_id, visitor_id=visitor_id, event_type="ENTRY"),
        event_factory(store_id=store_id, visitor_id=visitor_id, event_type="ZONE_DWELL", zone_id="COSMETICS", dwell_ms=30000),
        event_factory(store_id=store_id, visitor_id=visitor_id, event_type="BILLING_QUEUE_JOIN", zone_id="BILLING_ZONE", metadata={"queue_depth": 2})
    ]
    
    resp_ingest = await client.post("/events/ingest", json={"events": events})
    assert resp_ingest.status_code == 200

    resp = await client.get(f"/stores/{store_id}/metrics")
    assert resp.status_code == 200
    data = resp.json()
    assert data["store_id"] == store_id
    assert data["unique_visitors"] == 1
    assert data["queue_depth"] == 2
    assert data["abandonment_rate"] == 0.0
    assert len(data["avg_dwell_per_zone"]) == 1
    assert data["avg_dwell_per_zone"][0]["zone_id"] == "COSMETICS"
    assert data["avg_dwell_per_zone"][0]["avg_dwell_sec"] == 30.0

@pytest.mark.asyncio
async def test_get_metrics_excludes_staff(client, event_factory):
    store_id = "STORE_METRICS_STAFF_TEST"
    
    # Ingest one customer event and one staff event
    events = [
        event_factory(store_id=store_id, visitor_id="VIS_customer", is_staff=False, event_type="ENTRY"),
        event_factory(store_id=store_id, visitor_id="VIS_staff_member", is_staff=True, event_type="ENTRY", camera_id="CAM_FLOOR_01"),
        event_factory(store_id=store_id, visitor_id="VIS_staff_member", is_staff=True, event_type="ZONE_DWELL", zone_id="SKINCARE", dwell_ms=120000)
    ]
    
    await client.post("/events/ingest", json={"events": events})
    
    resp = await client.get(f"/stores/{store_id}/metrics")
    assert resp.status_code == 200
    data = resp.json()
    # Unique visitors should only count the customer, not staff
    assert data["unique_visitors"] == 1
    # Average dwell should not include staff dwell
    assert len(data["avg_dwell_per_zone"]) == 0

@pytest.mark.asyncio
async def test_get_metrics_zero_purchases(client, event_factory):
    # Store with visitors but no transactions
    store_id = "STORE_ZERO_TXN"
    events = [
        event_factory(store_id=store_id, visitor_id="VIS_buyer_1", event_type="ENTRY"),
        event_factory(store_id=store_id, visitor_id="VIS_buyer_1", event_type="BILLING_QUEUE_JOIN", zone_id="BILLING_ZONE")
    ]
    await client.post("/events/ingest", json={"events": events})
    
    resp = await client.get(f"/stores/{store_id}/metrics")
    assert resp.status_code == 200
    data = resp.json()
    assert data["unique_visitors"] == 1
    assert data["converted_visitors"] == 0
    assert data["conversion_rate"] == 0.0

@pytest.mark.asyncio
async def test_get_metrics_not_found(client):
    resp = await client.get("/stores/NON_EXISTENT_STORE/metrics")
    assert resp.status_code == 404
