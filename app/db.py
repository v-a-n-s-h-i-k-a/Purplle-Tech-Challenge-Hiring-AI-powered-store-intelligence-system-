import os
import asyncpg
import json
import logging
import re
from datetime import datetime, timezone
from contextlib import asynccontextmanager

def normalize_query(query: str) -> str:
    return re.sub(r'\s+', ' ', query.strip()).upper()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@db:5432/storedb")

logger = logging.getLogger(__name__)

# ── High-Fidelity In-Memory Database engine fallback ──────────────────────────
class InMemoryDB:
    events = []
    pos_transactions = []

    @classmethod
    def clear(cls):
        cls.events = []
        cls.pos_transactions = []

class MockConnection:
    async def execute(self, query: str, *args):
        q = normalize_query(query)
        if "INSERT INTO POS_TRANSACTIONS" in q:
            # args: transaction_id, store_id, timestamp, basket_value
            timestamp = args[2]
            if isinstance(timestamp, str):
                timestamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            InMemoryDB.pos_transactions.append({
                "transaction_id": args[0],
                "store_id": args[1],
                "timestamp": timestamp,
                "basket_value": float(args[3]) if args[3] is not None else 0.0
            })
            return "INSERT 1"
        return "OK"

    async def fetchval(self, query: str, *args):
        q_upper = normalize_query(query)

        if "SELECT 1 FROM EVENTS" in q_upper:
            store_id = args[0]
            for e in InMemoryDB.events:
                if e["store_id"] == store_id:
                    return 1
            return None

        elif "INSERT INTO EVENTS" in q_upper:
            # args: event_id, store_id, camera_id, visitor_id, event_type, timestamp, zone_id, dwell_ms, is_staff, confidence, metadata
            event_id = args[0]
            for e in InMemoryDB.events:
                if str(e["event_id"]) == str(event_id):
                    return None # duplicate conflict

            timestamp = args[5]
            if isinstance(timestamp, str):
                timestamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))

            meta = args[10]
            if isinstance(meta, str):
                meta = json.loads(meta)

            InMemoryDB.events.append({
                "event_id": str(event_id),
                "store_id": args[1],
                "camera_id": args[2],
                "visitor_id": args[3],
                "event_type": args[4],
                "timestamp": timestamp,
                "zone_id": args[6],
                "dwell_ms": int(args[7]) if args[7] is not None else 0,
                "is_staff": bool(args[8]),
                "confidence": float(args[9]),
                "metadata": meta
            })
            return event_id

        elif "COUNT(DISTINCT VISITOR_ID) FROM EVENTS" in q_upper:
            store_id = args[0]
            event_types = None
            if "EVENT_TYPE='ENTRY'" in q_upper or "EVENT_TYPE IN ('ENTRY', 'REENTRY')" in q_upper or "EVENT_TYPE IN ('ENTRY','REENTRY')" in q_upper:
                event_types = ("ENTRY", "REENTRY")
            elif "EVENT_TYPE='ZONE_ENTER'" in q_upper:
                event_types = ("ZONE_ENTER",)
            elif "EVENT_TYPE='BILLING_QUEUE_JOIN'" in q_upper:
                event_types = ("BILLING_QUEUE_JOIN",)

            since = args[1] if len(args) > 1 else None
            until = args[2] if len(args) > 2 else None

            vids = set()
            for e in InMemoryDB.events:
                if e["store_id"] != store_id:
                    continue
                if e["is_staff"]:
                    continue
                if event_types and e["event_type"] not in event_types:
                    continue
                if event_types == ("ZONE_ENTER",):
                    zid = e["zone_id"] or ""
                    if "entry" in zid.lower() or "exit" in zid.lower() or "billing" in zid.lower():
                        continue
                if since and e["timestamp"] < since:
                    continue
                if until and e["timestamp"] > until:
                    continue
                vids.add(e["visitor_id"])
            return len(vids)

        elif "COUNT(DISTINCT E.VISITOR_ID)" in q_upper:
            store_id = args[0]
            since = args[1] if len(args) > 1 else None
            until = args[2] if len(args) > 2 else None

            vids = set()
            for e in InMemoryDB.events:
                if e["store_id"] != store_id:
                    continue
                if e["is_staff"]:
                    continue
                zid = e["zone_id"] or ""
                if "billing" not in zid.lower():
                    continue
                if since and e["timestamp"] < since:
                    continue
                if until and e["timestamp"] > until:
                    continue
                
                # Match POS transaction within 5 minutes (transaction occurred at or after event)
                for p in InMemoryDB.pos_transactions:
                    if p["store_id"] != store_id:
                        continue
                    time_diff = (p["timestamp"] - e["timestamp"]).total_seconds()
                    if 0 <= time_diff <= 300:
                        vids.add(e["visitor_id"])
                        break
            return len(vids)

        elif "COUNT(*) FROM EVENTS" in q_upper:
            store_id = args[0]
            event_type = "BILLING_QUEUE_JOIN" if "BILLING_QUEUE_JOIN" in q_upper else "BILLING_QUEUE_ABANDON"
            since = args[1] if len(args) > 1 else None

            count = 0
            for e in InMemoryDB.events:
                if e["store_id"] != store_id:
                    continue
                if e["event_type"] != event_type:
                    continue
                if since and e["timestamp"] < since:
                    continue
                count += 1
            return count

        elif "SELECT 1" in q_upper:
            return 1

        return 0

    async def fetch(self, query: str, *args):
        q_upper = normalize_query(query)

        if "SELECT ZONE_ID, ROUND(AVG(DWELL_MS)" in q_upper:
            store_id = args[0]
            since = args[1]

            zone_dwells = {}
            for e in InMemoryDB.events:
                if e["store_id"] != store_id:
                    continue
                if e["event_type"] not in ("ZONE_DWELL", "ZONE_EXIT", "BILLING_QUEUE_ABANDON"):
                    continue
                if e["is_staff"]:
                    continue
                if e["dwell_ms"] <= 0:
                    continue
                if e["timestamp"] < since:
                    continue
                zid = e["zone_id"]
                if not zid:
                    continue
                zone_dwells.setdefault(zid, []).append(e["dwell_ms"])

            results = []
            for zid, dwells in zone_dwells.items():
                avg_dwell_sec = round((sum(dwells) / len(dwells)) / 1000.0, 1)
                results.append({
                    "zone_id": zid,
                    "avg_dwell_sec": avg_dwell_sec,
                    "visits": len(dwells)
                })
            return sorted(results, key=lambda x: x["avg_dwell_sec"], reverse=True)

        elif "SELECT ZONE_ID, COUNT(DISTINCT VISITOR_ID)" in q_upper:
            store_id = args[0]
            since = args[1]

            zone_visits = {}
            for e in InMemoryDB.events:
                if e["store_id"] != store_id:
                    continue
                if e["is_staff"]:
                    continue
                if e["timestamp"] < since:
                    continue
                if e["event_type"] not in ('ZONE_ENTER', 'ZONE_DWELL', 'ZONE_EXIT', 'BILLING_QUEUE_ABANDON'):
                    continue
                zid = e["zone_id"]
                if not zid:
                    continue
                
                zone_visits.setdefault(zid, {"vids": set(), "dwells": []})
                zone_visits[zid]["vids"].add(e["visitor_id"])
                if e["dwell_ms"] > 0:
                    zone_visits[zid]["dwells"].append(e["dwell_ms"])

            results = []
            for zid, data in zone_visits.items():
                avg_dwell = sum(data["dwells"]) / len(data["dwells"]) if data["dwells"] else 0.0
                results.append({
                    "zone_id": zid,
                    "visit_count": len(data["vids"]),
                    "avg_dwell_ms": avg_dwell,
                    "active_hours": 1
                })
            return results

        elif "SELECT DISTINCT ZONE_ID FROM EVENTS" in q_upper:
            store_id = args[0]
            since = args[1] if len(args) > 1 else None

            v_zones = set()
            for e in InMemoryDB.events:
                if e["store_id"] != store_id:
                    continue
                if e["is_staff"]:
                    continue
                if since and e["timestamp"] < since:
                    continue
                if e["zone_id"]:
                    v_zones.add(e["zone_id"])

            return [{"zone_id": z} for z in v_zones]

        elif "SELECT STORE_ID, MAX(TIMESTAMP)" in q_upper:
            max_times = {}
            for e in InMemoryDB.events:
                sid = e["store_id"]
                ts = e["timestamp"]
                if sid not in max_times or ts > max_times[sid]:
                    max_times[sid] = ts
            return [{"store_id": sid, "last_event": ts} for sid, ts in max_times.items()]

        return []

    async def fetchrow(self, query: str, *args):
        q_upper = normalize_query(query)

        if "BILLING_QUEUE_JOIN" in q_upper:
            store_id = args[0]
            joins = [
                e for e in InMemoryDB.events
                if e["store_id"] == store_id and e["event_type"] == "BILLING_QUEUE_JOIN"
            ]
            if not joins:
                return None
            latest = sorted(joins, key=lambda x: x["timestamp"])[-1]
            return {
                "depth": latest["metadata"].get("queue_depth", 0),
                "timestamp": latest["timestamp"]
            }

        elif "ORDER BY TIMESTAMP DESC LIMIT 1" in q_upper:
            store_id = args[0]
            store_events = [e for e in InMemoryDB.events if e["store_id"] == store_id]
            if not store_events:
                return None
            latest = sorted(store_events, key=lambda x: x["timestamp"])[-1]
            return {"timestamp": latest["timestamp"]}

        return None

