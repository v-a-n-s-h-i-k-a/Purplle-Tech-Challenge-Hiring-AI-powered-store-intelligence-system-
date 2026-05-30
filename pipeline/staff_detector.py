import cv2
import numpy as np
from pipeline.config import STAFF_HSV_LOW, STAFF_HSV_HIGH, STAFF_THRESHOLD

class StaffDetector:
    """
    Classifies store staff members based on whether they are wearing
    the official store uniform (color-based classification in HSV space).
    """
    
    def __init__(self):
        self.hsv_low = STAFF_HSV_LOW
        self.hsv_high = STAFF_HSV_HIGH
        self.threshold = STAFF_THRESHOLD

    def is_staff(self, frame: np.ndarray, bbox: tuple) -> tuple:
        """
        Analyzes the upper-torso region of the person's bounding box to determine
        if they are wearing the signature purple uniform.
        
        Args:
            frame: Raw BGR image frame from video.
            bbox: Bounding box tuple (x1, y1, x2, y2).
            
        Returns:
            (is_staff: bool, matching_ratio: float)
        """
        h, w, _ = frame.shape
        x1, y1, x2, y2 = map(int, bbox)
        
        # Ensure bounding boxes are bounded by frame dimensions
        x1 = max(0, x1)
        y1 = max(0, y1)
        x2 = min(w, x2)
        y2 = min(h, y2)
        
        # If the bounding box is too small or invalid, return False
        if (x2 - x1) <= 10 or (y2 - y1) <= 10:
            return False, 0.0
            
        # Target the upper-torso of the bounding box (20% to 55% of height, 15% to 85% of width)
        # This focuses on the shirt/uniform while avoiding faces, hair, and lower clothing/legs.
        torso_y1 = y1 + int(0.20 * (y2 - y1))
        torso_y2 = y1 + int(0.55 * (y2 - y1))
        torso_x1 = x1 + int(0.15 * (x2 - x1))
        torso_x2 = x1 + int(0.85 * (x2 - x1))
        
        # Crop upper torso
        torso_crop = frame[torso_y1:torso_y2, torso_x1:torso_x2]
        
        # Double check crop is valid
        if torso_crop.size == 0:
            return False, 0.0
            
        # Convert to HSV color space
        hsv_torso = cv2.cvtColor(torso_crop, cv2.COLOR_BGR2HSV)
        
        # Create mask for pixels within purple bounds
        mask = cv2.inRange(hsv_torso, self.hsv_low, self.hsv_high)
        
        # Calculate ratio of matching pixels
        matching_pixels = cv2.countNonZero(mask)
        total_pixels = torso_crop.shape[0] * torso_crop.shape[1]
        
        matching_ratio = matching_pixels / float(total_pixels)
        
        # Classify as staff if matching ratio is above threshold
        is_staff_member = matching_ratio >= self.threshold
        
        return is_staff_member, matching_ratio
