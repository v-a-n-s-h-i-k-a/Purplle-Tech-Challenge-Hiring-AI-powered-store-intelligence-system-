from sqlalchemy.orm import Session
from datetime import datetime, time as datetime_time
from typing import List, Dict, Any, Optional
from app.models import StoreEvent, ShoppingSession, HourlyMetric
from app.schemas import FunnelStep, HeatmapZone, AnomalyAlert
import json

class Sessionizer:
    """
    Statefully rolls up raw events (entry, exit, zone transitions) into 
    consolidated customer/staff sessions.
    """
    
    @staticmethod
    def process_event(db: Session, event: StoreEvent) -> Optional[ShoppingSession]:
        """
        Ingests a raw StoreEvent, creates or updates the associated ShoppingSession.
        """
        # Find existing active session for this track
        session = db.query(ShoppingSession).filter(
            ShoppingSession.track_id == event.track_id,
            ShoppingSession.end_time == None
        ).first()
        
        is_staff_val = False
        if event.metadata_json and isinstance(event.metadata_json, dict):
            is_staff_val = event.metadata_json.get("is_staff", False)
            
        # 1. Handle Entry Event -> Start a new session
        if event.event_type == "entry":
            if not session:
                session = ShoppingSession(
                    track_id=event.track_id,
                    start_time=event.timestamp,
                    is_staff=is_staff_val,
                    zones_visited=[]
                )
                db.add(session)
                db.commit()
                db.refresh(session)
            return session
            
        # 2. Handle Zone Entry -> Add to zones_visited ledger
        elif event.event_type == "zone_entry":
            if not session:
                # Fallback if entry was missed
                session = ShoppingSession(
                    track_id=event.track_id,
                    start_time=event.timestamp,
                    is_staff=is_staff_val,
                    zones_visited=[]
                )
                db.add(session)
                db.commit()
                db.refresh(session)
                
            # Append zone entry record (deserialize JSON list first)
            visited = list(session.zones_visited) if session.zones_visited else []
            
            # Avoid duplicate entries for the same active zone entry
            active_entries = [z for z in visited if z.get("zone_id") == event.zone_id and z.get("exit_time") is None]
            if not active_entries:
                visited.append({
                    "zone_id": event.zone_id,
                    "entry_time": event.timestamp.isoformat(),
                    "exit_time": None,
                    "dwell_time": 0.0
                })
                session.zones_visited = visited
                session.is_staff = is_staff_val or session.is_staff
                db.commit()
                db.refresh(session)
            return session
            
        # 3. Handle Zone Exit -> Calculate dwell time and close zone record
        elif event.event_type == "zone_exit":
            if session:
                visited = list(session.zones_visited) if session.zones_visited else []
                # Find the unclosed entry for this zone
                for entry in reversed(visited):
                    if entry.get("zone_id") == event.zone_id and entry.get("exit_time") is None:
                        entry["exit_time"] = event.timestamp.isoformat()
                        # Calculate elapsed seconds
                        entry_dt = datetime.fromisoformat(entry["entry_time"])
                        dwell = (event.timestamp - entry_dt).total_seconds()
                        entry["dwell_time"] = max(0.0, dwell)
                        break
                session.zones_visited = visited
                db.commit()
                db.refresh(session)
            return session
            
        # 4. Handle Exit Event -> Finalize the session
        elif event.event_type == "exit":
            if session:
                session.end_time = event.timestamp
                session.duration_seconds = (event.timestamp - session.start_time).total_seconds()
                
                # Auto-close any unclosed zone records
                visited = list(session.zones_visited) if session.zones_visited else []
                for entry in visited:
                    if entry.get("exit_time") is None:
                        entry["exit_time"] = event.timestamp.isoformat()
                        entry_dt = datetime.fromisoformat(entry["entry_time"])
                        entry["dwell_time"] = max(0.0, (event.timestamp - entry_dt).total_seconds())
                        
                session.zones_visited = visited
                
                # Compute Primary Zone (zone with maximum total dwell time)
                zone_durations = {}
                for entry in visited:
                    zid = entry.get("zone_id")
                    dwell = entry.get("dwell_time", 0.0)
                    zone_durations[zid] = zone_durations.get(zid, 0.0) + dwell
                    
                if zone_durations:
                    session.primary_zone = max(zone_durations, key=zone_durations.get)
                    
                db.commit()
                db.refresh(session)
            return session
            
        return None


