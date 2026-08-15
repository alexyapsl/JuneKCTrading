"""
Experiment Configuration for JuneKCTrading

Single source of truth for all experiment parameters.
This file defines the Keltner Channel settings, signal rules,
and auto-generates a descriptive experiment_name + config_id.

Future P&L data from IG will be tagged with the same experiment_name.
"""

from dataclasses import dataclass, field
from typing import Literal
import hashlib
import json
import os
from pathlib import Path


@dataclass
class ExperimentConfig:
    # === Candle Aggregation ===
    bar_minutes: int = 3

    # === Keltner Channel Parameters ===
    kc_period: int = 13
    kc_multiplier: float = 1.6

    # === Signal Rules (configurable for experimentation) ===
    entry_offset: float = 3.0      # points above signal high (long) or below signal low (short)
    stop_offset: float = 3.0       # points beyond opposite extreme for stop loss
    offset_mode: Literal["points", "atr_multiple"] = "points"  # "atr_multiple" uses stop_offset as multiplier of ATR

    # === Order Execution (Phase 1) ===
    account_name: str = "account1"          # Which account credential set to use
    paper_trading: bool = True              # True -> use .demo credentials, False -> .live
    size: float = 1.0                       # Position size in £ per point
    min_risk_reward: float = 1.75           # Minimum acceptable RR ratio at signal time (v2: was 1.5; sweep on W31-W32 signals showed 1.75 peak: +£705 sim vs +£183)
    pending_bar_timeout: int = 3            # Cancel working order after N bars
    filled_bar_timeout: int = 10            # Close filled position at market after N bars

    # === Slope Regime Filter (optional; slope_k=0 disables) ===
    # slope_norm = (mid[i] - mid[i-k]) / (k * atr[i])
    #   > +T -> uptrend (LONG only) | < -T -> downtrend (SHORT only) | else both
    slope_k: int = 0
    slope_T: float = 0.10

    # === Experiment Identity (auto-generated) ===
    version: int = 2
    experiment_name: str = field(init=False, repr=False)
    config_id: str = field(init=False, repr=False)

    def __post_init__(self):
        self.experiment_name = self._generate_experiment_name()
        self.config_id = self._generate_config_id()

    def _generate_experiment_name(self) -> str:
        """
        Generate a human-readable experiment name that encodes the key parameters.
        Example: kc_p13_m1.6_e3.0_s3.0_b3_v1
        """
        name = (
            f"kc_p{self.kc_period}_m{self.kc_multiplier}"
            f"_e{self.entry_offset}_s{self.stop_offset}"
            f"_b{self.bar_minutes}"
        )
        if self.slope_k > 0:
            name += f"_slope_k{self.slope_k}_T{self.slope_T}"
        return name + f"_v{self.version}"

    def _generate_config_id(self) -> str:
        """
        Generate a short deterministic hash of the full configuration.
        Useful for database keys or folder names.
        """
        key = {
            "bar_minutes": self.bar_minutes,
            "kc_period": self.kc_period,
            "kc_multiplier": self.kc_multiplier,
            "entry_offset": self.entry_offset,
            "stop_offset": self.stop_offset,
            "offset_mode": self.offset_mode,
            "account_name": self.account_name,
            "paper_trading": self.paper_trading,
            "size": self.size,
            "min_risk_reward": self.min_risk_reward,
            "version": self.version,
        }
        # Only mix slope params into the hash when the filter is active, so the
        # default (filter-off) config keeps its historical config_id.
        if self.slope_k > 0:
            key["slope_k"] = self.slope_k
            key["slope_T"] = self.slope_T
        raw = json.dumps(key, sort_keys=True).encode("utf-8")
        return hashlib.md5(raw).hexdigest()[:8]

    def as_dict(self) -> dict:
        """Return the full config as a dictionary (for logging)."""
        return {
            "bar_minutes": self.bar_minutes,
            "kc_period": self.kc_period,
            "kc_multiplier": self.kc_multiplier,
            "entry_offset": self.entry_offset,
            "stop_offset": self.stop_offset,
            "offset_mode": self.offset_mode,
            "account_name": self.account_name,
            "paper_trading": self.paper_trading,
            "size": self.size,
            "min_risk_reward": self.min_risk_reward,
            "pending_bar_timeout": self.pending_bar_timeout,
            "filled_bar_timeout": self.filled_bar_timeout,
            "slope_k": self.slope_k,
            "slope_T": self.slope_T,
            "version": self.version,
            "experiment_name": self.experiment_name,
            "config_id": self.config_id,
        }

    # === Experiment Output Paths ===
    @property
    def experiment_dir(self) -> Path:
        """Base directory for this experiment's logs (logs/experiments/<config_id>/)."""
        return Path("logs") / "experiments" / self.config_id

    @property
    def results_dir(self) -> Path:
        """Base directory for this experiment's charts/results (results/experiments/<config_id>/)."""
        return Path("results") / "experiments" / self.config_id

    def ensure_dirs(self) -> None:
        """Create the experiment directories if they don't exist."""
        self.experiment_dir.mkdir(parents=True, exist_ok=True)
        self.results_dir.mkdir(parents=True, exist_ok=True)


# === Global Instance ===
# All other modules should import CONFIG from here.
# Env overrides let a second runner instance (e.g. the slope-filter shadow on
# account2) reuse the same code without touching the primary's config.
CONFIG = ExperimentConfig(
    account_name=os.environ.get("KC_ACCOUNT_NAME", "account1"),
    slope_k=int(os.environ.get("KC_SLOPE_K", "0")),
    slope_T=float(os.environ.get("KC_SLOPE_T", "0.10")),
)


def get_config() -> ExperimentConfig:
    """Helper for explicit access if needed."""
    return CONFIG


# Quick sanity check when running this file directly
if __name__ == "__main__":
    CONFIG.ensure_dirs()
    print("ExperimentConfig loaded:")
    print(f"  experiment_name : {CONFIG.experiment_name}")
    print(f"  config_id       : {CONFIG.config_id}")
    print(f"  experiment_dir  : {CONFIG.experiment_dir}")
    print(f"  results_dir     : {CONFIG.results_dir}")
    print(f"  config_dict     : {CONFIG.as_dict()}")