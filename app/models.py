from pydantic import BaseModel, Field, validator
from typing import Optional, Literal
from datetime import datetime
from uuid import UUID
import uuid

EventType = Literal[
    "ENTRY", "EXIT", "ZONE_ENTER", "ZONE_EXIT",
    "ZONE_DWELL", "BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON", "REENTRY"
]

class EventMetadata(BaseModel):
    queue_depth: Optional[int] = None
    sku_zone: Optional[str] = None
    session_seq: Optional[int] = None

class StoreEvent(BaseModel):
    event_id: UUID = Field(default_factory=uuid.uuid4)
    store_id: str
    camera_id: str
    visitor_id: str
    event_type: EventType
    timestamp: datetime
    zone_id: Optional[str] = None
    dwell_ms: int = 0
    is_staff: bool = False
    confidence: float
    metadata: EventMetadata = Field(default_factory=EventMetadata)

    @validator("zone_id")
    def zone_required_for_zone_events(cls, v, values):
        event_type = values.get("event_type", "")
        if event_type in ("ZONE_ENTER", "ZONE_EXIT", "ZONE_DWELL", "BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON"):
            if v is None:
                raise ValueError(f"zone_id required for event_type {event_type}")
        return v

class IngestRequest(BaseModel):
    events: list[StoreEvent] = Field(..., max_items=500)

class IngestResponse(BaseModel):
    accepted: int
    rejected: int
    duplicate: int
    errors: list[dict] = []
