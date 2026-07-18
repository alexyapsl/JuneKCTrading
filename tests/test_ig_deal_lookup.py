#!/usr/bin/env python3
"""
Manual test: demonstrate confirming an order using both deal_reference and deal_id.

Example values from user:
    deal_reference : "5ZKRRMG478CTYRZ"
    deal_id        : "DIAAAAX3N2N5KAY"

Run:
    python tests/test_ig_deal_lookup.py
"""

import sys
from pathlib import Path

# Make sure project root is on path when running directly
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import logging
from config import CONFIG
from src.account_resolver import resolve_credentials
from src.ig_rest_client import IGRestClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("deal_lookup_test")


def main():
    print("=" * 80)
    print("IG Deal Lookup Test (deal_reference vs deal_id)")
    print("=" * 80)

    # Resolve credentials exactly as the live runner does
    creds = resolve_credentials(CONFIG.account_name, CONFIG.paper_trading)
    client = IGRestClient(creds)
    client.login()

    # ------------------------------------------------------------------
    # 1) Query by deal_reference
    # ------------------------------------------------------------------
    deal_ref = "5ZKRRMG478CTYRZ"
    print(f"\n[1] Confirming by deal_reference = {deal_ref}")
    try:
        resp_ref = client.confirm_order(deal_ref)
        print("    Response (deal_reference):")
        print(resp_ref)
    except Exception as e:
        print(f"    ERROR via deal_reference: {e}")

    # ------------------------------------------------------------------
    # 2) Query by deal_id
    # ------------------------------------------------------------------
    deal_id = "DIAAAAX3N2N5KAY"
    print(f"\n[2] Confirming by deal_id = {deal_id}")
    try:
        resp_id = client.fetch_deal_by_deal_id(deal_id)
        print("    Response (deal_id):")
        print(resp_id)
    except Exception as e:
        print(f"    ERROR via deal_id: {e}")

    print("\n" + "=" * 80)
    print("Test complete. Inspect the two responses above.")
    print("=" * 80)


if __name__ == "__main__":
    main()