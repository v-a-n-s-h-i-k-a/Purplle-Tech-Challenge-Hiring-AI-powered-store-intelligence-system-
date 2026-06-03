from datetime import datetime, timezone, timedelta

async def detect_anomalies(conn, store_id: str) -> dict:
    latest_ts = await conn.fetchval(
        "SELECT MAX(timestamp) FROM events WHERE store_id=$1", store_id
    )
    now = latest_ts if latest_ts else datetime.now(timezone.utc)
    anomalies = []

    # ── 1. BILLING_QUEUE_SPIKE: queue depth > 5 in last 5 min ────────────
    queue_row = await conn.fetchrow("""
        SELECT (metadata->>'queue_depth')::int AS depth, timestamp
        FROM events
        WHERE store_id=$1 AND event_type='BILLING_QUEUE_JOIN'
        ORDER BY timestamp DESC LIMIT 1
    """, store_id)
    if queue_row and queue_row["depth"] and queue_row["depth"] > 5:
        depth = queue_row["depth"]
        anomalies.append({
            "anomaly_type": "BILLING_QUEUE_SPIKE",
            "severity": "CRITICAL" if depth > 10 else "WARN",
            "detected_at": now.isoformat(),
            "detail": f"Queue depth is {depth}",
            "suggested_action": "Open additional billing counter immediately" if depth > 10
                                else "Monitor queue — consider opening second counter"
        })

    # ── 2. DEAD_ZONE: a zone with no visits in last 30 min ───────────────
    cutoff_30 = now - timedelta(minutes=30)
    all_zones = await conn.fetch("""
        SELECT DISTINCT zone_id FROM events
        WHERE store_id=$1 AND zone_id IS NOT NULL AND is_staff=FALSE
    """, store_id)
    recent_zones = await conn.fetch("""
        SELECT DISTINCT zone_id FROM events
        WHERE store_id=$1 AND zone_id IS NOT NULL
          AND is_staff=FALSE AND timestamp >= $2
    """, store_id, cutoff_30)
    recent_zone_ids = {r["zone_id"] for r in recent_zones}
    for row in all_zones:
        z = row["zone_id"]
        if z not in recent_zone_ids:
            anomalies.append({
                "anomaly_type": "DEAD_ZONE",
                "severity": "INFO",
                "detected_at": now.isoformat(),
                "detail": f"Zone '{z}' has had no customer visits in 30+ minutes",
                "suggested_action": f"Check if zone '{z}' display needs refresh or restocking"
            })

    # ── 3. CONVERSION_DROP: today vs 7-day avg ───────────────────────────
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = today_start - timedelta(days=7)

    today_rate = await _conversion_rate(conn, store_id, today_start, now)
    historical_rate = await _conversion_rate(conn, store_id, week_ago, today_start)

    if historical_rate and today_rate is not None:
        drop_pct = (historical_rate - today_rate) / historical_rate * 100
        if drop_pct > 20:
            anomalies.append({
                "anomaly_type": "CONVERSION_DROP",
                "severity": "CRITICAL" if drop_pct > 40 else "WARN",
                "detected_at": now.isoformat(),
                "detail": f"Conversion rate {round(today_rate*100,1)}% vs 7-day avg {round(historical_rate*100,1)}% (drop: {round(drop_pct,1)}%)",
                "suggested_action": "Review funnel drop-off points; check staff coverage at billing"
            })

    # ── 4. STALE_FEED (reported here too, full detail in /health) ────────
    last_event = await conn.fetchrow("""
        SELECT timestamp FROM events WHERE store_id=$1
        ORDER BY timestamp DESC LIMIT 1
    """, store_id)
    if last_event:
        lag_min = (now - last_event["timestamp"]).total_seconds() / 60
        if lag_min > 10:
            anomalies.append({
                "anomaly_type": "STALE_FEED",
                "severity": "CRITICAL",
                "detected_at": now.isoformat(),
                "detail": f"No events received for {round(lag_min, 1)} minutes",
                "suggested_action": "Check camera feed and pipeline process"
            })

    return {
        "store_id": store_id,
        "as_of": now.isoformat(),
        "anomaly_count": len(anomalies),
        "anomalies": anomalies
    }

async def _conversion_rate(conn, store_id, start, end) -> float | None:
    visitors = await conn.fetchval("""
        SELECT COUNT(DISTINCT visitor_id) FROM events
        WHERE store_id=$1 AND event_type='ENTRY'
          AND is_staff=FALSE AND timestamp BETWEEN $2 AND $3
    """, store_id, start, end)
    if not visitors:
        return None
    converted = await conn.fetchval("""
        SELECT COUNT(DISTINCT e.visitor_id)
        FROM events e
        JOIN pos_transactions p ON p.store_id=e.store_id
          AND e.timestamp BETWEEN p.timestamp - INTERVAL '5 minutes' AND p.timestamp
        WHERE e.store_id=$1 AND e.is_staff=FALSE
          AND e.zone_id ILIKE '%billing%'
          AND e.timestamp BETWEEN $2 AND $3
    """, store_id, start, end)
    return (converted or 0) / visitors
