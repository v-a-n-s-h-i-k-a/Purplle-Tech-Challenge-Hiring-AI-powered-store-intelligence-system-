import cv2
import numpy as np
import os
from pipeline.config import FRAME_WIDTH, FRAME_HEIGHT, FPS, ENTRY_EXIT_LINE, STORE_ZONES

def generate_synthetic_video(output_path: str = "retail_sample.mp4", duration_seconds: int = 25):
    """
    Generates a synthetic retail store CCTV video simulation.
    Draws a layout with walls, zones, entry line, and animated customer and staff figures.
    """
    print(f"Generating synthetic CCTV video: {output_path} ({duration_seconds}s)...")
    
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out = cv2.VideoWriter(output_path, fourcc, FPS, (FRAME_WIDTH, FRAME_HEIGHT))
    
    total_frames = duration_seconds * FPS
    
    # Define simulated paths for tracks
    # track_id -> dict of properties: type ('customer', 'staff'), path (list of x, y, width, height)
    tracks = {
        # Staff member 1: Stays in the store, walks around cosmetics and checkout
        1: {
            "type": "staff",
            "shirt_color": (128, 0, 128), # Purple (BGR)
            "path": []
        },
        # Customer 1: Enters, goes to Cosmetics, then Checkout, then Exits
        2: {
            "type": "customer",
            "shirt_color": (34, 139, 34), # Forest Green (BGR)
            "path": []
        },
        # Customer 2: Enters, goes straight to Checkout, Exits quickly
        3: {
            "type": "customer",
            "shirt_color": (46, 139, 87), # Sea Green (BGR)
            "path": []
        },
        # Customer 3: Enters late, loiters in Cosmetics, doesn't purchase, Exits
        4: {
            "type": "customer",
            "shirt_color": (0, 128, 0), # Green (BGR)
            "path": []
        }
    }
    
    # Pre-generate paths for each track over 750 frames (25 seconds)
    for frame_idx in range(total_frames):
        # --- STAFF MEMBER (Track 1) ---
        # Walks around the store, starting in Cosmetics, moving to Checkout, staying there
        if frame_idx < 250:
            # Cosmetics Aisle browsing
            x = 250 + int(40 * np.sin(frame_idx * 0.05))
            y = 300 + int(30 * np.cos(frame_idx * 0.05))
        elif frame_idx < 500:
            # Walk towards checkout
            t = (frame_idx - 250) / 250.0
            x = int(250 * (1 - t) + 950 * t)
            y = int(300 * (1 - t) + 350 * t)
        else:
            # Stand in checkout queue zone
            x = 950 + int(20 * np.sin(frame_idx * 0.03))
            y = 350 + int(15 * np.cos(frame_idx * 0.03))
        tracks[1]["path"].append((x, y, 60, 120))
        
        # --- CUSTOMER 1 (Track 2) ---
        # Enters at f=30, goes to Cosmetics, then Checkout, exits at f=650
        if frame_idx < 30:
            tracks[2]["path"].append(None)
        elif frame_idx < 90:
            # Entering: walks from below line (y=680) to Cosmetics (x=300, y=400)
            t = (frame_idx - 30) / 60.0
            x = int(300 * t + 300 * (1 - t))
            y = int(400 * t + 680 * (1 - t))
            tracks[2]["path"].append((x, y, 55, 110))
        elif frame_idx < 330:
            # Browsing Cosmetics Aisle
            x = 300 + int(50 * np.sin(frame_idx * 0.08))
            y = 400 + int(40 * np.cos(frame_idx * 0.08))
            tracks[2]["path"].append((x, y, 55, 110))
        elif frame_idx < 420:
            # Walk to Checkout Queue
            t = (frame_idx - 330) / 90.0
            x = int(950 * t + 300 * (1 - t))
            y = int(380 * t + 400 * (1 - t))
            tracks[2]["path"].append((x, y, 55, 110))
        elif frame_idx < 570:
            # Wait in Checkout Queue
            x = 950 + int(10 * np.sin(frame_idx * 0.1))
            y = 380 + int(5 * np.cos(frame_idx * 0.1))
            tracks[2]["path"].append((x, y, 55, 110))
        elif frame_idx < 650:
            # Exit: Walk down below line (y=680)
            t = (frame_idx - 570) / 80.0
            x = int(950 * (1 - t) + 1050 * t)
            y = int(380 * (1 - t) + 680 * t)
            tracks[2]["path"].append((x, y, 55, 110))
        else:
            tracks[2]["path"].append(None)
            
        # --- CUSTOMER 2 (Track 3) ---
        # Enters at f=150, goes straight to Checkout, exits at f=450
        if frame_idx < 150:
            tracks[3]["path"].append(None)
        elif frame_idx < 210:
            # Enter: walks from below line (y=680) directly to Checkout (x=880, y=420)
            t = (frame_idx - 150) / 60.0
            x = int(880 * t + 880 * (1 - t))
            y = int(420 * t + 680 * (1 - t))
            tracks[3]["path"].append((x, y, 55, 110))
        elif frame_idx < 380:
            # Waits in Checkout Queue (buying a single item)
            x = 880 + int(5 * np.sin(frame_idx * 0.15))
            y = 420 + int(5 * np.cos(frame_idx * 0.15))
            tracks[3]["path"].append((x, y, 55, 110))
        elif frame_idx < 450:
            # Exiting
            t = (frame_idx - 380) / 70.0
            x = int(880 * (1 - t) + 800 * t)
            y = int(420 * (1 - t) + 680 * t)
            tracks[3]["path"].append((x, y, 55, 110))
        else:
            tracks[3]["path"].append(None)
            
        # --- CUSTOMER 3 (Track 4) ---
        # Enters at f=350, loiters in Cosmetics, exits at f=720 without checkout
        if frame_idx < 350:
            tracks[4]["path"].append(None)
        elif frame_idx < 420:
            # Entering to Cosmetics (x=200, y=320)
            t = (frame_idx - 350) / 70.0
            x = int(200 * t + 200 * (1 - t))
            y = int(320 * t + 680 * (1 - t))
            tracks[4]["path"].append((x, y, 50, 100))
        elif frame_idx < 660:
            # Loitering in Cosmetics Zone
            x = 200 + int(80 * np.sin(frame_idx * 0.04))
            y = 320 + int(60 * np.cos(frame_idx * 0.04))
            tracks[4]["path"].append((x, y, 50, 100))
        elif frame_idx < 720:
            # Exit directly
            t = (frame_idx - 660) / 60.0
            x = int(200 * (1 - t) + 150 * t)
            y = int(380 * (1 - t) + 680 * t)
            tracks[4]["path"].append((x, y, 50, 100))
        else:
            tracks[4]["path"].append(None)

    # Render video frame by frame
    for f in range(total_frames):
        # Create a modern dark blueprint background
        frame = np.ones((FRAME_HEIGHT, FRAME_WIDTH, 3), dtype=np.uint8) * 30 # Dark Gray (30,30,30)
        
        # Draw Store Geometries (Floor Plan grid)
        for grid_x in range(0, FRAME_WIDTH, 100):
            cv2.line(frame, (grid_x, 0), (grid_x, FRAME_HEIGHT), (45, 45, 45), 1)
        for grid_y in range(0, FRAME_HEIGHT, 100):
            cv2.line(frame, (0, grid_y), (FRAME_WIDTH, grid_y), (45, 45, 45), 1)
            
        # Draw Zones Polygons (Semi-transparent fills)
        overlay = frame.copy()
        for zone_id, zone_data in STORE_ZONES.items():
            poly = zone_data["polygon"]
            color = zone_data["color"]
            cv2.fillPoly(overlay, [poly], color)
            
        # Blend zones with background
        cv2.addWeighted(overlay, 0.15, frame, 0.85, 0, frame)
        
        # Draw outline of Zones
        for zone_id, zone_data in STORE_ZONES.items():
            poly = zone_data["polygon"]
            color = zone_data["color"]
            cv2.polylines(frame, [poly], True, color, 2)
            # Add Zone Label
            x_min = np.min(poly[:, 0])
            y_min = np.min(poly[:, 1])
            cv2.putText(frame, zone_data["name"], (x_min + 10, y_min - 10), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
            
        # Draw Entry/Exit line (Purple-magenta)
        p1 = ENTRY_EXIT_LINE["p1"]
        p2 = ENTRY_EXIT_LINE["p2"]
        cv2.line(frame, p1, p2, (200, 50, 200), 3)
        cv2.putText(frame, "ENTRY / EXIT LINE", (p1[0] + 20, p1[1] - 10), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 50, 200), 2)
        
        # Draw Animated Figures
        for track_id, track_data in tracks.items():
            pos = track_data["path"][f]
            if pos is None:
                continue
                
            x, y, w, h = pos
            shirt_color = track_data["shirt_color"]
            
            # x,y is the center of the figure
            # Draw Torso (Rectangle) representing body
            cv2.rectangle(frame, (x - w//2, y - h//2), (x + w//2, y + h//2), shirt_color, -1) # Shirt fill
            cv2.rectangle(frame, (x - w//2, y - h//2), (x + w//2, y + h//2), (255, 255, 255), 1) # White border
            
            # Draw Head (Circle) on top of torso
            head_y = y - h//2 + 15
            cv2.circle(frame, (x, head_y), 15, (220, 180, 150), -1) # Peach/skin tone
            cv2.circle(frame, (x, head_y), 15, (0, 0, 0), 1)
            
            # Draw Label (Mock track annotation)
            lbl = f"SimID: {track_id} ({track_data['type'].upper()})"
            cv2.putText(frame, lbl, (x - w//2, y - h//2 - 10), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)
            
        # Write Frame
        out.write(frame)
        
    out.release()
    print(f"Video created successfully: {output_path}")

if __name__ == "__main__":
    generate_synthetic_video()
