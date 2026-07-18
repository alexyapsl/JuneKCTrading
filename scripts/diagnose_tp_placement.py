#!/usr/bin/env python3
"""
Diagnostic: Scan experiment logs for signals where the planned TP (limit)
would be on the wrong side of the entry price.

Usage:
    python scripts/diagnose_tp_placement.py
"""

import json
from pathlib import Path
from collections import defaultdict

def main():
    base = Path("logs/experiments")
    exp_dirs = [p for p in base.iterdir() if p.is_dir()]
    
    print("=" * 80)
    print("TP PLACEMENT DIAGNOSTIC")
    print("=" * 80)
    print(f"Scanning {len(exp_dirs)} experiment folder(s)\n")

    grand_total = defaultdict(int)
    grand_bad = defaultdict(int)
    grand_attempted = 0
    bad_cases = []

    for exp_dir in sorted(exp_dirs):
        jsonl_files = sorted(exp_dir.glob("kc_*.jsonl"))
        if not jsonl_files:
            continue

        print(f"--- Experiment: {exp_dir.name} ---")
        print(f"    {len(jsonl_files)} JSONL file(s)")

        exp_stats = defaultdict(int)
        exp_bad = defaultdict(int)
        exp_attempted = 0

        for jf in jsonl_files:
            for line_num, line in enumerate(jf.read_text(encoding="utf-8").splitlines(), 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue

                sig = rec.get("signal")
                exe = rec.get("execution")
                if not sig:
                    continue

                exp_stats["total_signals"] += 1
                grand_total["total_signals"] += 1

                direction = sig.get("direction")
                entry = sig.get("entry_price")
                kc_upper = sig.get("kc_upper")
                kc_lower = sig.get("kc_lower")
                ts = rec.get("timestamp_utc", "unknown")

                # Simulated limit that OrderManager would have used
                limit = kc_lower if direction == "SHORT" else kc_upper

                # Determine if this would have been a "bad" placement
                is_bad = False
                if direction == "LONG":
                    if limit is not None and entry is not None and limit <= entry:
                        is_bad = True
                        exp_bad["bad_long"] += 1
                        grand_bad["bad_long"] += 1
                elif direction == "SHORT":
                    if limit is not None and entry is not None and limit >= entry:
                        is_bad = True
                        exp_bad["bad_short"] += 1
                        grand_bad["bad_short"] += 1

                # Track attempted orders (not duplicates or RR-ignored)
                if exe:
                    status = exe.get("status")
                    if status not in ("DUPLICATE", "IGNORED_RR"):
                        exp_attempted += 1
                        grand_attempted += 1

                        if is_bad:
                            bad_cases.append({
                                "ts": ts,
                                "exp": exp_dir.name,
                                "direction": direction,
                                "entry": entry,
                                "limit": limit,
                                "kc_upper": kc_upper,
                                "kc_lower": kc_lower,
                                "status": status,
                                "file": jf.name,
                                "line": line_num,
                            })

        print(f"    Signals: {exp_stats['total_signals']}")
        print(f"    Orders attempted (non-duplicate, RR-ok): {exp_attempted}")
        print(f"    Bad LONG  (limit <= entry): {exp_bad['bad_long']}")
        print(f"    Bad SHORT (limit >= entry): {exp_bad['bad_short']}")
        print()

    # Grand totals
    print("=" * 80)
    print("GRAND TOTALS")
    print("=" * 80)
    print(f"Total signals across all experiments : {grand_total['total_signals']}")
    print(f"Total orders attempted               : {grand_attempted}")
    print(f"Bad LONG  placements (limit <= entry): {grand_bad['bad_long']}")
    print(f"Bad SHORT placements (limit >= entry): {grand_bad['bad_short']}")
    print()

    # List the bad cases
    if bad_cases:
        print("=" * 80)
        print("BAD CASES (limit on wrong side of entry)")
        print("=" * 80)
        for i, c in enumerate(bad_cases, 1):
            print(f"{i:2}. {c['ts']} | {c['direction']:5} | entry={c['entry']:.2f} | "
                  f"limit={c['limit']:.2f} | kc_u={c['kc_upper']:.2f} kc_l={c['kc_lower']:.2f} | "
                  f"status={c['status']}")
        print()
        print(f"Total bad cases found: {len(bad_cases)}")
    else:
        print("No bad TP placements found in the scanned logs.")

if __name__ == "__main__":
    main()