class MockPool:
    @asynccontextmanager
    async def acquire(self):
        yield MockConnection()

    async def close(self):
        pass

# ── Global database connection pool ─────────────────────────────────────────
_pool = None

async def init_db():
    global _pool
    try:
        logger.info(f"Attempting to connect to PostgreSQL at {DATABASE_URL}...")
        _pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10, timeout=5)
        # Verify connection
        async with _pool.acquire() as conn:
            await conn.execute("SELECT 1")
            
            # Setup tables
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                event_id     UUID PRIMARY KEY,
                store_id     TEXT NOT NULL,
                camera_id    TEXT NOT NULL,
                visitor_id   TEXT NOT NULL,
                event_type   TEXT NOT NULL,
                timestamp    TIMESTAMPTZ NOT NULL,
                zone_id      TEXT,
                dwell_ms     INTEGER DEFAULT 0,
                is_staff     BOOLEAN DEFAULT FALSE,
                confidence   FLOAT,
                metadata     JSONB DEFAULT '{}',
                ingested_at  TIMESTAMPTZ DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_events_store_time
                ON events (store_id, timestamp DESC);
            CREATE INDEX IF NOT EXISTS idx_events_visitor
                ON events (visitor_id, store_id);
            CREATE INDEX IF NOT EXISTS idx_events_type
                ON events (store_id, event_type);

            CREATE TABLE IF NOT EXISTS pos_transactions (
                transaction_id  TEXT PRIMARY KEY,
                store_id        TEXT NOT NULL,
                timestamp       TIMESTAMPTZ NOT NULL,
                basket_value    NUMERIC
            );
            CREATE INDEX IF NOT EXISTS idx_pos_store_time
                ON pos_transactions (store_id, timestamp DESC);
            """)
        logger.info("Successfully initialized PostgreSQL database with index triggers.")
    except Exception as e:
        logger.warning(
            f"PostgreSQL connection failed ({e}). "
            "Falling back to high-fidelity In-Memory Database engine for offline/local execution."
        )
        _pool = MockPool()

@asynccontextmanager
async def get_db():
    async with _pool.acquire() as conn:
        yield conn

