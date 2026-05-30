import numpy as np

# Video and Frame Settings
FRAME_WIDTH = 1280
FRAME_HEIGHT = 720
FPS = 30

# Entry/Exit Detection Line (Crossing Line)
# Point A to Point B
# Moving from below the line to above the line = ENTRY
# Moving from above the line to below the line = EXIT
ENTRY_EXIT_LINE = {
    "p1": (100, 580),
    "p2": (1180, 580),
}

# Store Zones represented by Polygons (numpy array format for OpenCV)
STORE_ZONES = {
    "cosmetics_aisle": {
        "name": "Cosmetics Aisle",
        "polygon": np.array([
            [100, 150],
            [550, 150],
            [500, 500],
            [80, 500]
        ], np.int32),
        "color": (255, 105, 180),  # Hot Pink (BGR: Blue, Green, Red)
    },
    "checkout_queue": {
        "name": "Checkout Queue",
        "polygon": np.array([
            [700, 200],
            [1180, 200],
            [1220, 520],
            [750, 520]
        ], np.int32),
        "color": (0, 165, 255),    # Orange (BGR)
    }
}

# Staff Uniform HSV Color Range (Targeting brand color: Purple/Magenta)
# In OpenCV, Hue is 0-180, Saturation is 0-255, Value is 0-255
STAFF_HSV_LOW = np.array([125, 40, 40])
STAFF_HSV_HIGH = np.array([165, 255, 255])

# Percentage of upper torso pixels that must match the staff color to be classified as staff
STAFF_THRESHOLD = 0.15  # 15% of torso pixels matching purple = Staff member

# API Config
API_BASE_URL = "http://localhost:8000/api/v1"
API_INGEST_URL = f"{API_BASE_URL}/events/ingest"
