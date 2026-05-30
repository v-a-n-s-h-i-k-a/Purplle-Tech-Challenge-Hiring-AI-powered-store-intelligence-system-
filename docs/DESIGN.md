# DESIGN.md — Store Intelligence System

## Architecture Overview

The system is a four-stage pipeline that transforms raw CCTV footage into a queryable
store analytics API. Each stage is independently containerised and communicates through
well-defined interfaces, so the CV pipeline can be replaced without touching the API.

```
Raw CCTV clips
    ↓
pipeline/detect.py   (YOLOv8n + ByteTrack → structured events)
    ↓ HTTP POST /events/ingest (batches of ≤500)
FastAPI API          (ingest, deduplicate, compute metrics)
    ↓
PostgreSQL           (events table + pos_transactions)
    ↓
Dashboard            (React / terminal — live polling)
```

### Stage 1 — Detection pipeline

The pipeline reads video frames using OpenCV, runs YOLOv8n for person detection (class 0
in COCO), and passes detections into ByteTrack for multi-object tracking. Each tracked
person gets a stable track ID for the duration of the clip.

Zone classification uses polygon ray-casting: the store_layout.json defines each zone as
a polygon in normalised (0–1) image coordinates. The bounding box centroid is tested
against each polygon to determine which zone a person occupies.

Entry/exit direction is determined by tracking whether a detection crosses a horizontal
threshold line (set per camera in the layout config). Crossing downward = ENTRY, crossing
upward = EXIT.

Staff detection uses a trajectory heuristic: any track that visits ≥4 distinct zones in
under 60 seconds is classified as staff (is_staff=true). This is imperfect for slow-moving
staff, but avoids the complexity of a dedicated classifier given the face-blur constraint.

### Stage 2 — Event schema

Events are buffered in memory (batches of 100) and POSTed to `/events/ingest`. A fallback
JSONL file captures events if the API is unavailable, allowing replay.

The schema follows the required specification exactly. `event_id` is a UUID v4 generated
at emission time, guaranteeing global uniqueness. `confidence` is always populated — low-
confidence detections are flagged, not dropped.

### Stage 3 — Intelligence API

FastAPI serves all analytics endpoints. Every request logs a `trace_id`, endpoint,
latency, and status code in structured JSON format for observability.

PostgreSQL stores events with a composite index on `(store_id, timestamp DESC)` covering
the most common query pattern. Session logic (funnel, re-entry deduplication) is computed
in SQL using `COUNT(DISTINCT visitor_id)` rather than in application code, keeping the
API stateless and horizontally scalable.

POS correlation is a time-window JOIN: a visitor is considered converted if they were in
the billing zone in the 5-minute window before a POS transaction timestamp.

### Stage 4 — Dashboard

The dashboard polls `/stores/{id}/metrics` and `/stores/{id}/anomalies` every 5 seconds
and renders live charts. For the bonus live view, it consumes events in near-real-time as
the pipeline flushes batches.

---

## AI-Assisted Decisions

### Decision 1 — Staff detection strategy

I asked Claude to evaluate three approaches to staff detection given the face-blur
constraint: (a) uniform colour segmentation using HSV thresholding, (b) a dedicated
binary classifier trained on labelled staff/customer crops, (c) trajectory heuristics.

Claude recommended option (b) as highest accuracy. I overrode this because: the dataset
is small (5 stores × 3 cameras), there are no staff labels in the provided data, and
training a classifier from scratch would consume 2+ days. I chose (c) with the heuristic
that ≥4 zones in <60s = staff. This trades recall for development speed — some slow-moving
staff may be counted as customers. I documented the threshold as a config parameter so it
can be tuned per-store.

### Decision 2 — Session deduplication for the funnel

The funnel requires that a visitor_id counts once per stage regardless of re-entries.
I asked Claude whether to handle this in SQL (DISTINCT) or in application-layer session
state. Claude suggested application-layer state (a Python dict per store) for "more
control". I disagreed: application-layer state breaks under horizontal scaling and
restarts. I implemented it in SQL using `COUNT(DISTINCT visitor_id)` which is correct,
stateless, and consistent across API replicas.

### Decision 3 — Event buffering strategy

Claude initially suggested using Kafka for event streaming between the pipeline and API.
I chose direct HTTP batching instead. Kafka adds significant operational complexity
(separate container, topic config, consumer groups) for a solo submission. HTTP batching
with a JSONL fallback file achieves the same durability guarantee with far less setup.
At Purplle's production scale of 40 stores, I would revisit this — Kafka's consumer group
model makes multi-store parallelism clean. But for this challenge, simplicity wins.
