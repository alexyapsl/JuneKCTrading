"""Live shadow runner entry point: CONTROL runner on account2 (real fills).

Thin wrapper around run_kc_live.py that injects the account2/control config via
env vars BEFORE config.py is imported. Exists as a separate file so process
lists (and scripts/kc-autostart.ps1) can distinguish it from the primary
run_kc_live.py instance.

Role since 2026-10-25 (ATR>=35 short-only filter experiment):
  - CONTROL: identical to the OLD primary = v5 dynamic exit management
    (KC_DYNAMIC_MGMT=1), NO entry filters. Unfiltered baseline on account2.
  - Primary (account1, via scripts/primary_atr35_short_runner.py) is the
    TREATMENT: same dyn management but SHORT-only + ATR>=35 entry filter.
Role 2026-08-26 .. 2026-10-25 (exit-management A/B experiment, concluded):
  - Was the static-TP/SL control (KC_DYNAMIC_MGMT=0) vs the dyn primary.

Run:  py -3 scripts\shadow_live_runner.py   (from project root)
"""
import os
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

os.environ.setdefault("KC_ACCOUNT_NAME", "account2")
os.environ.setdefault("KC_SLOPE_K", "0")       # slope filter set aside (2026-08-26)
os.environ.setdefault("KC_DYNAMIC_MGMT", "1")  # control: dyn TP/SL (matches old primary)

sys.path.insert(0, str(ROOT))
os.chdir(ROOT)  # run_kc_live.py uses relative log paths

runpy.run_path(str(ROOT / "run_kc_live.py"), run_name="__main__")
