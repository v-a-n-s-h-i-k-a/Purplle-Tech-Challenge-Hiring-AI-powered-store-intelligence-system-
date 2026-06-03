"""
run.py — Video processing and data ingestion orchestrator
Executes synthetic video generation, processes clips for all 3 cameras,
and loads POS transactions into the database.
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import subprocess
import logging
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

def main():
    api_base = "http://localhost:8000"
    store_id = "ST1008"
    layout_path = "data/store_layout.json"
    
    # Absolute paths to new resources
    base_challenge_dir = r"c:\Users\DELL\Documents\PurplleTechChallenge"
    pos_path = os.path.join(base_challenge_dir, "Brigade_Bangalore_10_April_26 (1)bc6219c.csv")
    video_dir = os.path.join(base_challenge_dir, "CCTV Footage")
    
    # Check layout directory
    os.makedirs("data", exist_ok=True)

    # 1. Verify resources exist
    logger.info("--- Step 1: Checking resources... ---")
    if not os.path.exists(pos_path):
        logger.warning(f"POS CSV file '{pos_path}' not found! Ingestion might fail to compute conversion rates.")
    if not os.path.exists(video_dir):
        logger.error(f"CCTV Footage directory '{video_dir}' not found!")
        sys.exit(1)

    # 2. Run detection pipeline for each camera
    logger.info("--- Step 2: Running CCTV detection pipeline for ST1008 store cameras... ---")
    cameras = ["CAM 1", "CAM 2", "CAM 3", "CAM 4", "CAM 5"]
    
    # Anchor clip starting at 19:51:00 UTC on April 10, 2026 to match POS transaction date
    clip_start_str = "2026-04-10T19:51:00Z"

    for cam in cameras:
        logger.info(f"Processing camera feed: {cam} ...")
        clip_path = os.path.join(video_dir, f"{cam}.mp4")
        if not os.path.exists(clip_path):
            logger.error(f"Video file '{clip_path}' does not exist!")
            sys.exit(1)
            
        # Run detect.py inside process
        cmd = [
            sys.executable, "pipeline/detect.py",
            "--clip", clip_path,
            "--store", store_id,
            "--camera", cam,
            "--layout", layout_path,
            "--api", api_base,
            "--clip-start", clip_start_str,
            "--simulate"  # Force simulation mode for 100% deterministic and rapid test runs
        ]
        
        try:
            subprocess.run(cmd, check=True)
            logger.info(f"Successfully processed feed for {cam}.")
        except subprocess.CalledProcessError as e:
            logger.error(f"Error processing camera {cam}: {e}")
            sys.exit(1)

    # 3. Load POS transactions
    logger.info("--- Step 3: Loading POS transactions into database... ---")
    if os.path.exists(pos_path):
        cmd_pos = [
            sys.executable, "pipeline/load_pos.py",
            "--file", pos_path,
            "--api", api_base
        ]
        try:
            subprocess.run(cmd_pos, check=True)
            logger.info("POS transaction load completed successfully.")
        except subprocess.CalledProcessError as e:
            logger.error(f"Error loading POS data: {e}")
            sys.exit(1)
    else:
        logger.warning(f"POS CSV file '{pos_path}' not found. Skipping transaction load.")

    logger.info("🎉 SUCCESS: Entire Store Intelligence pipeline run complete!")

if __name__ == "__main__":
    main()
