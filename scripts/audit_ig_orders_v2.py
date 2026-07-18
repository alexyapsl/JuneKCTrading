#!/usr/bin/env python3
"""
Comprehensive IG Order Audit Script (v2)

Features:
- Scans local JSONL logs for deal_references and deal_ids
- Falls back to IG /history/activity for rejected working orders
  (using a broad date range when needed)
- Also queries /history/transactions for closed P&L if requested
- Produces a rich report + audit_results.json

This version is designed to handle the fact that dealReference lookups
often 404 for old rejected working orders, while the activity history
endpoint remains reliable.

Usage examples:
    python scripts/audit_ig_orders_v2.py
    python scripts/audit_ig_orders_v2.py --from 2026-07-01 --to 2026-07-18
    python scripts/audit_ig_orders_v2.py --deal-id DIAAAAX3N2N5KAY
"""

import sys
import json
import argparse
import logging
from pathlib import Path
from collections import Counter, defaultdict
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import CONFIG
from src.account_resolver import resolve_credentials
from src.ig_rest_client import IGRestClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("audit_v2")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit IG working order attempts")
    parser.add_argument("--from", dest="from_date", default="2026-07-01",
                        help="Start date for IG activity query (YYYY-MM-DD)")
    parser.add_argument("--to", dest="to_date", default=None,
                        help="End date for IG activity query (YYYY-MM-DD). Defaults to today+1")
    parser.add_argument("--deal-id", dest="single_deal_id", default=None,
                        help="Audit only this specific dealId")
    parser.add_argument("--include-transactions", action="store_true",
                        help="Also fetch transaction history (P&L)")
    return parser.parse_args()


def collect_local_references() -> List[Dict[str, Any]]:
    """Collect all deal references + deal_ids from JSONL logs together with local context."""
    items: List[Dict[str, Any]] = []
    seen_refs = set()
    seen_ids = set()

    for jf in Path("logs/experiments").rglob("kc_*.jsonl"):
        for line in jf.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue

            exe = rec.get("execution") or {}
            ref = exe.get("deal_reference")
            did = exe.get("deal_id")

            item = {
                "deal_reference": ref,
                "deal_id": did,
                "local_status": exe.get("status"),
                "local_reason": exe.get("reason"),
                "market_price_snapshot": exe.get("market_price_snapshot"),
                "entry_distance_points": exe.get("entry_distance_points"),
                "rr": exe.get("rr"),
                "direction": (rec.get("signal") or {}).get("direction"),
                "entry_price": (rec.get("signal") or {}).get("entry_price"),
                "stop_loss": (rec.get("signal") or {}).get("stop_loss"),
                "timestamp_utc": rec.get("timestamp_utc"),
            }

            if ref and ref not in seen_refs:
                seen_refs.add(ref)
                items.append(item)
            if did and did not in seen_ids:
                seen_ids.add(did)
                # also store a copy keyed by deal_id
                items.append({**item, "deal_reference": None})

    return items


def main():
    args = parse_args()

    from_date = args.from_date
    to_date = args.to_date or (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

    print("=" * 90)
    print("IG WORKING ORDER AUDIT v2")
    print("=" * 90)
    print(f"Date range      : {from_date} → {to_date}")
    print(f"Include txns    : {args.include_transactions}")
    print()

    creds = resolve_credentials(CONFIG.account_name, CONFIG.paper_trading)
    client = IGRestClient(creds)
    client.login()

    # ------------------------------------------------------------------
    # 1. Pull broad activity history (the reliable source for rejections)
    # ------------------------------------------------------------------
    print("[1] Fetching full activity history from IG...")
    try:
        activity_resp = client.fetch_account_activity(
            from_date=from_date,
            to_date=to_date,
            epic="IX.D.DOW.IFS.IP",
        )
        activities = activity_resp.get("activities", []) if isinstance(activity_resp, dict) else []
        print(f"    Retrieved {len(activities)} activity records\n")
    except Exception as e:
        print(f"    ERROR fetching activity: {e}")
        activities = []

    # Index activities by dealId
    activity_by_deal_id: Dict[str, Dict] = {}
    for act in activities:
        did = act.get("dealId")
        if did:
            activity_by_deal_id[did] = act

    # ------------------------------------------------------------------
    # 2. Also collect local JSONL references (for correlation)
    # ------------------------------------------------------------------
    print("[2] Scanning local JSONL logs...")
    local_items = collect_local_references()
    print(f"    Found {len(local_items)} local execution records\n")

    # ------------------------------------------------------------------
    # 3. Build unified view
    # ------------------------------------------------------------------
    results = []
    rejection_reasons = Counter()
    wrong_side_count = 0

    for item in local_items:
        did = item.get("deal_id")
        ref = item.get("deal_reference")

        ig_activity = activity_by_deal_id.get(did) if did else None

        status = "UNKNOWN"
        description = None
        if ig_activity:
            status = ig_activity.get("status", "UNKNOWN")
            description = ig_activity.get("description")

            if "REJECTED" in status.upper():
                rejection_reasons[description] += 1

        # Simple wrong-side check (using local entry + IG description)
        wrong_side = False
        if description and "too close to market" in description.lower():
            wrong_side = True
            wrong_side_count += 1

        results.append({
            **item,
            "ig_status": status,
            "ig_description": description,
            "wrong_side": wrong_side,
        })

    # ------------------------------------------------------------------
    # 4. Optional: transaction history
    # ------------------------------------------------------------------
    transactions = []
    if args.include_transactions:
        print("[3] Fetching transaction history...")
        try:
            txn_resp = client.fetch_account_transactions(from_date=from_date, to_date=to_date)
            transactions = txn_resp.get("transactions", []) if isinstance(txn_resp, dict) else []
            print(f"    Retrieved {len(transactions)} transactions\n")
        except Exception as e:
            print(f"    ERROR: {e}")

    # ------------------------------------------------------------------
    # 5. Summary
    # ------------------------------------------------------------------
    print("=" * 90)
    print("AUDIT SUMMARY")
    print("=" * 90)
    print(f"Total local execution records : {len(results)}")
    print(f"IG activity records found     : {len(activities)}")
    rejected = sum(1 for r in results if "REJECTED" in (r.get("ig_status") or "").upper())
    accepted = sum(1 for r in results if "ACCEPTED" in (r.get("ig_status") or "").upper() or r.get("ig_status") == "OPEN")
    print(f"Accepted (from IG)            : {accepted}")
    print(f"Rejected (from IG)            : {rejected}")
    print(f"Wrong-side / too close        : {wrong_side_count}")
    print()

    if rejection_reasons:
        print("Rejection reasons (top):")
        for reason, cnt in rejection_reasons.most_common(10):
            print(f"  {reason[:70]:70} : {cnt}")
        print()

    # Show the most useful rows
    print("Sample rejected working orders with IG description:")
    for r in results:
        if "REJECTED" in (r.get("ig_status") or "").upper():
            print(f"  {r.get('timestamp_utc', '')[:16]} | {r.get('direction', ''):5} | "
                  f"entry={r.get('entry_price')} | dist={r.get('entry_distance_points')} | "
                  f"{r.get('ig_description', '')[:60]}")

    out_file = Path("audit_results_v2.json")
    out_file.write_text(json.dumps({
        "summary": {
            "total_records": len(results),
            "accepted": accepted,
            "rejected": rejected,
            "wrong_side": wrong_side_count,
        },
        "results": results,
        "activities": activities,
        "transactions": transactions,
    }, indent=2, default=str), encoding="utf-8")

    print(f"\nFull results written to {out_file}")


if __name__ == "__main__":
    main()