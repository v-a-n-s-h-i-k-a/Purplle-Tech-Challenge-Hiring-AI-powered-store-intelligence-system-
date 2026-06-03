from datetime import datetime, timezone

async def compute_funnel(conn, store_id: str) -> dict:
    """
    Funnel: Entry → Zone Visit → Billing Queue → Purchase
    Unit is SESSION (not raw events). Re-entries are deduplicated —
    a visitor_id counts once in each funnel stage regardless of re-entries.
    """
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

    # Stage 1: unique customer sessions (distinct visitor_id with ENTRY or REENTRY today)
    entered = await conn.fetchval("""
        SELECT COUNT(DISTINCT visitor_id) FROM events
        WHERE store_id=$1 AND event_type IN ('ENTRY', 'REENTRY')
          AND is_staff=FALSE AND timestamp >= $2
    """, store_id, today_start)

    # Stage 2: visited at least one product zone
    visited_zone = await conn.fetchval("""
        SELECT COUNT(DISTINCT visitor_id) FROM events
        WHERE store_id=$1 AND event_type='ZONE_ENTER'
          AND is_staff=FALSE AND timestamp >= $2
          AND zone_id NOT ILIKE '%entry%'
          AND zone_id NOT ILIKE '%exit%'
          AND zone_id NOT ILIKE '%billing%'
    """, store_id, today_start)

    # Stage 3: reached billing queue
    reached_billing = await conn.fetchval("""
        SELECT COUNT(DISTINCT visitor_id) FROM events
        WHERE store_id=$1 AND event_type='BILLING_QUEUE_JOIN'
          AND is_staff=FALSE AND timestamp >= $2
    """, store_id, today_start)

    # Stage 4: completed purchase (in billing zone within 5 min of POS txn)
    purchased = await conn.fetchval("""
        SELECT COUNT(DISTINCT e.visitor_id)
        FROM events e
        INNER JOIN pos_transactions p
          ON p.store_id = e.store_id
         AND e.timestamp BETWEEN p.timestamp - INTERVAL '5 minutes' AND p.timestamp
        WHERE e.store_id=$1 AND e.is_staff=FALSE AND e.timestamp >= $2
          AND e.zone_id ILIKE '%billing%'
    """, store_id, today_start)

    def drop_pct(a, b):
        if not a or not b:
            return 0.0
        return round((1 - b / a) * 100, 1)

    stages = [
        {"stage": "entry",        "visitors": entered         or 0, "drop_off_pct": 0.0},
        {"stage": "zone_visit",   "visitors": visited_zone    or 0, "drop_off_pct": drop_pct(entered, visited_zone)},
        {"stage": "billing_queue","visitors": reached_billing or 0, "drop_off_pct": drop_pct(visited_zone, reached_billing)},
        {"stage": "purchase",     "visitors": purchased       or 0, "drop_off_pct": drop_pct(reached_billing, purchased)},
    ]

    return {
        "store_id": store_id,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "note": "Each visitor_id counted once per stage; re-entries deduplicated",
        "stages": stages
    }
