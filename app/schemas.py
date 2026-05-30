from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, Dict, List, Any

# --- API INGEST SCHEMAS ---

class EventIngest(BaseModel):
    """Payload schema for registering a raw store event."""
    event_type: str = Field(..., description="Type of event: entry, exit, zone_entry, zone_exit, anomaly")
    track_id: int = Field(..., description="Unique track ID assigned by CV tracker")
    zone_id: Optional[str] = Field(None, description="Affected zone identifier")
    confidence: float = Field(1.0, description="Tracking confidence level (0.0 to 1.0)")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Metadata key-value pairs")

class EventResponse(BaseModel):
    """Schema for returning registered raw store events."""
    id: int
    event_type: str
    track_id: int
    timestamp: datetime
    zone_id: Optional[str]
    confidence: float
    metadata_json: Optional[Dict[str, Any]]

    class Config:
        from_attributes = True

# --- STATE SCHEMAS ---

class SessionResponse(BaseModel):
    """Schema representing an aggregated shopper session."""
    id: int
    track_id: int
    start_time: datetime
    end_time: Optional[datetime]
    is_staff: bool
    primary_zone: Optional[str]
    duration_seconds: float
    zones_visited: List[Dict[str, Any]]

    class Config:
        from_attributes = True

class HourlyMetricsResponse(BaseModel):
    """Hourly analytical metric summary."""
    id: int
    hour: datetime
    total_entries: int
    total_exits: int
    staff_count: int
    avg_dwell_time: float
    queue_conversion_rate: float
    zone_distribution: Dict[str, int]

    class Config:
        from_attributes = True

# --- ANALYTICS CUSTOM SCHEMAS ---

class FunnelStep(BaseModel):
    """Represents a single layer in the retail conversion funnel."""
    step_name: str = Field(..., description="Stage name (e.g. Total Visitors, Checkout Queue)")
    count: int = Field(..., description="Number of visitors reaching this stage")
    percentage_of_total: float = Field(..., description="Stage conversion rate relative to top-of-funnel")

class HeatmapZone(BaseModel):
    """Zone spatial dwell analytics."""
    zone_id: str
    zone_name: str
    visitor_count: int
    average_dwell_seconds: float
    total_dwell_hours: float

class AnomalyAlert(BaseModel):
    """Triggered security/operational warning."""
    anomaly_type: str = Field(..., description="Type: queue_overflow, customer_loitering, off_hours_entry")
    severity: str = Field("warning", description="warning, critical")
    timestamp: datetime
    description: str
    affected_id: Optional[int] = Field(None, description="Track ID or other entity")
    metadata: Dict[str, Any]
