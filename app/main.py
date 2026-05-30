from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import time, uuid, logging, os
from datetime import datetime, timezone

from app.models import IngestRequest, IngestResponse
from app.db import get_db, init_db
from app.ingestion import ingest_events
from app.metrics import compute_metrics
from app.funnel import compute_funnel
from app.heatmap import compute_heatmap
from app.anomalies import detect_anomalies
from app.health import get_health

logging.basicConfig(
    level=logging.INFO,
    format='{"time":"%(asctime)s","level":"%(levelname)s","message":%(message)s}'
)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield

app = FastAPI(
    title="Store Intelligence API",
    version="1.0.0",
    lifespan=lifespan
)

@app.get("/")
async def root():
    return {
        "system": "Purplle AI Store Intelligence System",
        "status": "online",
        "documentation": "http://127.0.0.1:8000/docs",
        "health": "http://127.0.0.1:8000/health"
    }

# ── Middleware: trace_id + structured logging ──────────────────────────────
@app.middleware("http")
async def logging_middleware(request: Request, call_next):
    trace_id = str(uuid.uuid4())
    request.state.trace_id = trace_id
    start = time.time()
    try:
        response = await call_next(request)
        latency_ms = round((time.time() - start) * 1000, 2)
        store_id = request.path_params.get("store_id", "-")
        logger.info(
            f'"trace_id":"{trace_id}","store_id":"{store_id}",'
            f'"endpoint":"{request.url.path}","method":"{request.method}",'
            f'"status_code":{response.status_code},"latency_ms":{latency_ms}'
        )
        response.headers["X-Trace-Id"] = trace_id
        return response
    except Exception as e:
        logger.error(f'"trace_id":"{trace_id}","error":"{str(e)}"')
        return JSONResponse(
            status_code=503,
            content={"error": "Service unavailable", "trace_id": trace_id}
        )

from pydantic import BaseModel

class POSTransaction(BaseModel):
    transaction_id: str
    store_id: str
    timestamp: datetime
    basket_value: float

class POSIngestRequest(BaseModel):
    transactions: list[POSTransaction]

# ── Ingest POS ──────────────────────────────────────────────────────────────
@app.post("/pos/ingest")
async def ingest_pos(request: Request, body: POSIngestRequest):
    async with get_db() as conn:
        count = 0
        for tx in body.transactions:
            try:
                await conn.execute("""
                    INSERT INTO pos_transactions (transaction_id, store_id, timestamp, basket_value)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (transaction_id) DO NOTHING
                """, tx.transaction_id, tx.store_id, tx.timestamp, tx.basket_value)
                count += 1
            except Exception as e:
                logger.error(f"POS insert error: {e}")
    logger.info(
        f'"trace_id":"{request.state.trace_id}",'
        f'"pos_count":{len(body.transactions)},"accepted":{count}'
    )
    return {"accepted": count, "total": len(body.transactions)}

# ── Ingest ─────────────────────────────────────────────────────────────────
@app.post("/events/ingest", response_model=IngestResponse)
async def ingest(request: Request, body: IngestRequest):
    async with get_db() as conn:
        result = await ingest_events(conn, body.events)
    logger.info(
        f'"trace_id":"{request.state.trace_id}",'
        f'"event_count":{len(body.events)},"accepted":{result.accepted}'
    )
    return result

# ── Metrics ────────────────────────────────────────────────────────────────
@app.get("/stores/{store_id}/metrics")
async def metrics(store_id: str):
    async with get_db() as conn:
        data = await compute_metrics(conn, store_id)
    if data is None:
        raise HTTPException(404, detail=f"Store {store_id} not found")
    return data

# ── Funnel ─────────────────────────────────────────────────────────────────
@app.get("/stores/{store_id}/funnel")
async def funnel(store_id: str):
    async with get_db() as conn:
        return await compute_funnel(conn, store_id)

# ── Heatmap ────────────────────────────────────────────────────────────────
@app.get("/stores/{store_id}/heatmap")
async def heatmap(store_id: str):
    async with get_db() as conn:
        return await compute_heatmap(conn, store_id)

# ── Anomalies ──────────────────────────────────────────────────────────────
@app.get("/stores/{store_id}/anomalies")
async def anomalies(store_id: str):
    async with get_db() as conn:
        return await detect_anomalies(conn, store_id)

# ── Health ─────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    async with get_db() as conn:
        return await get_health(conn)
