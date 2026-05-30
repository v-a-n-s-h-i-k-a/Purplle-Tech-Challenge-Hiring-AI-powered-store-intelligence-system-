import requests
import uuid
import sys
from datetime import datetime, timezone

# Target API URL
API_URL = "http://localhost:8000"

def run_assertions():
    print("==================================================================")
    print("         PURPLLE STORE INTELLIGENCE API - SUBMISSION GATE TESTS   ")
    print("==================================================================")
    
    passed_tests = 0
    total_tests = 10

    # Test Helper
    def assert_test(name, condition, detail=""):
        nonlocal passed_tests
        if condition:
            passed_tests += 1
            print(f" [PASS] Test {passed_tests}/{total_tests}: {name}")
        else:
            print(f" [FAIL] Test: {name}")
            if detail:
                print(f"        Detail: {detail}")

    # 1. GET /health
    try:
        r = requests.get(f"{API_URL}/health", timeout=3)
        assert_test("Health Check Endpoint Status", r.status_code == 200, f"Expected 200, got {r.status_code}")
    except Exception as e:
        assert_test("Health Check Endpoint Status", False, f"Connection failed: {e}")

    # Create dummy events for testing
    visitor_id = f"VIS_{uuid.uuid4().hex[:6]}"
    event_id1 = str(uuid.uuid4())
    event_id2 = str(uuid.uuid4())
    
    events_payload = [
        {
            "event_id": event_id1,
            "store_id": "STORE_BLR_002",
            "camera_id": "CAM_ENTRY_01",
            "visitor_id": visitor_id,
            "event_type": "ENTRY",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "zone_id": None,
            "dwell_ms": 0,
            "is_staff": False,
            "confidence": 0.95,
            "metadata": {"session_seq": 1}
        },
        {
            "event_id": event_id2,
            "store_id": "STORE_BLR_002",
            "camera_id": "CAM_BILLING_01",
            "visitor_id": visitor_id,
            "event_type": "BILLING_QUEUE_JOIN",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "zone_id": "BILLING_ZONE",
            "dwell_ms": 0,
            "is_staff": False,
            "confidence": 0.91,
            "metadata": {"queue_depth": 3, "session_seq": 2}
        }
    ]

    # 2. POST /events/ingest - Ingestion Success
    try:
        r = requests.post(f"{API_URL}/events/ingest", json={"events": events_payload}, timeout=3)
        assert_test("Event Ingestion Connection", r.status_code == 200, f"Expected 200, got {r.status_code}")
        if r.status_code == 200:
            res = r.json()
            assert_test("Ingestion Accepted Count", res.get("accepted") == 2, f"Expected 2 accepted, got {res.get('accepted')}")
        else:
            assert_test("Ingestion Accepted Count", False)
    except Exception as e:
        assert_test("Event Ingestion Connection", False, f"Connection failed: {e}")
        assert_test("Ingestion Accepted Count", False)

    # 4. Ingest Idempotency Check (Duplicate POST)
    try:
        r = requests.post(f"{API_URL}/events/ingest", json={"events": events_payload}, timeout=3)
        res = r.json()
        assert_test(
            "Ingestion Idempotency Check", 
            res.get("accepted") == 0 and res.get("duplicate") == 2, 
            f"Expected accepted=0, duplicate=2. Got accepted={res.get('accepted')}, duplicate={res.get('duplicate')}"
        )
    except Exception as e:
        assert_test("Ingestion Idempotency Check", False, f"Request failed: {e}")

    # 5. POST /events/ingest - Malformed payload (Missing visitor_id)
    invalid_payload = [{
        "event_id": str(uuid.uuid4()),
        "store_id": "STORE_BLR_002",
        "camera_id": "CAM_ENTRY_01",
        # visitor_id missing
        "event_type": "ENTRY",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "confidence": 0.95
    }]
    try:
        r = requests.post(f"{API_URL}/events/ingest", json={"events": invalid_payload}, timeout=3)
        # Fastapi returns 422 for pydantic schema validation failure
        assert_test("Pydantic Schema Rejection", r.status_code == 422, f"Expected 422 validation failure, got {r.status_code}")
    except Exception as e:
        assert_test("Pydantic Schema Rejection", False, f"Request failed: {e}")

    # 6. GET /stores/{id}/metrics
    try:
        r = requests.get(f"{API_URL}/stores/STORE_BLR_002/metrics", timeout=3)
        assert_test("Store Metrics Endpoint Status", r.status_code == 200, f"Expected 200, got {r.status_code}")
        if r.status_code == 200:
            res = r.json()
            assert_test("Metrics Schema unique_visitors key", "unique_visitors" in res, "Missing unique_visitors key")
            assert_test("Metrics Schema conversion_rate key", "conversion_rate" in res, "Missing conversion_rate key")
        else:
            assert_test("Metrics Schema unique_visitors key", False)
            assert_test("Metrics Schema conversion_rate key", False)
    except Exception as e:
        assert_test("Store Metrics Endpoint Status", False, f"Connection failed: {e}")
        assert_test("Metrics Schema unique_visitors key", False)
        assert_test("Metrics Schema conversion_rate key", False)

    # 9. GET /stores/{id}/funnel
    try:
        r = requests.get(f"{API_URL}/stores/STORE_BLR_002/funnel", timeout=3)
        assert_test("Store Funnel Endpoint Status", r.status_code == 200, f"Expected 200, got {r.status_code}")
    except Exception as e:
        assert_test("Store Funnel Endpoint Status", False, f"Connection failed: {e}")

    # 10. GET /stores/{id}/heatmap
    try:
        r = requests.get(f"{API_URL}/stores/STORE_BLR_002/heatmap", timeout=3)
        assert_test("Store Heatmap Endpoint Status", r.status_code == 200, f"Expected 200, got {r.status_code}")
    except Exception as e:
        assert_test("Store Heatmap Endpoint Status", False, f"Connection failed: {e}")

    print("==================================================================")
    print(f" ASSERTIONS RESULTS: Passed {passed_tests}/{total_tests} tests.")
    print("==================================================================")
    
    if passed_tests == total_tests:
        print("  [SUCCESS] CONGRATULATIONS! Your submission fully satisfies the acceptance gate!")
        sys.exit(0)
    else:
        print("  [FAIL] WARNING: Some assertions failed. Please verify API execution.")
        sys.exit(1)

if __name__ == "__main__":
    run_assertions()
