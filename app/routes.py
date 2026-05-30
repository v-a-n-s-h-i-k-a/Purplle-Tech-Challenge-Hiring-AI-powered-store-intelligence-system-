from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import datetime, date
from typing import List, Dict, Any
from app.database import get_db
from app.models import StoreEvent, ShoppingSession
from app.schemas import (
    EventIngest, EventResponse, SessionResponse,
    FunnelStep, HeatmapZone, AnomalyAlert
)
from app.services import Sessionizer, FunnelService, HeatmapService, AnomalyEngine

router = APIRouter(prefix="/api/v1")

# --- EVENT INGESTION ---

@router.post("/events/ingest", status_code=status.HTTP_201_CREATED)
def ingest_event(payload: EventIngest, db: Session = Depends(get_db)):
    """
    Receives raw events from the CCTV pipeline, logs them, and triggers
    the stateful Sessionizer to compute and compile active shopping sessions.
    """
    try:
        # Create and save raw event
        event = StoreEvent(
            event_type=payload.event_type,
            track_id=payload.track_id,
            zone_id=payload.zone_id,
            confidence=payload.confidence,
            metadata_json=payload.metadata
        )
        db.add(event)
        db.commit()
        db.refresh(event)
        
        # Trigger stateful session compiler
        Sessionizer.process_event(db, event)
        
        return {"status": "success", "event_id": event.id}
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to ingest event: {str(e)}"
        )

# --- ANALYTICS & BUSINESS INTELLIGENCE ---

@router.get("/analytics/metrics")
def get_store_metrics(db: Session = Depends(get_db)):
    """
    Exposes high-level real-time retail KPIs for the executive dashboard.
    """
    # 1. Live Occupancy & Active Staff
    active_sessions = db.query(ShoppingSession).filter(ShoppingSession.end_time == None).all()
    live_occupancy = sum(1 for s in active_sessions if not s.is_staff)
    active_staff = sum(1 for s in active_sessions if s.is_staff)
    
    # 2. Total Shoppers Today (Customers entered)
    today_start = datetime.combine(date.today(), datetime.min.time())
    total_shoppers_today = db.query(ShoppingSession).filter(
        ShoppingSession.is_staff == False,
        ShoppingSession.start_time >= today_start
    ).count()
    
    # 3. Average Shopper Dwell Time (Finished sessions)
    finished_sessions = db.query(ShoppingSession).filter(
        ShoppingSession.is_staff == False,
        ShoppingSession.end_time != None
    ).all()
    
    avg_dwell = 0.0
    if finished_sessions:
        avg_dwell = sum(s.duration_seconds for s in finished_sessions) / len(finished_sessions)
        
    # 4. Queue Depth (Currently in checkout queue)
    queue_depth = 0
    for s in active_sessions:
        if s.zones_visited:
            active_q = [z for z in s.zones_visited if z.get("zone_id") == "checkout_queue" and z.get("exit_time") is None]
            if active_q:
                queue_depth += 1
                
    # 5. Conversion Rate
    # Converted customers / Total entered customer sessions
    total_customers = db.query(ShoppingSession).filter(ShoppingSession.is_staff == False).count()
    converted_customers = 0
    all_customers = db.query(ShoppingSession).filter(ShoppingSession.is_staff == False).all()
    for s in all_customers:
        visited_zones = {z.get("zone_id") for z in s.zones_visited if z.get("zone_id")}
        if "checkout_queue" in visited_zones and s.end_time is not None:
            converted_customers += 1
            
    conversion_rate = 0.0
    if total_customers > 0:
        conversion_rate = (converted_customers / total_customers) * 100.0
        
    return {
        "live_occupancy": live_occupancy,
        "active_staff": active_staff,
        "total_shoppers_today": total_shoppers_today,
        "avg_dwell_seconds": round(avg_dwell, 1),
        "queue_depth": queue_depth,
        "conversion_rate": round(conversion_rate, 1)
    }

@router.get("/analytics/funnel", response_model=List[FunnelStep])
def get_funnel(db: Session = Depends(get_db)):
    """Returns the visual shopping conversion funnel layers."""
    return FunnelService.get_conversion_funnel(db)

@router.get("/analytics/heatmap", response_model=List[HeatmapZone])
def get_heatmap(db: Session = Depends(get_db)):
    """Returns spatial hot-traffic zone metrics and dwell times."""
    return HeatmapService.get_heatmap_data(db)

@router.get("/analytics/anomalies", response_model=List[AnomalyAlert])
def get_anomalies(db: Session = Depends(get_db)):
    """Runs rule heuristics and returns operational alerts."""
    return AnomalyEngine.detect_anomalies(db)

# --- SYSTEM HEALTHCHECK ---

@router.get("/health")
def get_health(db: Session = Depends(get_db)):
    """Confirms server availability and DB connection health."""
    try:
        # Trivial DB execution check
        db.execute(text("SELECT 1"))
        return {
            "status": "healthy",
            "db_connected": True,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Database connection degraded: {str(e)}"
        )
