"""Shadow slope-filter experiment runner.

Runs the KC strategy in parallel with the live runner WITHOUT a second IG
connection: it tails the primary experiment's JSONL bar feed
(logs/experiments/<primary_id>/kc_YYYY-Www.jsonl) and applies the exact
backtest semantics from scripts/backtest_slope.py / backtest_multiplier.py:

- Signal: SHORT if bar opens above prev upper band and closes back below it
          LONG  if bar opens below prev lower band and closes back above it
- SLOPE REGIME FILTER (the experiment):
      slope_norm = (mid[i] - mid[i-k]) / (k * atr[i])
      slope_norm > +T  -> uptrend   -> LONG only
      slope_norm < -T  -> downtrend -> SHORT only
      else             -> range     -> both directions
- Entry offset 3.0 pts, stop offset 3.0 pts, target = opposite band at signal
- RR filter: reward/risk >= 1.75 else signal rejected
- Cooldown: same direction within 2 bars skipped
- Fill window: next 3 bars (pending_bar_timeout), same-bar stop-first exit
- Filled position timeout: close at market (bar close) after 10 bars

Output: logs/experiments/<shadow_config_id>/
  - experiment_config.json
  - kc_YYYY-Www.jsonl   (same record shape as primary, for plot_kc.py)
  - shadow_trades.jsonl (completed trades)
  - shadow_state.json   (resume state: positions, pending, cooldown)
  - shadow_stream.log
  - processed_signals.json
"""
import json
import logging
import sys
import time
import hashlib
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ====================== EXPERIMENT CONFIG ======================
PRIMARY_CONFIG_ID = "de5459e4"

SHADOW_CFG = {
    "bar_minutes": 3,
    "kc_period": 13,
    "kc_multiplier": 1.6,
    "entry_offset": 3.0,
    "stop_offset": 3.0,
    "offset_mode": "points",
    "account_name": "shadow",          # simulated fills only, no IG orders
    "paper_trading": True,
    "size": 1.0,
    "min_risk_reward": 1.75,
    "pending_bar_timeout": 3,
    "filled_bar_timeout": 10,
    "cooldown_bars": 2,
    "slope_k": 10,
    "slope_T": 0.10,
    "version": 2,
    "shadow_of": PRIMARY_CONFIG_ID,
    "feed": "primary_jsonl_tail",
}

EXP_NAME = (
    f"kc_p{SHADOW_CFG['kc_period']}_m{SHADOW_CFG['kc_multiplier']}"
    f"_e{SHADOW_CFG['entry_offset']}_s{SHADOW_CFG['stop_offset']}"
    f"_b{SHADOW_CFG['bar_minutes']}"
    f"_slope_k{SHADOW_CFG['slope_k']}_T{SHADOW_CFG['slope_T']}"
    f"_v{SHADOW_CFG['version']}"
)
CONFIG_ID = hashlib.md5(
    json.dumps(SHADOW_CFG, sort_keys=True).encode("utf-8")
).hexdigest()[:8]

EXP_DIR = ROOT / "logs" / "experiments" / CONFIG_ID
EXP_DIR.mkdir(parents=True, exist_ok=True)
(ROOT / "results" / "experiments" / CONFIG_ID).mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(EXP_DIR / "shadow_stream.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("shadow_slope")

PRIMARY_DIR = ROOT / "logs" / "experiments" / PRIMARY_CONFIG_ID
TRADES_FILE = EXP_DIR / "shadow_trades.jsonl"
STATE_FILE = EXP_DIR / "shadow_state.json"
PROCESSED_FILE = EXP_DIR / "processed_signals.json"

# ====================== STATE ======================
class ShadowState:
    def __init__(self):
        self.prev_upper = None
        self.prev_lower = None
        self.mids = deque(maxlen=SHADOW_CFG["slope_k"] + 1)  # incl current
        self.last_sig_idx = None
        self.last_sig_dir = None
        self.bar_idx = -1
        self.last_ts = None            # ISO string of last processed bar
        self.pending = []              # working orders
        self.positions = []            # filled positions
        self.processed_signal_ids = []
        self.signals = 0
        self.blocked_slope = 0
        self.skipped_rr = 0

    def save(self):
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "prev_upper": self.prev_upper,
            "prev_lower": self.prev_lower,
            "mids": list(self.mids),
            "last_sig_idx": self.last_sig_idx,
            "last_sig_dir": self.last_sig_dir,
            "bar_idx": self.bar_idx,
            "last_ts": self.last_ts,
            "pending": self.pending,
            "positions": self.positions,
            "processed_signal_ids": self.processed_signal_ids,
            "signals": self.signals,
            "blocked_slope": self.blocked_slope,
            "skipped_rr": self.skipped_rr,
        }, indent=2), encoding="utf-8")
        tmp.replace(STATE_FILE)
        PROCESSED_FILE.write_text(json.dumps(
            {"processed_signal_ids": self.processed_signal_ids}, indent=2
        ), encoding="utf-8")

    @classmethod
    def load(cls):
        s = cls()
        if STATE_FILE.exists():
            try:
                d = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                s.prev_upper = d["prev_upper"]
                s.prev_lower = d["prev_lower"]
                s.mids = deque(d["mids"], maxlen=SHADOW_CFG["slope_k"] + 1)
                s.last_sig_idx = d["last_sig_idx"]
                s.last_sig_dir = d["last_sig_dir"]
                s.bar_idx = d["bar_idx"]
                s.last_ts = d["last_ts"]
                s.pending = d["pending"]
                s.positions = d["positions"]
                s.processed_signal_ids = d["processed_signal_ids"]
                s.signals = d.get("signals", 0)
                s.blocked_slope = d.get("blocked_slope", 0)
                s.skipped_rr = d.get("skipped_rr", 0)
                log.info(f"[RESUME] state loaded: bar_idx={s.bar_idx} last_ts={s.last_ts} "
                         f"pending={len(s.pending)} open={len(s.positions)}")
            except Exception as e:
                log.warning(f"[RESUME] failed to load state ({e}); starting fresh")
        return s


