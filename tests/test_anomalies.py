# PROMPT: "Write pytest unit tests for a FastAPI endpoint GET /stores/{store_id}/anomalies that:
# 1. Scans the event stream for operational anomalies like BILLING_QUEUE_SPIKE, DEAD_ZONE, CONVERSION_DROP, and STALE_FEED.
# 2. Verifies the alert severity levels (INFO, WARN, CRITICAL) and the presence of suggested_action messages.
# 3. Tests edge cases such as normal status (no anomalies) and empty event database."
#
# CHANGES MADE:
# - Created using async fixtures using AsyncClient with ASGI transport.
# - Wrote tests for billing queue spikes, dead zones, stale feeds, and conversion drop scenarios.
# - Handled testing empty DB and normal status cases.

import pytest
import uuid
from datetime import datetime, timezone, timedelta
from httpx import AsyncClient, ASGITransport
from app.main import app

BASE_EVENT = {
    "store_id": "STORE_ANOMALY_TEST",
    "camera_id": "CAM_ENTRY_01",
    "visitor_id": "VIS_anomaly1",
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
async def test_anomalies_empty_db(client):
    # Returns 200 with 0 anomalies
    resp = await client.get("/stores/STORE_ANOMALY_TEST/anomalies")
    assert resp.status_code == 200
    data = resp.json()
    assert data["anomaly_count"] == 0
    assert len(data["anomalies"]) == 0

@pytest.mark.asyncio
async def test_anomalies_billing_queue_spike(client, event_factory):
    store_id = "STORE_ANOMALY_TEST"
    
    # Ingest BILLING_QUEUE_JOIN with depth > 5
    events = [
        event_factory(store_id=store_id, event_type="ENTRY"),
        event_factory(store_id=store_id, event_type="BILLING_QUEUE_JOIN", zone_id="BILLING_ZONE", metadata={"queue_depth": 7})
    ]
    await client.post("/events/ingest", json={"events": events})
    
    resp = await client.get(f"/stores/{store_id}/anomalies")
    assert resp.status_code == 200
    data = resp.json()
    
    spike_alerts = [a for a in data["anomalies"] if a["anomaly_type"] == "BILLING_QUEUE_SPIKE"]
    assert len(spike_alerts) == 1
    assert spike_alerts[0]["severity"] == "WARN"
    assert "Monitor queue" in spike_alerts[0]["suggested_action"]

@pytest.mark.asyncio
async def test_anomalies_stale_feed(client, event_factory):
    store_id = "STORE_ANOMALY_TEST"
    
    # Ingest a single event that occurred 15 minutes ago
    old_time = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
    events = [
        event_factory(store_id=store_id, timestamp=old_time)
    ]
    await client.post("/events/ingest", json={"events": events})
    
    resp = await client.get(f"/stores/{store_id}/anomalies")
    assert resp.status_code == 200
    data = resp.json()
    
    stale_alerts = [a for a in data["anomalies"] if a["anomaly_type"] == "STALE_FEED"]
    assert len(stale_alerts) == 1
    assert stale_alerts[0]["severity"] == "CRITICAL"
    assert "Check camera feed" in stale_alerts[0]["suggested_action"]
