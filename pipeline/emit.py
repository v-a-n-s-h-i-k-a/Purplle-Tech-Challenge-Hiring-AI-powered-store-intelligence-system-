import uuid, json, requests
from datetime import datetime

class EventEmitter:
    def __init__(self, api_url: str, store_id: str, camera_id: str, clip_start: datetime):
        self.api_url = api_url.rstrip("/")
        self.store_id = store_id
        self.camera_id = camera_id
        self.clip_start = clip_start
        self._buffer: list[dict] = []
        self.total_emitted = 0

    def emit(self, event_type: str, visitor_id: str, zone_id: str | None,
             dwell_ms: int, is_staff: bool, confidence: float,
             timestamp: datetime, session_seq: int,
             extra_metadata: dict | None = None):

        event = {
            "event_id": str(uuid.uuid4()),
            "store_id": self.store_id,
            "camera_id": self.camera_id,
            "visitor_id": visitor_id,
            "event_type": event_type,
            "timestamp": timestamp.isoformat(),
            "zone_id": zone_id,
            "dwell_ms": dwell_ms,
            "is_staff": is_staff,
            "confidence": round(confidence, 4),
            "metadata": {
                "queue_depth": extra_metadata.get("queue_depth") if extra_metadata else None,
                "sku_zone": zone_id,
                "session_seq": session_seq,
            }
        }
        self._buffer.append(event)
        if len(self._buffer) >= 100:
            self.flush()

    def flush(self, force: bool = False):
        if not self._buffer:
            return
        try:
            resp = requests.post(
                f"{self.api_url}/events/ingest",
                json={"events": self._buffer},
                timeout=10
            )
            resp.raise_for_status()
            result = resp.json()
            self.total_emitted += result.get("accepted", 0)
            print(f"Flushed {len(self._buffer)} events -> accepted={result.get('accepted')}, "
                  f"dup={result.get('duplicate')}, rejected={result.get('rejected')}")
        except Exception as e:
            print(f"Flush error: {e}")
            # Write to fallback JSONL so events aren't lost
            with open("events_fallback.jsonl", "a") as f:
                for ev in self._buffer:
                    f.write(json.dumps(ev) + "\n")
        finally:
            self._buffer = []
