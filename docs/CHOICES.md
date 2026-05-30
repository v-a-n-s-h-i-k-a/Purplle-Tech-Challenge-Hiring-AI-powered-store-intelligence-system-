# CHOICES.md — Three Key Engineering Decisions

## Decision 1 — Detection model selection

**Options considered:**
- YOLOv8n (nano): fastest, 640×640 input, ~80ms/frame on CPU, 37.3 mAP COCO
- YOLOv8m (medium): more accurate, ~180ms/frame on CPU, 50.2 mAP
- RT-DETR: transformer-based, state-of-art accuracy, requires GPU for real-time
- MediaPipe: lightweight but weaker on occlusion cases

**What AI suggested:**
I asked Claude to evaluate these models against the specific challenge constraints
(1080p footage, 15fps, 5 edge cases including partial occlusion and group entry).
Claude recommended YOLOv8m as the best balance, noting that partial occlusion
performance drops significantly on nano. It also suggested RT-DETR for best accuracy
if a GPU was available.

**What I chose and why:**
YOLOv8n for development + YOLOv8m for final submission runs. The clips are 20 minutes
each — at 180ms/frame on CPU, processing one clip would take ~5 hours with the medium
model. I process every 2nd frame (effectively 7.5fps) with the medium model, cutting
that to ~2.5 hours per clip while retaining most accuracy. For group entry, YOLOv8m's
higher mAP matters: detecting 3 people in a doorway simultaneously is harder than
detecting them in open floor space.

I did not use a VLM for detection because frame-by-frame VLM calls are cost-prohibitive
at 15fps × 60 minutes × 15 cameras = ~54,000 frames per store. I did evaluate using
Claude Vision for zone classification on a sample of 50 frames — it was accurate but
latency was ~1.2s/frame vs 8ms for the polygon test. Rule-based zone classification
stays.

---

## Decision 2 — Event schema design

**Options considered:**
- Flat schema: all fields at top level, simple but verbose
- Nested schema with typed sub-objects: `detection`, `session`, `zone` objects
- The required schema from the problem spec (flat with a `metadata` JSONB field)

**What AI suggested:**
Claude suggested a more granular nested schema where `session_context` contained
visitor history and `detection_context` contained model outputs. This would make
querying easier at the cost of a more complex ingest parser.

**What I chose and why:**
I followed the required spec exactly — there was no ambiguity here. The `metadata`
JSONB field is the right place for optional, event-type-specific fields like
`queue_depth`. JSONB in PostgreSQL is queryable (you can index specific keys and use
`->>`), so the schema doesn't sacrifice query power. The `session_seq` field in metadata
was my addition: it lets you reconstruct the full visit sequence for any visitor_id by
ordering on session_seq within a store + visitor_id.

One deliberate choice: `confidence` is never suppressed or rounded to a binary.
Low-confidence detections (e.g. partially occluded person at 0.32) are emitted with
their true confidence value. This lets the API layer make policy decisions about which
events to include in metrics — rather than baking a hard threshold into the pipeline
where it can't be tuned without reprocessing.

---

## Decision 3 — API architecture: SQLite vs PostgreSQL

**Options considered:**
- SQLite: zero-config, file-based, simple, single-writer limitation
- PostgreSQL: full ACID, concurrent writes, proper time-series indexing, docker image
- TimescaleDB (PostgreSQL extension): native time-series compression and bucketing

**What AI suggested:**
Claude's initial recommendation was SQLite with the reasoning that "the problem says
SQLite is fine and it reduces setup complexity." It noted TimescaleDB as overkill.

**What I chose and why:**
PostgreSQL, overriding the AI suggestion. My reasons:

1. The scoring harness runs `assertions.py` against a running system — concurrent reads
   from the test suite + live event writes would hit SQLite's single-writer lock,
   causing intermittent 500s during scoring.
2. The `/anomalies` endpoint requires a 7-day historical comparison. SQLite's datetime
   functions are limited; the PostgreSQL query (`INTERVAL '7 days'`, window functions)
   is cleaner and tested.
3. `asyncpg` gives proper async connection pooling — SQLite's `aiosqlite` is a thread
   wrapper, not true async. Under load, FastAPI's async workers would block.

TimescaleDB was tempting but the compression and hypertable features only pay off at
higher event volumes. For 5 stores × 20 minutes, standard PostgreSQL time-indexed
tables are fast enough and simpler to operate.
