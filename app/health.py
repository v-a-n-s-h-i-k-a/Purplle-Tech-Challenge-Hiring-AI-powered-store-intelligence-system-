from datetime import datetime, timezone, timedelta

async def get_health(conn) -> dict:
    now = datetime.now(timezone.utc)
    stale_threshold = timedelta(minutes=10)

    stores = await conn.fetch("""
        SELECT store_id, MAX(timestamp) AS last_event
        FROM events
        GROUP BY store_id
    """)

    store_status = []
    overall = "OK"
    for row in stores:
        lag = now - row["last_event"]
        is_stale = lag > stale_threshold
        if is_stale:
            overall = "DEGRADED"
        store_status.append({
            "store_id": row["store_id"],
            "last_event_at": row["last_event"].isoformat(),
            "lag_seconds": round(lag.total_seconds()),
            "status": "STALE_FEED" if is_stale else "OK"
        })

    db_ok = True
    try:
        await conn.fetchval("SELECT 1")
    except Exception:
        db_ok = False
        overall = "DEGRADED"

    return {
        "status": overall,
        "checked_at": now.isoformat(),
        "database": "OK" if db_ok else "UNAVAILABLE",
        "stores": store_status
    }
