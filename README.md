# Store Intelligence System

AI-powered retail analytics from raw CCTV footage. Built for Purplle Tech Challenge 2026.

## 5-command setup

```bash
git clone <your-repo-url> && cd store-intelligence
cp .env.example .env                          # set any overrides (optional)
docker compose up -d                           # starts db + api + dashboard
python pipeline/run.sh                         # processes all clips → events
open http://localhost:3000                     # live dashboard
```

API is live at `http://localhost:8000`
Auto-generated docs at `http://localhost:8000/docs`

---

## Running the detection pipeline

### Prerequisites
```bash
pip install ultralytics supervision opencv-python requests
# Download YOLOv8m weights (auto-downloads on first run)
```

### Process a single clip
```bash
python pipeline/detect.py \
  --clip data/clips/STORE_BLR_002/CAM_ENTRY_01.mp4 \
  --store STORE_BLR_002 \
  --camera CAM_ENTRY_01 \
  --layout data/store_layout.json \
  --api http://localhost:8000 \
  --clip-start 2026-03-03T09:00:00Z
```

### Process all clips (run.sh)
```bash
bash pipeline/run.sh \
  --clips-dir data/clips \
  --layout data/store_layout.json \
  --api http://localhost:8000
```

Events are POSTed to `/events/ingest` in batches of 100.
If the API is unreachable, events are written to `events_fallback.jsonl` for replay.

### Load POS transactions
```bash
python pipeline/load_pos.py --file data/pos_transactions.csv \
  --api http://localhost:8000
```

---

## API reference

| Endpoint | Description |
|---|---|
| `POST /events/ingest` | Ingest up to 500 events. Idempotent by event_id. |
| `GET /stores/{id}/metrics` | Unique visitors, conversion rate, dwell, queue depth |
| `GET /stores/{id}/funnel` | Entry → zone → billing → purchase with drop-off % |
| `GET /stores/{id}/heatmap` | Zone visit frequency + avg dwell, normalised 0–100 |
| `GET /stores/{id}/anomalies` | Active anomalies with severity + suggested actions |
| `GET /health` | Service status + STALE_FEED warnings per store |

Full OpenAPI spec: `http://localhost:8000/docs`

---

## Running tests

```bash
pip install pytest pytest-asyncio httpx
pytest tests/ -v --tb=short
```

Test coverage report:
```bash
pytest tests/ --cov=app --cov-report=term-missing
```

---

## Architecture

See `docs/DESIGN.md` for full architecture walkthrough and AI-assisted decisions.
See `docs/CHOICES.md` for model selection, schema design, and API architecture rationale.

---

## Live dashboard (Part E)

The dashboard at `http://localhost:3000` polls the API every 5 seconds and shows:
- Live visitor count per store
- Real-time conversion rate gauge
- Zone heatmap
- Active anomalies with severity badges

To simulate real-time feed during demo:
```bash
python pipeline/replay.py --events events_fallback.jsonl --speed 10x \
  --api http://localhost:8000
```
