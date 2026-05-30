"""
load_pos.py — POS transactions loader
Parses a CSV file containing POS transactions and uploads them to the FastAPI server.

Usage:
    python pipeline/load_pos.py --file data/pos_transactions.csv \
      --api http://localhost:8000
"""

import argparse
import csv
import requests
import os
import logging
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

def load_pos(file_path: str, api_url: str):
    api_url = api_url.rstrip("/")
    if not os.path.exists(file_path):
        logger.error(f"POS CSV file '{file_path}' does not exist.")
        return

    transactions = []
    
    with open(file_path, mode='r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            # POS column layout: store_id, transaction_id, timestamp, basket_value_inr (or basket_value)
            store_id = row.get("store_id")
            txn_id = row.get("transaction_id")
            ts = row.get("timestamp")
            
            # The column name might be basket_value or basket_value_inr
            basket_val = row.get("basket_value_inr") or row.get("basket_value") or "0.0"

            if not (store_id and txn_id and ts):
                logger.warning(f"Skipping malformed CSV row: {row}")
                continue

            # Standardize timestamp to ISO8601
            try:
                # Replace Z with UTC offset if present
                ts_iso = ts.replace('Z', '+00:00')
                dt = datetime.fromisoformat(ts_iso)
            except ValueError:
                # If it's a raw datetime format without timezone, parse it as UTC
                try:
                    dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                except ValueError:
                    logger.warning(f"Could not parse timestamp '{ts}', skipping row.")
                    continue

            transactions.append({
                "transaction_id": txn_id,
                "store_id": store_id,
                "timestamp": dt.isoformat(),
                "basket_value": float(basket_val)
            })

    if not transactions:
        logger.info("No valid POS transactions found to upload.")
        return

    logger.info(f"Uploading {len(transactions)} POS transactions to API at {api_url}/pos/ingest...")
    
    try:
        resp = requests.post(
            f"{api_url}/pos/ingest",
            json={"transactions": transactions},
            timeout=10
        )
        resp.raise_for_status()
        result = resp.json()
        logger.info(f"Successfully loaded {result.get('accepted')} transactions (total: {result.get('total')}).")
    except Exception as e:
        logger.error(f"Failed to post POS transactions: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True, help="Path to POS transaction CSV")
    parser.add_argument("--api", default="http://localhost:8000", help="API URL base")
    args = parser.parse_args()

    load_pos(args.file, args.api)
