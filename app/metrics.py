from datetime import datetime, timezone

async def compute_metrics(conn, store_id: str) -> dict | None:
    # Verify store exists in our events
    exists = await conn.fetchval(
        "SELECT 1 FROM events WHERE store_id=$1 LIMIT 1", store_id
    )
    if not exists:
        return None

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

    # Unique customer visitors today (exclude staff, count by first ENTRY or REENTRY)
    unique_visitors = await conn.fetchval("""
        SELECT COUNT(DISTINCT visitor_id)
        FROM events
        WHERE store_id=$1
          AND event_type IN ('ENTRY', 'REENTRY')
          AND is_staff=FALSE
          AND timestamp >= $2
    """, store_id, today_start)

    # Conversion rate: visitors who were in billing zone 5 min before a POS txn
    converted = await conn.fetchval("""
        SELECT COUNT(DISTINCT e.visitor_id)
        FROM events e
        INNER JOIN pos_transactions p
          ON p.store_id = e.store_id
         AND e.timestamp BETWEEN p.timestamp - INTERVAL '5 minutes' AND p.timestamp
        WHERE e.store_id=$1
          AND e.zone_id ILIKE '%billing%'
          AND e.is_staff=FALSE
          AND e.timestamp >= $2
    """, store_id, today_start)

    conversion_rate = round(converted / unique_visitors, 4) if unique_visitors > 0 else 0.0

    # Avg dwell per zone (seconds)
    zone_dwell = await conn.fetch("""
        SELECT zone_id,
               ROUND(AVG(dwell_ms) / 1000.0, 1) AS avg_dwell_sec,
               COUNT(*) AS visits
        FROM events
        WHERE store_id=$1
          AND event_type IN ('ZONE_DWELL', 'ZONE_EXIT', 'BILLING_QUEUE_ABANDON')
          AND is_staff=FALSE
          AND zone_id IS NOT NULL
          AND dwell_ms > 0
          AND timestamp >= $2
        GROUP BY zone_id
        ORDER BY avg_dwell_sec DESC
    """, store_id, today_start)

    # Current queue depth (most recent BILLING_QUEUE_JOIN metadata)
    queue_row = await conn.fetchrow("""
        SELECT metadata->>'queue_depth' AS depth
        FROM events
        WHERE store_id=$1 AND event_type='BILLING_QUEUE_JOIN'
        ORDER BY timestamp DESC LIMIT 1
    """, store_id)
    queue_depth = int(queue_row["depth"]) if queue_row and queue_row["depth"] else 0

    # Abandonment rate
    total_joins = await conn.fetchval("""
        SELECT COUNT(*) FROM events
        WHERE store_id=$1 AND event_type='BILLING_QUEUE_JOIN' AND timestamp >= $2
    """, store_id, today_start)
    total_abandons = await conn.fetchval("""
        SELECT COUNT(*) FROM events
        WHERE store_id=$1 AND event_type='BILLING_QUEUE_ABANDON' AND timestamp >= $2
    """, store_id, today_start)
    abandonment_rate = round(total_abandons / total_joins, 4) if total_joins > 0 else 0.0

    return {
        "store_id": store_id,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "unique_visitors": unique_visitors or 0,
        "converted_visitors": converted or 0,
        "conversion_rate": conversion_rate,
        "queue_depth": queue_depth,
        "abandonment_rate": abandonment_rate,
        "avg_dwell_per_zone": [
            {
                "zone_id": row["zone_id"],
                "avg_dwell_sec": float(row["avg_dwell_sec"]),
                "visits": row["visits"]
            }
            for row in zone_dwell
        ]
    }
