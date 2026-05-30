"""
tracker.py — Re-ID and tracking wrapper
Uses supervision library's ByteTrack implementation.
Falls back to simple IoU tracker if supervision unavailable.
"""
import numpy as np

try:
    import supervision as sv
    USE_SUPERVISION = True
except ImportError:
    USE_SUPERVISION = False

class ByteTrackWrapper:
    def __init__(self):
        if USE_SUPERVISION:
            self.tracker = sv.ByteTrack()
        else:
            self._tracks = {}
            self._next_id = 1

    def update(self, detections: list, frame) -> list[dict]:
        if not detections:
            return []
        if USE_SUPERVISION:
            return self._supervision_update(detections)
        return self._iou_update(detections)

    def _supervision_update(self, detections: list) -> list[dict]:
        boxes = np.array([[d[0], d[1], d[2], d[3]] for d in detections])
        confs = np.array([d[4] for d in detections])
        class_ids = np.zeros(len(detections), dtype=int)
        sv_dets = sv.Detections(xyxy=boxes, confidence=confs, class_id=class_ids)
        tracked = self.tracker.update_with_detections(sv_dets)
        results = []
        for i, tid in enumerate(tracked.tracker_id):
            results.append({
                "id": int(tid),
                "bbox": tracked.xyxy[i].tolist(),
                "conf": float(tracked.confidence[i]) if tracked.confidence is not None else 0.9
            })
        return results

    def _iou_update(self, detections: list) -> list[dict]:
        """Simple IoU-based tracker fallback."""
        current = []
        for det in detections:
            x1, y1, x2, y2, conf = det
            matched_id = None
            best_iou = 0.3  # min IoU threshold

            for tid, prev in self._tracks.items():
                iou = self._iou(prev["bbox"], [x1, y1, x2, y2])
                if iou > best_iou:
                    best_iou = iou
                    matched_id = tid

            if matched_id is None:
                matched_id = self._next_id
                self._next_id += 1

            self._tracks[matched_id] = {"bbox": [x1, y1, x2, y2]}
            current.append({"id": matched_id, "bbox": [x1, y1, x2, y2], "conf": conf})

        # Prune stale tracks (not seen this frame)
        seen_ids = {t["id"] for t in current}
        self._tracks = {k: v for k, v in self._tracks.items() if k in seen_ids}
        return current

    @staticmethod
    def _iou(a, b) -> float:
        ax1, ay1, ax2, ay2 = a
        bx1, by1, bx2, by2 = b
        ix1, iy1 = max(ax1, bx1), max(ay1, by1)
        ix2, iy2 = min(ax2, bx2), min(ay2, by2)
        inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
        if inter == 0:
            return 0.0
        area_a = (ax2 - ax1) * (ay2 - ay1)
        area_b = (bx2 - bx1) * (by2 - by1)
        return inter / (area_a + area_b - inter)