class FunnelService:
    """
    Calculates retail purchase funnel conversions:
    Entrants -> Cosmetics Browsers -> Checkout queue -> Purchase completion
    """
    
    @staticmethod
    def get_conversion_funnel(db: Session) -> List[FunnelStep]:
        # Exclude staff members for business conversion statistics
        customer_sessions = db.query(ShoppingSession).filter(ShoppingSession.is_staff == False).all()
        total_customers = len(customer_sessions)
        
        cosmetics_count = 0
        checkout_count = 0
        converted_count = 0
        
        for s in customer_sessions:
            visited_zones = {v.get("zone_id") for v in s.zones_visited if v.get("zone_id")}
            
            # Check Cosmetics entry
            if "cosmetics_aisle" in visited_zones:
                cosmetics_count += 1
                
            # Check Checkout queue entry
            if "checkout_queue" in visited_zones:
                checkout_count += 1
                
                # Converted: If they visited the checkout queue, and exited the store (completed shopping journey)
                if s.end_time is not None:
                    converted_count += 1
                    
        # Construct Funnel structure
        steps = [
            FunnelStep(
                step_name="Store Entrants (Total Shoppers)",
                count=total_customers,
                percentage_of_total=100.0 if total_customers > 0 else 0.0
            ),
            FunnelStep(
                step_name="Cosmetics Browsers",
                count=cosmetics_count,
                percentage_of_total=round((cosmetics_count / total_customers) * 100, 1) if total_customers > 0 else 0.0
            ),
            FunnelStep(
                step_name="Checkout Aisle (High Intent)",
                count=checkout_count,
                percentage_of_total=round((checkout_count / total_customers) * 100, 1) if total_customers > 0 else 0.0
            ),
            FunnelStep(
                step_name="Completed Purchases (Converted)",
                count=converted_count,
                percentage_of_total=round((converted_count / total_customers) * 100, 1) if total_customers > 0 else 0.0
            )
        ]
        return steps


class HeatmapService:
    """
    Compiles average dwell times and foot traffic stats across store zones.
    """
    
    @staticmethod
    def get_heatmap_data(db: Session) -> List[HeatmapZone]:
        customer_sessions = db.query(ShoppingSession).filter(ShoppingSession.is_staff == False).all()
        
        zones_config = {
            "cosmetics_aisle": "Cosmetics Aisle",
            "checkout_queue": "Checkout Queue"
        }
        
        results = []
        
        for zid, zname in zones_config.items():
            visitors = set()
            dwell_times = []
            
            for s in customer_sessions:
                for v in s.zones_visited:
                    if v.get("zone_id") == zid:
                        visitors.add(s.track_id)
                        dwell_times.append(v.get("dwell_time", 0.0))
            
            visitor_count = len(visitors)
            avg_dwell = sum(dwell_times) / len(dwell_times) if dwell_times else 0.0
            total_hours = sum(dwell_times) / 3600.0
            
            results.append(HeatmapZone(
                zone_id=zid,
                zone_name=zname,
                visitor_count=visitor_count,
                average_dwell_seconds=round(avg_dwell, 1),
                total_dwell_hours=round(total_hours, 3)
            ))
            
        return results


class AnomalyEngine:
    """
    Scans live event streams and shopping sessions to detect retail operational anomalies.
    """
    
    @staticmethod
    def detect_anomalies(db: Session) -> List[AnomalyAlert]:
        alerts = []
        now = datetime.now()
        
        # 1. Queue Bottleneck / Overflow
        # Active shoppers currently in the checkout queue (exit_time is None)
        active_sessions = db.query(ShoppingSession).filter(ShoppingSession.end_time == None).all()
        in_queue = 0
        for s in active_sessions:
            if s.zones_visited:
                active_visits = [z for z in s.zones_visited if z.get("zone_id") == "checkout_queue" and z.get("exit_time") is None]
                if active_visits:
                    in_queue += 1
                    
        if in_queue > 2:
            alerts.append(AnomalyAlert(
                anomaly_type="queue_overflow",
                severity="critical",
                timestamp=now,
                description=f"Queue Alert! Checkout queue has {in_queue} active shoppers. Bottleneck detected.",
                metadata={"queue_count": in_queue, "threshold": 2}
            ))
            
        # 2. Customer Loitering Alert
        # Shopper stays in a zone for an unusually long time (threshold: 15 seconds for simulation testing)
        for s in active_sessions:
            if s.is_staff:
                continue
            if s.zones_visited:
                for z in s.zones_visited:
                    if z.get("exit_time") is None:
                        entry_dt = datetime.fromisoformat(z["entry_time"])
                        dwell_time = (now - entry_dt).total_seconds()
                        if dwell_time > 15.0:
                            alerts.append(AnomalyAlert(
                                anomaly_type="customer_loitering",
                                severity="warning",
                                timestamp=now,
                                description=f"Shopper ID {s.track_id} loitering in {z['zone_id']} for {int(dwell_time)}s.",
                                affected_id=s.track_id,
                                metadata={"zone_id": z["zone_id"], "dwell_time": dwell_time}
                            ))
                            
        # 3. Off-Hours Operational Entry
        # Entry events occurring during closed store hours (e.g. 10:00 PM to 6:00 AM)
        off_hours_events = db.query(StoreEvent).filter(
            StoreEvent.event_type == "entry"
        ).all()
        
        for e in off_hours_events:
            t = e.timestamp.time()
            if t > datetime_time(22, 0) or t < datetime_time(6, 0):
                alerts.append(AnomalyAlert(
                    anomaly_type="off_hours_entry",
                    severity="critical",
                    timestamp=e.timestamp,
                    description=f"Security Alert! Off-hours entry detected by Track ID {e.track_id} at {e.timestamp.strftime('%H:%M:%S')}.",
                    affected_id=e.track_id,
                    metadata={"time_detected": e.timestamp.isoformat()}
                ))
                
        return alerts
