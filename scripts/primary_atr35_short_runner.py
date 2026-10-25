"""Primary runner entry point (2026-10-25 experiment): TREATMENT on account1.

Thin wrapper around run_kc_live.py that injects the entry-filter config via
env vars BEFORE config.py is imported. Separate file so process lists (and
scripts/kc-autostart.ps1) can distinguish it from the control instance.

Role (ATR>=35 short-only filter experiment):
  - TREATMENT: v5 dyn exit management + SHORT-only + ATR>=35 entry filter
    (KC_DIRECTION_FILTER=SHORT, KC_ATR_MIN=35) on account1, real fills.
  - Control (account2, scripts/shadow_live_runner.py): same dyn management,
    NO entry filters = the old primary behaviour as unfiltered baseline.
  - Blocked signals are still logged to the JSONL funnel (flagged
    direction_blocked / atr_blocked, execution.status BLOCKED_DIRECTION /
    BLOCKED_ATR) so we keep full accounting of what the filter skipped.

Run:  py -3 scripts\primary_atr35_short_runner.py   (from project root)
"""
import os
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

os.environ.setdefault("KC_ACCOUNT_NAME", "account1")
os.environ.setdefault("KC_DYNAMIC_MGMT", "1")      # dyn TP/SL (unchanged)
os.environ.setdefault("KC_DIRECTION_FILTER", "SHORT")  # treatment: short only
os.environ.setdefault("KC_ATR_MIN", "35")              # treatment: ATR >= 35

sys.path.insert(0, str(ROOT))
os.chdir(ROOT)  # run_kc_live.py uses relative log paths

runpy.run_path(str(ROOT / "run_kc_live.py"), run_name="__main__")
