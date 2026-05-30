import cv2
import numpy as np
import argparse
import requests
import json
import time
from pipeline.config import (
    FRAME_WIDTH, FRAME_HEIGHT, FPS, ENTRY_EXIT_LINE, STORE_ZONES, API_INGEST_URL
)
from pipeline.tracker import StoreTracker

def parse_args():
    parser = argparse.ArgumentParser(description="AI-powered Store Intelligence Video Pipeline")
    parser.add_argument("--video", type=str, default="retail_sample.mp4", help="Path to input video file")
    parser.add_argument("--output", type=str, default="processed_cctv.mp4", help="Path to save processed video")
    parser.add_argument("--api-url", type=str, default=API_INGEST_URL, help="FastAPI Event Ingestion Endpoint")
    parser.add_argument("--yolo", action="store_true", help="Enable actual YOLOv8 tracking (default uses simulation)")
    parser.add_argument("--no-api", action="store_true", help="Disable posting events to backend API")
    parser.add_argument("--display", action="store_true", help="Show live OpenCV window playback")
    return parser.parse_args()


def post_event_to_api(api_url: str, event: dict, no_api: bool):
    """Posts a structured retail event to the FastAPI backend."""
    if no_api:
        return
        
    try:
        # Pydantic schemas expect specific field names
        payload = {
            "event_type": event["event_type"],
            "track_id": event["track_id"],
            "zone_id": event["zone_id"],
            "confidence": event["confidence"],
            "metadata": event.get("metadata", {})
        }
        response = requests.post(api_url, json=payload, timeout=2.0)
        if response.status_code == 200 or response.status_code == 201:
            print(f" [+] API Ingestion Success: {event['event_type'].upper()} for Track {event['track_id']}")
        else:
            print(f" [!] API Ingestion Failed ({response.status_code}): {response.text}")
    except Exception as e:
        print(f" [x] API Connection Error: Could not post event. Ensure backend is running. ({e})")


def get_predefined_simulation_tracks(duration_frames):
    """
    Returns pre-programmed simulated paths for our 4 tracks.
    This exactly aligns with synthetic_generator.py to make simulation tracking 100% robust.
    """
    tracks_data = {}
    
    # Pre-generate coordinates
    for f in range(duration_frames):
        tracks_data[f] = []
        
        # --- STAFF MEMBER (Track 1) ---
        if f < 250:
            x = 250 + int(40 * np.sin(f * 0.05))
            y = 300 + int(30 * np.cos(f * 0.05))
            tracks_data[f].append((1, (x - 30, y - 60, x + 30, y + 60)))
        elif f < 500:
            t = (f - 250) / 250.0
            x = int(250 * (1 - t) + 950 * t)
            y = int(300 * (1 - t) + 350 * t)
            tracks_data[f].append((1, (x - 30, y - 60, x + 30, y + 60)))
        else:
            x = 950 + int(20 * np.sin(f * 0.03))
            y = 350 + int(15 * np.cos(f * 0.03))
            tracks_data[f].append((1, (x - 30, y - 60, x + 30, y + 60)))
            
        # --- CUSTOMER 1 (Track 2) ---
        if 30 <= f < 650:
            if f < 90:
                t = (f - 30) / 60.0
                x = int(300 * t + 300 * (1 - t))
                y = int(400 * t + 680 * (1 - t))
            elif f < 330:
                x = 300 + int(50 * np.sin(f * 0.08))
                y = 400 + int(40 * np.cos(f * 0.08))
            elif f < 420:
                t = (f - 330) / 90.0
                x = int(950 * t + 300 * (1 - t))
                y = int(380 * t + 400 * (1 - t))
            elif f < 570:
                x = 950 + int(10 * np.sin(f * 0.1))
                y = 380 + int(5 * np.cos(f * 0.1))
            else:
                t = (f - 570) / 80.0
                x = int(950 * (1 - t) + 1050 * t)
                y = int(380 * (1 - t) + 680 * t)
            tracks_data[f].append((2, (x - 27, y - 55, x + 27, y + 55)))
            
        # --- CUSTOMER 2 (Track 3) ---
        if 150 <= f < 450:
            if f < 210:
                t = (f - 150) / 60.0
                x = int(880 * t + 880 * (1 - t))
                y = int(420 * t + 680 * (1 - t))
            elif f < 380:
                x = 880 + int(5 * np.sin(f * 0.15))
                y = 420 + int(5 * np.cos(f * 0.15))
            else:
                t = (f - 380) / 70.0
                x = int(880 * (1 - t) + 800 * t)
                y = int(420 * (1 - t) + 680 * t)
            tracks_data[f].append((3, (x - 27, y - 55, x + 27, y + 55)))
            
        # --- CUSTOMER 3 (Track 4) ---
        if 350 <= f < 720:
            if f < 420:
                t = (f - 350) / 70.0
                x = int(200 * t + 200 * (1 - t))
                y = int(320 * t + 680 * (1 - t))
            elif f < 660:
                x = 200 + int(80 * np.sin(f * 0.04))
                y = 320 + int(60 * np.cos(f * 0.04))
            else:
                t = (f - 660) / 60.0
                x = int(200 * (1 - t) + 150 * t)
                y = int(380 * (1 - t) + 680 * t)
            tracks_data[f].append((4, (x - 25, y - 50, x + 25, y + 50)))
            
    return tracks_data


