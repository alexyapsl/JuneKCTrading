#!/usr/bin/env python3
"""
Audit script: fetch real IG confirmation for every deal_reference found in logs.

For each submitted working order we will see:
- Exactly what entry / stop / limit IG received
- Whether the limit was on the wrong side of entry
- The explicit rejection reason from IG (if rejected)

This bypasses the incomplete signal records in the JSONL.

Usage:
    python scripts/audit_ig_orders.py
"""

import sys
import json
import logging
from pathlib import Path
from collections import defaultdict, Counter
from typing import List, Dict, Any

# Make the script runnable directly from the project root
# (e.g.  py scripts\audit_ig_orders.py )
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import CONFIG
from src.account_resolver import resolve_credentials
from src.ig_rest_client import IGRestClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("audit")


def collect_deal_references() -> List[Dict[str, Any]]:
    """
    Return a list of dicts with deal_reference + the execution context that was
    recorded locally when the order was submitted.
    """
    items: List[Dict[str, Any]] = []
    seen = set()
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
            if ref and ref not in seen:
                seen.add(ref)
                items.append({
                    "deal_reference": ref,
                    "local_status": exe.get("status"),
                    "local_reason": exe.get("reason"),
                    "market_price_snapshot": exe.get("market_price_snapshot"),
                    "entry_distance_points": exe.get("entry_distance_points"),
                    "rr": exe.get("rr"),
                    "direction": (rec.get("signal") or {}).get("direction"),
                    "entry_price": (rec.get("signal") or {}).get("entry_price"),
                    "stop_loss": (rec.get("signal") or {}).get("stop_loss"),
                    "kc_upper": (rec.get("signal") or {}).get("kc_upper"),
                    "kc_lower": (rec.get("signal") or {}).get("kc_lower"),
                })
    return items


def main():
    items = collect_deal_references()
    print(f"\n=== Found {len(items)} unique deal_references in logs ===\n")
    if not items:
        print("No deal references found. Nothing to audit.")
        return

    refs = [it["deal_reference"] for it in items]
    # quick lookup from ref -> local execution context
    context_by_ref = {it["deal_reference"]: it for it in items}

    # Login once
    creds = resolve_credentials(CONFIG.account_name, CONFIG.paper_trading)
    client = IGRestClient(creds)
    client.login()

    results = []
    reason_counter = Counter()
    wrong_side = 0
    accepted = 0
    rejected = 0

    for i, ref in enumerate(refs, 1):
        local_ctx = context_by_ref.get(ref, {})
        print(f"[{i}/{len(refs)}] Confirming {ref} ...", end=" ", flush=True)
        try:
            conf = client.confirm_order(ref)
            # conf is usually a list of dicts or a single dict
            if isinstance(conf, list):
                conf = conf[0] if conf else {}

            status = conf.get("dealStatus") or conf.get("status") or "UNKNOWN"
            reason = conf.get("reason") or conf.get("errorCode") or "N/A"

            # Extract levels if present
            entry = None
            limit_lvl = None
            stop_lvl = None
            direction = None

            # IG confirmation shape can vary; try common locations
            if "affectedDeals" in conf:
                for d in conf["affectedDeals"]:
                    if d.get("dealId"):
                        # we only care about the first one for levels
                        pass

            # Some responses embed the working-order snapshot under 'workingOrder'
            wo = conf.get("workingOrder") or {}
            entry = wo.get("level") or conf.get("level")
            limit_lvl = wo.get("limitLevel") or conf.get("limitLevel")
            stop_lvl = wo.get("stopLevel") or conf.get("stopLevel")
            direction = wo.get("direction") or conf.get("direction")

            is_wrong_side = False
            if direction and entry is not None and limit_lvl is not None:
                if direction.upper() == "BUY" and limit_lvl <= entry:
                    is_wrong_side = True
                    wrong_side += 1
                elif direction.upper() == "SELL" and limit_lvl >= entry:
                    is_wrong_side = True
                    wrong_side += 1

            if status == "ACCEPTED" or status == "OPEN":
                accepted += 1
            else:
                rejected += 1
                reason_counter[reason] += 1

            # Merge local snapshot data
            local_entry_dist = local_ctx.get("entry_distance_points")
            local_snapshot = local_ctx.get("market_price_snapshot")

            results.append({
                "deal_reference": ref,
                "status": status,
                "reason": reason,
                "direction": direction,
                "entry": entry,
                "limit": limit_lvl,
                "stop": stop_lvl,
                "wrong_side": is_wrong_side,
                "local_entry_distance": local_entry_dist,
                "local_market_snapshot": local_snapshot,
            })
            print(f"{status} reason={reason} wrong_side={is_wrong_side} dist={local_entry_dist}")

        except Exception as e:
            print(f"ERROR: {e}")
            results.append({"deal_reference": ref, "status": "ERROR", "reason": str(e)})

    # Summary
    print("\n" + "=" * 90)
    print("AUDIT SUMMARY")
    print("=" * 90)
    print(f"Total deal references audited : {len(refs)}")
    print(f"ACCEPTED                       : {accepted}")
    print(f"REJECTED / FAILED              : {rejected}")
    print(f"Limit on wrong side of entry   : {wrong_side}")
    print()
    if reason_counter:
        print("Rejection reasons breakdown:")
        for r, cnt in reason_counter.most_common():
            print(f"  {r:30} : {cnt}")
    print()

    # Show the interesting cases
    if wrong_side > 0:
        print("=== Cases where limit was on the wrong side of entry ===")
        for r in results:
            if r.get("wrong_side"):
                print(f"  {r['deal_reference'][:20]}... | {r['direction']:5} | "
                      f"entry={r['entry']} limit={r['limit']} | {r['status']} {r['reason']}")
        print()

    # === Market price snapshot diagnostic ===
    print("=== Market Price Snapshot Diagnostic ===")
    dist_values = [r.get("local_entry_distance") for r in results if r.get("local_entry_distance") is not None]
    if dist_values:
        print(f"  Snapshots captured : {len(dist_values)}")
        print(f"  Min distance       : {min(dist_values):.2f}")
        print(f"  Max distance       : {max(dist_values):.2f}")
        print(f"  Avg distance       : {sum(dist_values)/len(dist_values):.2f}")
    else:
        print("  No market_price_snapshot data found in the JSONL (older runs).")

    # Cross-check: rejected orders that had snapshot data
    rejected_with_snapshot = [r for r in results
                              if r.get("status") != "ACCEPTED"
                              and r.get("local_entry_distance") is not None]
    if rejected_with_snapshot:
        print(f"\n  Rejected orders with snapshot ({len(rejected_with_snapshot)}):")
        for r in rejected_with_snapshot:
            print(f"    {r['deal_reference'][:18]}... | dist={r['local_entry_distance']:.2f} | reason={r.get('reason')}")

    print("\nFull result list saved to audit_results.json for deeper inspection.")
    Path("audit_results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()