def week_file(ts: datetime) -> Path:
    y, w, _ = ts.isocalendar()
    return EXP_DIR / f"kc_{y}-W{w:02d}.jsonl"


def append_jsonl(path: Path, record: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def append_trade(tr: dict):
    append_jsonl(TRADES_FILE, tr)


def current_primary_file() -> Path:
    """Most recent weekly file in the primary experiment dir."""
    now = datetime.now(timezone.utc)
    y, w, _ = now.isocalendar()
    cand = PRIMARY_DIR / f"kc_{y}-W{w:02d}.jsonl"
    if cand.exists():
        return cand
    files = sorted(PRIMARY_DIR.glob("kc_*.jsonl"),
                   key=lambda p: p.stat().st_mtime)
    if not files:
        raise FileNotFoundError(f"no primary jsonl files in {PRIMARY_DIR}")
    return files[-1]

# ====================== CORE LOGIC (mirrors backtest exactly) ======================
def process_bar(st: ShadowState, rec: dict, record_live: bool = True, warmup: bool = False):
    ts = datetime.fromisoformat(rec["timestamp_utc"])
    o, h, l, c = rec["open"], rec["high"], rec["low"], rec["close"]
    mid, atr = rec["kc"]["mid"], rec["kc"]["atr"]
    mult = SHADOW_CFG["kc_multiplier"]
    upper = round(mid + mult * atr, 4)
    lower = round(mid - mult * atr, 4)

    i = st.bar_idx + 1
    st.bar_idx = i

    if warmup:
        # State warmup only: advance bands/mids so the slope filter and prev-band
        # rules have correct context. No signals, positions, or output.
        st.prev_upper, st.prev_lower = upper, lower
        st.mids.append(mid)
        st.last_ts = ts.isoformat()
        return

    # --- manage open positions ---
    for pos in list(st.positions):
        if pos["fill_idx"] == i:
            continue  # resolved at fill time
        exit_price = exit_result = None
        if pos["dir"] == "LONG":
            if l <= pos["stop"]:
                exit_price, exit_result = pos["stop"], "STOP"
            elif h >= pos["target"]:
                exit_price, exit_result = pos["target"], "TARGET"
        else:
            if h >= pos["stop"]:
                exit_price, exit_result = pos["stop"], "STOP"
            elif l <= pos["target"]:
                exit_price, exit_result = pos["target"], "TARGET"
        if exit_result is None and (i - pos["fill_idx"]) >= SHADOW_CFG["filled_bar_timeout"]:
            exit_price, exit_result = c, "TIMEOUT"
        if exit_result is not None:
            pos["exit"] = exit_price
            pos["exit_ts"] = ts.isoformat()
            pos["result"] = exit_result
            pnl = (exit_price - pos["entry"]) if pos["dir"] == "LONG" else (pos["entry"] - exit_price)
            pos["pnl"] = round(pnl, 1)
            st.positions.remove(pos)
            append_trade(pos)
            log.info(f"[EXIT] {pos['dir']} {pos['signal_id']} {exit_result} "
                     f"exit={exit_price:.1f} pnl={pnl:+.1f}")

    # --- manage pending orders ---
    for po in list(st.pending):
        filled = (h >= po["entry"]) if po["dir"] == "LONG" else (l <= po["entry"])
        if filled:
            st.pending.remove(po)
            pos = dict(po)
            pos["fill_idx"] = i
            pos["fill_ts"] = ts.isoformat()
            # same-bar resolution, stop-first
            exit_price = exit_result = None
            if pos["dir"] == "LONG":
                if l <= pos["stop"]:
                    exit_price, exit_result = pos["stop"], "STOP"
                elif h >= pos["target"]:
                    exit_price, exit_result = pos["target"], "TARGET"
            else:
                if h >= pos["stop"]:
                    exit_price, exit_result = pos["stop"], "STOP"
                elif l <= pos["target"]:
                    exit_price, exit_result = pos["target"], "TARGET"
            if exit_result is not None:
                pos["exit"] = exit_price
                pos["exit_ts"] = ts.isoformat()
                pos["result"] = exit_result
                pnl = (exit_price - pos["entry"]) if pos["dir"] == "LONG" else (pos["entry"] - exit_price)
                pos["pnl"] = round(pnl, 1)
                append_trade(pos)
                log.info(f"[FILL+EXIT] {pos['dir']} {pos['signal_id']} {exit_result} "
                         f"entry={pos['entry']:.1f} exit={exit_price:.1f} pnl={pnl:+.1f}")
            else:
                st.positions.append(pos)
                log.info(f"[FILL] {pos['dir']} {pos['signal_id']} entry={pos['entry']:.1f} "
                         f"stop={pos['stop']:.1f} target={pos['target']:.1f}")
        elif (i - po["sig_idx"]) >= SHADOW_CFG["pending_bar_timeout"]:
            st.pending.remove(po)
            log.info(f"[EXPIRE] {po['dir']} {po['signal_id']} not filled in "
                     f"{SHADOW_CFG['pending_bar_timeout']} bars")

    # --- signal detection with slope filter ---
    signal_payload = None
    execution = None
    if st.prev_upper is not None:
        direction = None
        if o > st.prev_upper and c < st.prev_upper:
            direction = "SHORT"
        elif o < st.prev_lower and c > st.prev_lower:
            direction = "LONG"

        if direction:
            in_cooldown = (
                st.last_sig_idx is not None
                and direction == st.last_sig_dir
                and (i - st.last_sig_idx) < SHADOW_CFG["cooldown_bars"]
            )
            if not in_cooldown:
                st.last_sig_idx = i
                st.last_sig_dir = direction

                # slope regime
                slope_norm = None
                regime = "range"
                if len(st.mids) >= SHADOW_CFG["slope_k"] + 1:
                    mid_k = st.mids[0]
                    slope_norm = (mid - mid_k) / (SHADOW_CFG["slope_k"] * atr) if atr > 0 else 0.0
                    if slope_norm > SHADOW_CFG["slope_T"]:
                        regime = "up"
                    elif slope_norm < -SHADOW_CFG["slope_T"]:
                        regime = "down"

                blocked = (
                    (regime == "up" and direction == "SHORT")
                    or (regime == "down" and direction == "LONG")
                )

                sig_ts = ts.strftime("%Y%m%d_%H%M")
                signal_id = f"sig_{sig_ts}_{direction.lower()}"

                if direction == "SHORT":
                    entry = round(l - SHADOW_CFG["entry_offset"], 4)
                    stop = round(h + SHADOW_CFG["stop_offset"], 4)
                    target = lower
                    risk = stop - entry
                    reward = entry - target
                else:
                    entry = round(h + SHADOW_CFG["entry_offset"], 4)
                    stop = round(l - SHADOW_CFG["stop_offset"], 4)
                    target = upper
                    risk = entry - stop
                    reward = target - entry
                rr = round(reward / risk, 4) if risk > 0 else 0.0

                if blocked:
                    st.blocked_slope += 1
                    execution = {"status": "BLOCKED_SLOPE", "rr": rr,
                                 "regime": regime, "slope_norm": round(slope_norm, 4) if slope_norm is not None else None,
                                 "reason": f"{direction} blocked in {regime}trend"}
                    log.info(f"[SIGNAL] {direction} {signal_id} BLOCKED by slope filter "
                             f"(regime={regime}, slope_norm={slope_norm:+.3f})")
                elif rr < SHADOW_CFG["min_risk_reward"]:
                    st.skipped_rr += 1
                    execution = {"status": "IGNORED_RR", "rr": rr, "regime": regime,
                                 "reason": f"RR < {SHADOW_CFG['min_risk_reward']}"}
                    log.info(f"[SIGNAL] {direction} {signal_id} rejected: RR={rr} < {SHADOW_CFG['min_risk_reward']}")
                else:
                    st.signals += 1
                    st.processed_signal_ids.append(signal_id)
                    st.pending.append({
                        "signal_id": signal_id, "dir": direction,
                        "entry": entry, "stop": stop, "target": round(target, 4),
                        "rr": rr, "regime": regime,
                        "slope_norm": round(slope_norm, 4) if slope_norm is not None else None,
                        "sig_ts": ts.isoformat(), "sig_idx": i,
                        "experiment_name": EXP_NAME, "config_id": CONFIG_ID,
                    })
                    execution = {"status": "WORKING", "rr": rr, "regime": regime,
                                 "deal_reference": None, "deal_id": None}
                    log.info(f"[SIGNAL] {direction} {signal_id} entry={entry:.1f} "
                             f"stop={stop:.1f} target={target:.1f} rr={rr} regime={regime} -> WORKING")

                signal_payload = {
                    "signal_id": signal_id, "direction": direction,
                    "entry_price": entry, "stop_loss": stop,
                    "experiment_name": EXP_NAME, "config_id": CONFIG_ID,
                }
            else:
                log.info(f"[SIGNAL] {direction} skipped (cooldown)")

    st.prev_upper, st.prev_lower = upper, lower
    st.mids.append(mid)
    st.last_ts = ts.isoformat()

    if record_live:
        append_jsonl(week_file(ts), {
            "timestamp_utc": ts.isoformat(),
            "open": o, "high": h, "low": l, "close": c,
            "resolution": rec.get("resolution", "3min"),
            "epic": rec.get("epic", "IX.D.DOW.IFS.IP"),
            "kc": rec["kc"],
            "signal": signal_payload,
            "execution": execution,
            "experiment_name": EXP_NAME,
            "config_id": CONFIG_ID,
        })

# ====================== MAIN LOOP ======================
def main():
    log.info(f"=== Shadow slope runner starting ===")
    log.info(f"experiment_name={EXP_NAME}")
    log.info(f"config_id={CONFIG_ID}  shadow_of={PRIMARY_CONFIG_ID}")
    (EXP_DIR / "experiment_config.json").write_text(
        json.dumps({**SHADOW_CFG, "experiment_name": EXP_NAME,
                    "config_id": CONFIG_ID}, indent=2),
        encoding="utf-8",
    )

    st = ShadowState.load()
    fresh_boot = st.last_ts is None

    # --- warmup / catch-up pass ---
    pf = current_primary_file()
    catchup = 0
    with open(pf, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            ts = rec["timestamp_utc"]
            if st.last_ts is not None and ts <= st.last_ts:
                continue
            # Fresh boot: warmup only (no phantom signals from before launch).
            # Resumed: full catch-up of bars missed while the shadow was down.
            process_bar(st, rec, record_live=not fresh_boot, warmup=fresh_boot)
            catchup += 1
    if catchup:
        mode = "warmup" if fresh_boot else "catchup"
        log.info(f"[{mode.upper()}] processed {catchup} historical bars from {pf.name}")
    st.save()

    offset = pf.stat().st_size
    cur_file = pf
    log.info(f"[TAIL] following {cur_file.name} @ offset {offset}")

    while True:
        try:
            now_file = current_primary_file()
            if now_file != cur_file:
                log.info(f"[TAIL] week rollover -> {now_file.name}")
                cur_file = now_file
                offset = 0
            size = cur_file.stat().st_size
            if size < offset:  # truncated/rotated
                log.warning("[TAIL] file shrank; resetting offset")
                offset = 0
            if size > offset:
                with open(cur_file, encoding="utf-8") as f:
                    f.seek(offset)
                    chunk = f.read()
                    offset = f.tell()
                # hold back a trailing partial line for the next pass
                if chunk and not chunk.endswith("\n"):
                    cut = chunk.rfind("\n")
                    if cut == -1:
                        offset -= len(chunk.encode("utf-8"))
                        time.sleep(5)
                        continue
                    offset -= len(chunk[cut + 1:].encode("utf-8"))
                    chunk = chunk[:cut + 1]
                for line in chunk.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue  # partial line at EOF; picked up next pass
                    ts = rec["timestamp_utc"]
                    if st.last_ts is not None and ts <= st.last_ts:
                        continue
                    process_bar(st, rec, record_live=True)
                    st.save()
        except Exception as e:
            log.exception(f"[LOOP] error: {e}")
        time.sleep(5)


if __name__ == "__main__":
    main()
