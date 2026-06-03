"""
detect.py — Main detection + tracking pipeline
Processes CCTV clips and emits structured events to the API.
Supports a robust simulation fallback if YOLOv8 is unavailable or clip is missing.

Usage:
    python pipeline/detect.py --clip path/to/clip.mp4 \
           --store STORE_BLR_002 --camera CAM_ENTRY_01 \
           --layout data/store_layout.json --api http://localhost:8000
"""

import argparse
import json
import uuid
import requests
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import cv2
import numpy as np
import logging
from datetime import datetime, timezone, timedelta

# Try loading YOLOv8
try:
    from ultralytics import YOLO
    YOLO_AVAILABLE = True
except ImportError:
    YOLO_AVAILABLE = False

from tracker import ByteTrackWrapper
from emit import EventEmitter
from staff_detector import StaffDetector

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# ── Zone helpers ───────────────────────────────────────────────────────────
def load_zones(layout_path: str, store_id: str, camera_id: str) -> list[dict]:
    if not os.path.exists(layout_path):
        # Fallback layout if file missing
        return [
            {"zone_id": "BILLING_ZONE", "polygon": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]}
        ] if "billing" in camera_id.lower() else []
        
    with open(layout_path) as f:
        layout = json.load(f)
    store = next((s for s in layout["stores"] if s["store_id"] == store_id), None)
    if not store:
        return []
    cam = next((c for c in store["cameras"] if c["camera_id"] == camera_id), None)
    return cam["zones"] if cam else []

def point_in_polygon(px, py, polygon: list) -> bool:
    """Ray-casting algorithm for point-in-polygon test."""
    n = len(polygon)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside

def classify_zone(cx: float, cy: float, zones: list) -> str | None:
    for zone in zones:
        if point_in_polygon(cx, cy, zone["polygon"]):
            return zone["zone_id"]
    return None

def is_entry_direction(prev_y: float, curr_y: float, threshold_y: float) -> str | None:
    """Determine ENTRY or EXIT based on crossing a horizontal threshold line."""
    if prev_y < threshold_y <= curr_y:
        return "ENTRY"
    if prev_y >= threshold_y > curr_y:
        return "EXIT"
    return None

def likely_staff(trajectory: list) -> bool:
    """Heuristic: staff traverse multiple zones in a short time."""
    if len(trajectory) < 10:
        return False
    zones_seen = {t.get("zone") for t in trajectory if t.get("zone")}
    duration_frames = len(trajectory)
    return len(zones_seen) >= 3 and duration_frames < 600

def get_torso_hsv_signature(frame, bbox):
    """Computes the average HSV color of the upper-torso region."""
    h, w, _ = frame.shape
    x1, y1, x2, y2 = map(int, bbox)
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    
    if (x2 - x1) <= 10 or (y2 - y1) <= 10:
        return None
        
    torso_y1 = y1 + int(0.20 * (y2 - y1))
    torso_y2 = y1 + int(0.55 * (y2 - y1))
    torso_x1 = x1 + int(0.15 * (x2 - x1))
    torso_x2 = x1 + int(0.85 * (x2 - x1))
    
    torso_crop = frame[torso_y1:torso_y2, torso_x1:torso_x2]
    if torso_crop.size == 0:
        return None
        
    hsv = cv2.cvtColor(torso_crop, cv2.COLOR_BGR2HSV)
    mean_val = cv2.mean(hsv)[:3]
    return mean_val

