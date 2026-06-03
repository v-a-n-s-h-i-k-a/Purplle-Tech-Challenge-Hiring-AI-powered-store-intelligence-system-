from datetime import datetime, timezone, timedelta

# ── Heatmap ────────────────────────────────────────────────────────────────
async def compute_heatmap(conn, store_id: str) -> dict:
    latest_ts = await conn.fetchval(
        "SELECT MAX(timestamp) FROM events WHERE store_id=$1", store_id
    )
    if latest_ts:
        today_start = latest_ts.replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    else:
        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    rows = await conn.fetch("""
        SELECT zone_id,
               COUNT(DISTINCT visitor_id) AS visit_count,
               COALESCE(AVG(NULLIF(dwell_ms, 0)), 0) AS avg_dwell_ms,
               COUNT(DISTINCT DATE_TRUNC('hour', timestamp)) AS active_hours
        FROM events
        WHERE store_id=$1 AND is_staff=FALSE
          AND zone_id IS NOT NULL AND timestamp >= $2
          AND event_type IN ('ZONE_ENTER', 'ZONE_DWELL', 'ZONE_EXIT', 'BILLING_QUEUE_ABANDON')
        GROUP BY zone_id
    """, store_id, today_start)

    if not rows:
        return {"store_id": store_id, "zones": [], "data_confidence": "LOW"}

    max_visits = max(r["visit_count"] for r in rows) or 1
    max_dwell = max(r["avg_dwell_ms"] for r in rows) or 1
    total_sessions = await conn.fetchval("""
        SELECT COUNT(DISTINCT visitor_id) FROM events
        WHERE store_id=$1 AND event_type IN ('ENTRY', 'REENTRY') AND is_staff=FALSE AND timestamp >= $2
    """, store_id, today_start)

    zones = []
    for r in rows:
        norm_visits = round((r["visit_count"] / max_visits) * 100)
        norm_dwell  = round((r["avg_dwell_ms"] / max_dwell) * 100)
        zones.append({
            "zone_id": r["zone_id"],
            "visit_frequency_normalised": norm_visits,
            "avg_dwell_normalised": norm_dwell,
            "avg_dwell_sec": round(r["avg_dwell_ms"] / 1000, 1),
            "unique_visitors": r["visit_count"],
        })

    return {
        "store_id": store_id,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "data_confidence": "LOW" if (total_sessions or 0) < 20 else "HIGH",
        "zones": sorted(zones, key=lambda z: z["visit_frequency_normalised"], reverse=True)
    }