def main():
    args = parse_args()
    
    # 1. Open Video Source
    cap = cv2.VideoCapture(args.video)
    if not cap.isOpened():
        print(f" [x] Error: Could not open video file {args.video}. Running synthetic_generator first...")
        from pipeline.synthetic_generator import generate_synthetic_video
        generate_synthetic_video(args.video, 25)
        cap = cv2.VideoCapture(args.video)
        if not cap.isOpened():
            print(" [x] Double Failure: Unable to read video. Exiting.")
            return

    # Extract Video properties
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = int(cap.get(cv2.CAP_PROP_FPS)) or FPS
    
    # Initialize Output Video Writer
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out = cv2.VideoWriter(args.output, fourcc, fps, (FRAME_WIDTH, FRAME_HEIGHT))
    
    # Initialize Tracker and YOLO if needed
    tracker = StoreTracker()
    
    yolo_model = None
    if args.yolo:
        try:
            from ultralytics import YOLO
            # Load nano model for real-time inference speed
            yolo_model = YOLO("yolov8n.pt")
            print(" [+] YOLOv8 model loaded successfully.")
        except ImportError:
            print(" [!] WARNING: ultralytics package not found. Bypassing YOLO mode and using Simulation Mode instead.")
            args.yolo = False

    # Pre-generate simulated detections for Simulation Mode
    sim_tracks_lookup = get_predefined_simulation_tracks(total_frames) if not args.yolo else None
    
    # Running Stats
    in_count = 0
    out_count = 0
    active_in_store = set()
    
    print(f" [+] Processing started. Processing {total_frames} frames...")
    
    frame_idx = 0
    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break
            
        # Detections list for this frame: (track_id, bbox_coords)
        detections = []
        
        # A. Get Detections from YOLO
        if args.yolo and yolo_model is not None:
            results = yolo_model.track(frame, persist=True, verbose=False, classes=0) # 0 is person class
            if results and results[0].boxes and results[0].boxes.id is not None:
                boxes = results[0].boxes.xyxy.cpu().numpy()
                track_ids = results[0].boxes.id.cpu().numpy().astype(int)
                for bbox, track_id in zip(boxes, track_ids):
                    detections.append((track_id, tuple(bbox)))
        # B. Get Detections from Pre-generated Simulation (Default)
        else:
            detections = sim_tracks_lookup.get(frame_idx, [])
            
        # Draw base blueprint overlays
        # Draw Zones Polygons (Semi-transparent fills)
        overlay = frame.copy()
        for zone_id, zone_data in STORE_ZONES.items():
            poly = zone_data["polygon"]
            color = zone_data["color"]
            cv2.fillPoly(overlay, [poly], color)
            
        cv2.addWeighted(overlay, 0.12, frame, 0.88, 0, frame)
        
        # Draw outlines of Zones
        for zone_id, zone_data in STORE_ZONES.items():
            poly = zone_data["polygon"]
            color = zone_data["color"]
            cv2.polylines(frame, [poly], True, color, 2)
            # Count how many people are in this zone right now
            zone_count = sum(1 for tid, _ in detections if tracker.current_zones.get(tid) == zone_id)
            cv2.putText(frame, f"{zone_data['name']} (Count: {zone_count})", 
                        (poly[0][0] + 10, poly[0][1] - 15), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2)
            
        # Draw Entry/Exit line (Purple-magenta)
        p1 = ENTRY_EXIT_LINE["p1"]
        p2 = ENTRY_EXIT_LINE["p2"]
        cv2.line(frame, p1, p2, (200, 50, 200), 3)
        cv2.putText(frame, f"ENTRY / EXIT LINE (In: {in_count} | Out: {out_count})", 
                    (p1[0] + 20, p1[1] - 15), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, (200, 50, 200), 2)
                    
        # Keep track of which IDs are present in this frame
        active_ids = set()
        
        # Process each detection
        for track_id, bbox in detections:
            active_ids.add(track_id)
            x1, y1, x2, y2 = map(int, bbox)
            
            # Update spatial and state tracking
            events = tracker.update_track(track_id, bbox, frame)
            
            # Post events to FastAPI
            for event in events:
                # Local statistics counting
                if event["event_type"] == "entry":
                    in_count += 1
                    active_in_store.add(track_id)
                elif event["event_type"] == "exit":
                    out_count += 1
                    active_in_store.discard(track_id)
                    
                post_event_to_api(args.api_url, event, args.no_api)
                
            # Draw standard computer vision feedback overlay
            is_staff = tracker.staff_status.get(track_id, False)
            color = (200, 50, 200) if is_staff else (0, 255, 0) # Purple for staff, green for customer
            
            # Draw bounding box
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            
            # Draw ground point (bottom center)
            px = int((x1 + x2) / 2)
            py = int(y2)
            cv2.circle(frame, (px, py), 5, (0, 0, 255), -1)
            
            # Draw track trails
            history = tracker.history.get(track_id, [])
            if len(history) >= 2:
                for pt_idx in range(1, len(history)):
                    cv2.line(frame, history[pt_idx - 1], history[pt_idx], color, 2)
                    
            # Text Tag Overlay
            role = "STAFF" if is_staff else "CUSTOMER"
            lbl = f"ID:{track_id} | {role}"
            # Add small badge background
            cv2.rectangle(frame, (x1, y1 - 25), (x1 + 150, y1), color, -1)
            cv2.putText(frame, lbl, (x1 + 5, y1 - 7), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)
            
        # Clean up tracks that disappeared (lost tracks)
        # Any track previously managed but not present in this frame
        lost_ids = list(tracker.history.keys() - active_ids)
        for lost_id in lost_ids:
            lost_events = tracker.handle_track_lost(lost_id)
            active_in_store.discard(lost_id)
            for event in lost_events:
                post_event_to_api(args.api_url, event, args.no_api)
                
        # Draw Live Intelligence Statistics Widget
        widget_x, widget_y = 30, 40
        cv2.rectangle(frame, (widget_x, widget_y), (widget_x + 350, widget_y + 90), (0, 0, 0), -1)
        cv2.rectangle(frame, (widget_x, widget_y), (widget_x + 350, widget_y + 90), (180, 180, 180), 2)
        cv2.putText(frame, "RETAIL INTELLIGENCE OVERLAY", (widget_x + 15, widget_y + 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1, cv2.LINE_AA)
        cv2.putText(frame, f"Live Store Occupancy: {len(active_in_store)}", (widget_x + 15, widget_y + 50),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)
        cv2.putText(frame, f"Total Entries: {in_count}  |  Total Exits: {out_count}", (widget_x + 15, widget_y + 70),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)

        # Write frame to processed video file
        out.write(frame)
        
        # Display window locally if enabled
        if args.display:
            cv2.imshow("Purplle Store CCTV Live Feed", frame)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
                
        frame_idx += 1
        
    cap.release()
    out.release()
    if args.display:
        cv2.destroyAllWindows()
        
    print(f" [+] Processing complete. Output saved to {args.output}")
    print(f" [*] Final Statistics: In: {in_count} | Out: {out_count}")

if __name__ == "__main__":
    main()