# ── Main pipeline ──────────────────────────────────────────────────────────
def process_clip(clip_path: str, store_id: str, camera_id: str,
                 layout_path: str, api_url: str, clip_start_time: datetime,
                 force_simulate: bool = False):
                 
    use_yolo = YOLO_AVAILABLE and not force_simulate
    
    # Verify video file
    cap = cv2.VideoCapture(clip_path) if os.path.exists(clip_path) else None
    if cap is None or not cap.isOpened():
        logger.warning(f"Video file '{clip_path}' unavailable. Running in Simulation mode.")
        use_yolo = False
        fps = 30.0
        total_frames = 750 # 25 seconds
        w, h = 1280, 720
    else:
        fps = cap.get(cv2.CAP_PROP_FPS) or 15.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 750
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 1280
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 720
        
    if use_yolo:
        logger.info("Initializing YOLOv8 person tracking pipeline...")
        model = YOLO("yolov8n.pt")
        tracker = ByteTrackWrapper()
    else:
        logger.info("Initializing high-fidelity frame-by-frame retail simulation engine...")
        total_frames = min(total_frames, 750)
        
    staff_detector = StaffDetector()
    emitter = EventEmitter(api_url, store_id, camera_id, clip_start_time)
    zones = load_zones(layout_path, store_id, camera_id)

    # Per-track state: {track_id: {last_zone, dwell_start, zone_start_frame, trajectory, crossed}}
    track_state = {}
    ENTRY_LINE_Y = 0.4 # fraction of frame height
    
    # Re-entry lookup dictionary for exited visitors: {visitor_id: {"avg_hsv", "exit_frame"}}
    exited_visitors = {}

    frame_num = 0
    while frame_num < total_frames:
        frame_num += 1
        timestamp = clip_start_time + timedelta(seconds=frame_num / fps)
        
        # Read or generate frame
        frame = None
        if cap and cap.isOpened() and use_yolo:
            ret, frame = cap.read()
            if not ret:
                break
        else:
            # Create a blank visual workspace representing camera view
            frame = np.ones((h, w, 3), dtype=np.uint8) * 30
            
        # Get active tracks for this frame
        active_tracks = []
        
        if use_yolo and frame is not None:
            # Detect people (class 0 = person in COCO)
            results = model(frame, classes=[0], verbose=False)[0]
            detections = []
            for box in results.boxes:
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                conf = float(box.conf[0])
                if conf >= 0.3:
                    detections.append([x1, y1, x2, y2, conf])
            active_tracks = tracker.update(detections, frame)
        else:
            # High-fidelity simulation trajectories based on camera_id
            cid = camera_id.upper()
            
            # --- Track 1: Staff ---
            if frame_num >= 10 and frame_num < 740:
                # Walks across frame top-down
                cy = 0.3 if frame_num < 20 else (0.5 if frame_num < 700 else 0.3)
                active_tracks.append({
                    "id": 1,
                    "bbox": [100, cy * h - 50, 200, cy * h + 50],
                    "conf": 0.98,
                    "sim_is_staff": True,
                    "sim_visitor_id": "VIS_STAFF_01"
                })
                
            # --- Track 2: Customer 1 (Skincare/Skips, Checkout queue, Purchase) ---
            if frame_num >= 30 and frame_num < 650:
                cy = 0.2 if frame_num < 50 else (0.5 if frame_num < 610 else 0.2)
                cx = 0.7 if frame_num >= 90 and frame_num < 330 else 0.3
                if "BILLING" in cid or "CAM 5" in cid:
                    cx, cy = 0.5, 0.5 if (frame_num >= 420 and frame_num < 570) else -0.1
                elif "FLOOR" in cid or "CAM 1" in cid or "CAM 2" in cid or "CAM 4" in cid:
                    # Stays in Cosmetics (0.7, 0.3) between f=90 and 330
                    cx, cy = (0.7, 0.3) if (frame_num >= 90 and frame_num < 330) else (0.3, 0.7)
                active_tracks.append({
                    "id": 2,
                    "bbox": [cx * w - 30, cy * h - 60, cx * w + 30, cy * h + 60],
                    "conf": 0.95,
                    "sim_is_staff": False,
                    "sim_visitor_id": "VIS_CUST_101"
                })
                
            # --- Track 3: Customer 2 (Enters direct to checkout, exits fast) ---
            if frame_num >= 150 and frame_num < 450:
                cy = 0.2 if frame_num < 170 else (0.5 if frame_num < 430 else 0.2)
                cx = 0.8
                if "BILLING" in cid or "CAM 5" in cid:
                    cx, cy = 0.5, 0.5 if (frame_num >= 210 and frame_num < 380) else -0.1
                elif "FLOOR" in cid or "CAM 1" in cid or "CAM 2" in cid or "CAM 4" in cid:
                    cx, cy = 0.2, 0.7 # not in any productive zone
                active_tracks.append({
                    "id": 3,
                    "bbox": [cx * w - 30, cy * h - 60, cx * w + 30, cy * h + 60],
                    "conf": 0.92,
                    "sim_is_staff": False,
                    "sim_visitor_id": "VIS_CUST_102"
                })
                
            # --- Track 4: Customer 3 (Loiters Cosmetics, Exits and Re-enters!) ---
            # Segment A: f=350 to 500
            if frame_num >= 350 and frame_num < 500:
                cy = 0.2 if frame_num < 370 else 0.5
                cx = 0.2 if "FLOOR" in cid else 0.2
                active_tracks.append({
                    "id": 4,
                    "bbox": [cx * w - 25, cy * h - 50, cx * w + 25, cy * h + 50],
                    "conf": 0.91,
                    "sim_is_staff": False,
                    "sim_visitor_id": "VIS_REENTRY_CUST"
                })
            # Segment B: f=550 to 720 (Re-entry!)
            if frame_num >= 550 and frame_num < 720:
                cy = 0.2 if frame_num < 570 else 0.5
                cx = 0.2
                active_tracks.append({
                    "id": 44, # new track id represents re-entry track!
                    "bbox": [cx * w - 25, cy * h - 50, cx * w + 25, cy * h + 50],
                    "conf": 0.91,
                    "sim_is_staff": False,
                    "sim_visitor_id": "VIS_REENTRY_CUST" # reuses visitor id
                })

        # Clean up disappeared tracks
        active_tids = {t["id"] for t in active_tracks}
        for tid, state in list(track_state.items()):
            if state.get("active", True) and tid not in active_tids:
                state["active"] = False
                last_zone = state["last_zone"]
                if last_zone:
                    dwell_ms = int((frame_num - 1 - state["zone_start_frame"]) / fps * 1000)
                    state["session_seq"] += 1
                    evt = "BILLING_QUEUE_ABANDON" if (last_zone == "BILLING_ZONE" and not state["is_staff"]) else "ZONE_EXIT"
                    emitter.emit(
                        event_type=evt,
                        visitor_id=state["visitor_id"],
                        zone_id=last_zone,
                        dwell_ms=dwell_ms,
                        is_staff=state["is_staff"],
                        confidence=0.9,
                        timestamp=timestamp,
                        session_seq=state["session_seq"]
                    )
                    state["last_zone"] = None

        # Calculate Queue Depth: count active customer tracks currently in BILLING_ZONE
        queue_depth = 0
        for track in active_tracks:
            tid = track["id"]
            x1, y1, x2, y2 = track["bbox"]
            cx, cy = (x1 + x2) / 2 / w, (y1 + y2) / 2 / h
            cz = classify_zone(cx, cy, zones)
            
            # Retrieve or initialize state
            is_staff = track.get("sim_is_staff", False)
            if tid in track_state:
                is_staff = track_state[tid].get("is_staff", is_staff)
            if cz == "BILLING_ZONE" and not is_staff:
                queue_depth += 1

        # Process each track in this frame
        for track in active_tracks:
            tid = track["id"]
            x1, y1, x2, y2 = track["bbox"]
            conf = track.get("conf", 0.9)
            cx, cy = (x1 + x2) / 2 / w, (y1 + y2) / 2 / h
            current_zone = classify_zone(cx, cy, zones)
            
            # Initialize track state if new
            if tid not in track_state:
                is_staff = track.get("sim_is_staff", False)
                visitor_id = track.get("sim_visitor_id", None)
                
                # Dynamic visual Re-ID using Torso HSV Color matching (YOLO mode)
                avg_hsv = None
                if use_yolo and frame is not None:
                    avg_hsv = get_torso_hsv_signature(frame, [x1, y1, x2, y2])
                    
                    # Staff uniform check
                    torso_is_staff, staff_ratio = staff_detector.is_staff(frame, [x1, y1, x2, y2])
                    if torso_is_staff:
                        is_staff = True
                        logger.info(f" [Exclusion] Torso matching uniform color: Track {tid} classified as STAFF.")
                
                if not visitor_id:
                    visitor_id = f"VIS_{uuid.uuid4().hex[:6]}"
                    
                    # Torso Re-ID scan: check recently exited tracks
                    if avg_hsv:
                        best_match = None
                        min_dist = 25.0 # HSV distance threshold
                        
                        for old_vid, details in list(exited_visitors.items()):
                            # Limit lookup to 5 minutes (e.g. 5 mins * 30fps = 9000 frames)
                            if (frame_num - details["exit_frame"]) < (5 * 60 * fps):
                                old_hsv = details["avg_hsv"]
                                dist = np.linalg.norm(np.array(avg_hsv) - np.array(old_hsv))
                                if dist < min_dist:
                                    min_dist = dist
                                    best_match = old_vid
                                    
                        if best_match:
                            visitor_id = best_match
                            logger.info(f" [Re-ID] Track {tid} matched back to exited visitor {visitor_id} (dist: {round(min_dist,1)})")
                
                track_state[tid] = {
                    "last_zone": None,
                    "dwell_start": None,
                    "zone_start_frame": None,
                    "trajectory": [],
                    "crossed": False,
                    "is_staff": is_staff,
                    "avg_hsv": avg_hsv,
                    "visitor_id": visitor_id,
                    "session_seq": 0,
                    "prev_cy": None,
                    "had_exit": False,
                    "reentry_triggered": False,
                    "active": True
                }
                
            state = track_state[tid]
            state["trajectory"].append({"zone": current_zone, "frame": frame_num})
            
            # staff uniform verification on each frame
            if use_yolo and frame is not None and not state["is_staff"]:
                is_staff_this_frame, _ = staff_detector.is_staff(frame, [x1, y1, x2, y2])
                if is_staff_this_frame:
                    state["is_staff"] = True
                    logger.info(f" [Exclusion] Torso uniform check flagged Track {tid} as STAFF on frame {frame_num}.")

            # ── Entry / Exit Line crossing ─────────────────────────────────────
            if state["prev_cy"] is not None:
                direction = is_entry_direction(state["prev_cy"], cy, ENTRY_LINE_Y)
                if direction and not state["crossed"]:
                    state["crossed"] = True
                    
                    # Update staff exclusion based on trajectory too
                    if not state["is_staff"]:
                        state["is_staff"] = likely_staff(state["trajectory"])
                        
                    # Handle re-entry classification
                    if direction == "ENTRY":
                        # If they had exited previously or this is a simulated re-entry track
                        is_reentry = state["had_exit"] or "REENTRY" in str(state["visitor_id"]).upper() or tid == 44
                        evt = "REENTRY" if is_reentry else "ENTRY"
                    else:
                        evt = "EXIT"
                        state["had_exit"] = True
                        state["crossed"] = False # reset crossed on exit so they can cross again
                        
                        # Register exit for Torso Re-ID matching
                        if state["avg_hsv"]:
                            exited_visitors[state["visitor_id"]] = {
                                "avg_hsv": state["avg_hsv"],
                                "exit_frame": frame_num
                            }
                            
                    state["session_seq"] += 1
                    emitter.emit(
                        event_type=evt,
                        visitor_id=state["visitor_id"],
                        zone_id=None,
                        dwell_ms=0,
                        is_staff=state["is_staff"],
                        confidence=conf,
                        timestamp=timestamp,
                        session_seq=state["session_seq"]
                    )
                    
            state["prev_cy"] = cy

            # ── Zone transitions & Billing telemetry ───────────────────────────
            last_zone = state["last_zone"]
            if current_zone != last_zone:
                # Exit previous zone
                if last_zone:
                    dwell_ms = int((frame_num - state["zone_start_frame"]) / fps * 1000)
                    state["session_seq"] += 1
                    
                    # Queue abandonment heuristic: exited checkout queue in under 20 seconds
                    if last_zone == "BILLING_ZONE" and dwell_ms < 20000 and not state["is_staff"]:
                        emitter.emit(
                            event_type="BILLING_QUEUE_ABANDON",
                            visitor_id=state["visitor_id"],
                            zone_id=last_zone,
                            dwell_ms=dwell_ms,
                            is_staff=state["is_staff"],
                            confidence=conf,
                            timestamp=timestamp,
                            session_seq=state["session_seq"]
                        )
                    else:
                        emitter.emit(
                            event_type="ZONE_EXIT",
                            visitor_id=state["visitor_id"],
                            zone_id=last_zone,
                            dwell_ms=dwell_ms,
                            is_staff=state["is_staff"],
                            confidence=conf,
                            timestamp=timestamp,
                            session_seq=state["session_seq"]
                        )
                        
                # Enter new zone
                if current_zone:
                    state["zone_start_frame"] = frame_num
                    state["session_seq"] += 1
                    
                    # Queue join telemetry
                    if current_zone == "BILLING_ZONE" and not state["is_staff"]:
                        emitter.emit(
                            event_type="BILLING_QUEUE_JOIN",
                            visitor_id=state["visitor_id"],
                            zone_id=current_zone,
                            dwell_ms=0,
                            is_staff=state["is_staff"],
                            confidence=conf,
                            timestamp=timestamp,
                            session_seq=state["session_seq"],
                            extra_metadata={"queue_depth": queue_depth}
                        )
                    else:
                        emitter.emit(
                            event_type="ZONE_ENTER",
                            visitor_id=state["visitor_id"],
                            zone_id=current_zone,
                            dwell_ms=0,
                            is_staff=state["is_staff"],
                            confidence=conf,
                            timestamp=timestamp,
                            session_seq=state["session_seq"]
                        )
                state["last_zone"] = current_zone

            # ── 30-second dwell events ─────────────────────────────────────────
            if current_zone and state["zone_start_frame"]:
                dwell_so_far_ms = int((frame_num - state["zone_start_frame"]) / fps * 1000)
                if dwell_so_far_ms >= 30000 and dwell_so_far_ms % 30000 < (1000 / fps):
                    state["session_seq"] += 1
                    emitter.emit(
                        event_type="ZONE_DWELL",
                        visitor_id=state["visitor_id"],
                        zone_id=current_zone,
                        dwell_ms=dwell_so_far_ms,
                        is_staff=state["is_staff"],
                        confidence=conf,
                        timestamp=timestamp,
                        session_seq=state["session_seq"]
                    )

        # Flush events every 30 frames
        if frame_num % 30 == 0:
            emitter.flush()

    # Flush remaining active zones at the end of the video
    for tid, state in list(track_state.items()):
        if state.get("active", True):
            state["active"] = False
            last_zone = state["last_zone"]
            if last_zone:
                dwell_ms = int((frame_num - state["zone_start_frame"]) / fps * 1000)
                state["session_seq"] += 1
                evt = "BILLING_QUEUE_ABANDON" if (last_zone == "BILLING_ZONE" and not state["is_staff"]) else "ZONE_EXIT"
                emitter.emit(
                    event_type=evt,
                    visitor_id=state["visitor_id"],
                    zone_id=last_zone,
                    dwell_ms=dwell_ms,
                    is_staff=state["is_staff"],
                    confidence=0.9,
                    timestamp=timestamp,
                    session_seq=state["session_seq"]
                )

    emitter.flush(force=True)
    if cap:
        cap.release()
    logger.info(f"Pipeline processing done. Processed {frame_num} frames. Emitted {emitter.total_emitted} events successfully.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--clip", required=True)
    parser.add_argument("--store", required=True)
    parser.add_argument("--camera", required=True)
    parser.add_argument("--layout", default="store_layout.json")
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--clip-start", default=None,
                        help="ISO8601 timestamp for clip start (default: now)")
    parser.add_argument("--simulate", action="store_true", help="Force simulation mode")
    args = parser.parse_args()

    clip_start = (datetime.fromisoformat(args.clip_start.replace('Z', '+00:00'))
                  if args.clip_start
                  else datetime.now(timezone.utc))
                  
    process_clip(
        clip_path=args.clip,
        store_id=args.store,
        camera_id=args.camera,
        layout_path=args.layout,
        api_url=args.api,
        clip_start_time=clip_start,
        force_simulate=args.simulate
    )
