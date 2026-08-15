"""Live shadow runner entry point: KC + slope filter on account2 (real fills).

Thin wrapper around run_kc_live.py that injects the account2/slope config via
env vars BEFORE config.py is imported. Exists as a separate file so process
lists (and scripts/kc-autostart.ps1) can distinguish it from the primary
run_kc_live.py instance.

Run:  py -3 scripts\shadow_live_runner.py   (from project root)
"""
import os
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

os.environ.setdefault("KC_ACCOUNT_NAME", "account2")
os.environ.setdefault("KC_SLOPE_K", "10")
os.environ.setdefault("KC_SLOPE_T", "0.1")

sys.path.insert(0, str(ROOT))
os.chdir(ROOT)  # run_kc_live.py uses relative log paths

runpy.run_path(str(ROOT / "run_kc_live.py"), run_name="__main__")
