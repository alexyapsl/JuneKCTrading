"""
KC Live Runner - Single file that does both:
- Streams 3-minute Dow candles from IG
- Calculates Keltner Channels in real time
- Auto-reconnects if the Lightstreamer connection drops

Just run:
    python run_kc_live.py

It will:
- Stream bars continuously (with auto-reconnect)
- Compute KC (period=13, multiplier=1.6 by default)
- Log both bar + KC values to logs/experiments/<config_id>/kc_YYYY-Www.jsonl
- Print clean output to console

Requirements:
    pip install trading-ig python-dotenv lightstreamer-client
"""

import os
import json
import time
import signal
import logging
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, List, TYPE_CHECKING

if TYPE_CHECKING:
    from src.signal_detector import Signal

from trading_ig import IGService, IGStreamService
from lightstreamer.client import Subscription as LSSubscription, SubscriptionListener
from dotenv import load_dotenv

# ====================== CONFIG ======================
load_dotenv()

from config import CONFIG
# Import signal detector (works when running as script from project root)
try:
    from src.signal_detector import SignalDetector, Signal
except ModuleNotFoundError:
    import sys
    sys.path.append(str(Path(__file__).parent))
    from src.signal_detector import SignalDetector, Signal

# Phase 1 – Order execution (conditional import so existing runs still work)
ORDER_MANAGER_AVAILABLE = False
try:
    from src.order_manager import OrderManager
    ORDER_MANAGER_AVAILABLE = True
except Exception:
    ORDER_MANAGER_AVAILABLE = False  # Will initialize later if needed

# Ensure experiment directories exist on import
CONFIG.ensure_dirs()

# Unified credential resolution (Phase 1)
# Prefers accountX.env.demo/live files. Falls back to legacy .env if not found.
try:
    from src.account_resolver import resolve_credentials
    _ACCOUNT_CREDS = resolve_credentials(
        account_name=CONFIG.account_name,
        paper_trading=CONFIG.paper_trading
    )
    USERNAME = _ACCOUNT_CREDS["username"]
    PASSWORD = _ACCOUNT_CREDS["password"]
    API_KEY  = _ACCOUNT_CREDS["api_key"]
    ACC_TYPE = _ACCOUNT_CREDS["acc_type"]
    logger.info(f"[CREDENTIALS] Using account '{CONFIG.account_name}' (paper_trading={CONFIG.paper_trading}) -> {_ACCOUNT_CREDS.get('credential_file')}")
except Exception as e:
    logger.warning(f"AccountResolver failed ({e}), falling back to legacy .env")
    USERNAME = os.getenv("IG_USERNAME")
    PASSWORD = os.getenv("IG_PASSWORD")
    API_KEY  = os.getenv("IG_API_KEY")
    ACC_TYPE = os.getenv("IG_ACC_TYPE", "DEMO")

EPIC             = "IX.D.DOW.IFS.IP"
RESOLUTION       = "1MINUTE"

HEARTBEAT_MINUTES = 15   # Print a heartbeat to terminal (not log) every N minutes

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

# Experiment-specific log directory (logs/experiments/<config_id>/)
EXP_LOG_DIR = CONFIG.experiment_dir
EXP_LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(EXP_LOG_DIR / "kc_stream.log", encoding="utf-8")
    ]
)
logger = logging.getLogger(__name__)


# ====================== DATA CLASSES ======================
@dataclass
class Bar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float


@dataclass
class KeltnerValues:
    mid: float
    upper: float
    lower: float
    atr: float


# ====================== KELTNER CHANNEL ======================
class KeltnerChannel:
    """Stateful Keltner Channel calculator (EMA + Wilder ATR)."""

    def __init__(self, period: int = 13, multiplier: float = 1.6):
        if period < 1:
            raise ValueError("period must be >= 1")
        if multiplier <= 0:
            raise ValueError("multiplier must be > 0")

        self.period = period
        self.multiplier = multiplier
        self.alpha = 1.0 / period

        self._ema: Optional[float] = None
        self._atr: Optional[float] = None
        self._prev_close: Optional[float] = None
        self._initialized = False

    def warmup(self, bars: List[Bar]) -> None:
        if not bars:
            return
        first = bars[0]
        self._ema = float(first.close)
        self._atr = float(first.high) - float(first.low)
        self._prev_close = float(first.close)

        for bar in bars[1:]:
            self._update_internal(bar)
        self._initialized = True

    def update(self, bar: Bar) -> KeltnerValues:
        if not self._initialized:
            self.warmup([bar])
        else:
            self._update_internal(bar)

        if self._ema is None or self._atr is None:
            raise RuntimeError("KeltnerChannel internal state is invalid")

        mid = self._ema
        atr = self._atr
        offset = atr * self.multiplier

        return KeltnerValues(
            mid=round(mid, 4),
            upper=round(mid + offset, 4),
            lower=round(mid - offset, 4),
            atr=round(atr, 4),
        )

    def _update_internal(self, bar: Bar) -> None:
        close = float(bar.close)
        high = float(bar.high)
        low = float(bar.low)

        tr = self._true_range(bar, self._prev_close)
        self._ema = self.alpha * close + (1 - self.alpha) * self._ema
        self._atr = self.alpha * tr + (1 - self.alpha) * self._atr
        self._prev_close = close

    @staticmethod
    def _true_range(bar: Bar, prev_close: Optional[float]) -> float:
        high = float(bar.high)
        low = float(bar.low)
        if prev_close is None:
            return high - low
        prev = float(prev_close)
        tr1 = high - low
        tr2 = abs(high - prev)
        tr3 = abs(low - prev)
        return max(tr1, tr2, tr3)

    def reset(self) -> None:
        self._ema = None
        self._atr = None
        self._prev_close = None
        self._initialized = False


# ====================== JSONL HELPERS ======================
def get_log_filename(bucket: datetime, prefix: str = "kc") -> Path:
    """Return path inside the experiment directory (logs/experiments/<config_id>/)."""
    y, w, _ = bucket.isocalendar()
    week_str = f"{y}-W{w:02d}"
    return EXP_LOG_DIR / f"{prefix}_{week_str}.jsonl"


def append_jsonl(filepath: Path, record: dict):
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


# ====================== CANDLE AGGREGATION + KC ======================
current_candles = {}
last_bucket = None
kc = KeltnerChannel(period=CONFIG.kc_period, multiplier=CONFIG.kc_multiplier)
detector = SignalDetector()


def get_3min_bucket(dt: datetime) -> datetime:
    minute = (dt.minute // CONFIG.bar_minutes) * CONFIG.bar_minutes
    return dt.replace(minute=minute, second=0, microsecond=0, tzinfo=timezone.utc)


def process_1min_candle(values: dict):
    global last_bucket

    now = datetime.now(timezone.utc)
    bucket = get_3min_bucket(now)

    try:
        o = float(values.get("OFR_OPEN")  or values.get("BID_OPEN", 0))
        h = float(values.get("OFR_HIGH")  or values.get("BID_HIGH", 0))
        l = float(values.get("OFR_LOW")   or values.get("BID_LOW", 0))
        c = float(values.get("OFR_CLOSE") or values.get("BID_CLOSE", 0))
    except (TypeError, ValueError):
        return

    if bucket != last_bucket:
        # Save previous candle + compute KC
        if last_bucket and last_bucket in current_candles:
            prev = current_candles[last_bucket]
            bar = Bar(
                timestamp=last_bucket,
                open=prev["open"],
                high=prev["high"],
                low=prev["low"],
                close=prev["close"],
            )

            kc_values = kc.update(bar)

            # === Signal Detection ===
            signal_obj: Optional[Signal] = detector.check(bar, kc_values)
            signal_payload = None
            if signal_obj:
                signal_payload = {
                    "signal_id": signal_obj.signal_id,
                    "direction": signal_obj.direction,
                    "entry_price": signal_obj.entry_price,
                    "stop_loss": signal_obj.stop_loss,
                    "experiment_name": signal_obj.experiment_name,
                    "config_id": signal_obj.config_id,
                }
                logger.info(
                    f"[SIGNAL] {signal_obj.direction} | entry={signal_obj.entry_price:.2f} "
                    f"stop={signal_obj.stop_loss:.2f} | {signal_obj.experiment_name}"
                )

                # === Phase 1: Order Placement ===
                if ORDER_MANAGER_AVAILABLE:
                    try:
                        # Lazy-init OrderManager on first signal
                        if "order_manager" not in globals():
                            global order_manager
                            order_manager = OrderManager(experiment_dir=CONFIG.experiment_dir)
                        order_manager.place(signal_obj, kc_values)
                    except Exception as e:
                        logger.error(f"[ORDER] Failed to process signal: {e}")

            record = {
                "timestamp_utc": last_bucket.isoformat(),
                "open": prev["open"],
                "high": prev["high"],
                "low": prev["low"],
                "close": prev["close"],
                "resolution": f"{CONFIG.bar_minutes}min",
                "epic": EPIC,
                "kc": {
                    "mid": kc_values.mid,
                    "upper": kc_values.upper,
                    "lower": kc_values.lower,
                    "atr": kc_values.atr,
                    "period": CONFIG.kc_period,
                    "multiplier": CONFIG.kc_multiplier,
                },
                "signal": signal_payload,          # None or signal dict
                "experiment_name": CONFIG.experiment_name,
                "config_id": CONFIG.config_id,
            }

            log_file = get_log_filename(last_bucket)
            append_jsonl(log_file, record)

            logger.info(
                f"[BAR+KC] {last_bucket.strftime('%H:%M')} "
                f"O:{prev['open']:.2f} H:{prev['high']:.2f} L:{prev['low']:.2f} C:{prev['close']:.2f} | "
                f"mid={kc_values.mid:,.2f} upper={kc_values.upper:,.2f} lower={kc_values.lower:,.2f} atr={kc_values.atr:,.2f}"
            )

        last_bucket = bucket
        current_candles[bucket] = {"open": o, "high": h, "low": l, "close": c}
    else:
        candle = current_candles.setdefault(bucket, {"open": o, "high": h, "low": l, "close": c})
        candle["high"] = max(candle.get("high", h), h)
        candle["low"] = min(candle.get("low", l), l)
        candle["close"] = c

    logger.debug(f"  [{now.strftime('%H:%M:%S')}] updating {bucket.strftime('%H:%M')} candle")


# ====================== LIGHTSTREAMER LISTENER ======================
class ChartListener(SubscriptionListener):
    def __init__(self, on_disconnect_callback=None):
        super().__init__()
        self.on_disconnect_callback = on_disconnect_callback

    def onListenStart(self):
        logger.info("[LS] Listener attached")

    def onListenEnd(self):
        logger.info("[LS] Listener removed")

    def onSubscription(self):
        logger.info("[LS] Subscription successful")

    def onUnsubscription(self):
        logger.warning("[LS] Unsubscribed — connection lost")
        if self.on_disconnect_callback:
            self.on_disconnect_callback()

    def onSubscriptionError(self, code, message):
        logger.error(f"[LS] Subscription error: code={code}, message={message}")
        if self.on_disconnect_callback:
            self.on_disconnect_callback()

    def onItemUpdate(self, update):
        try:
            values = update.getFields()
            if values:
                process_1min_candle(values)
        except Exception as e:
            logger.exception(f"[LS] Error: {e}")

    def onEndOfSnapshot(self, itemName, itemPos):
        logger.debug(f"[LS] Snapshot complete for {itemName}")


# ====================== MAIN ======================
def run_stream():
    """
    Create a fresh IG session + subscription.
    Returns (ig_stream, listener, disconnect_event) so caller can manage lifecycle.
    """
    ig_service = IGService(USERNAME, PASSWORD, API_KEY, ACC_TYPE)
    ig_service.create_session()

    ig_stream = IGStreamService(ig_service)
    ig_stream.create_session()

    item = f"CHART:{EPIC}:{RESOLUTION}"
    fields = ["OFR_OPEN", "OFR_HIGH", "OFR_LOW", "OFR_CLOSE",
              "BID_OPEN", "BID_HIGH", "BID_LOW", "BID_CLOSE"]

    # This flag will be set to True when unsubscription or error occurs
    disconnect_event = {"triggered": False}

    def trigger_reconnect():
        disconnect_event["triggered"] = True

    listener = ChartListener(on_disconnect_callback=trigger_reconnect)
    sub = LSSubscription("MERGE", [item], fields)
    sub.addListener(listener)

    ig_stream.ls_client.subscribe(sub)
    logger.info(f"[CONNECT] Subscribed to {item}")

    return ig_stream, listener, disconnect_event


def main():
    logger.info("=== KC Live Runner (Bars + Keltner Channels) ===")

    if not all([USERNAME, PASSWORD, API_KEY]):
        logger.error("Missing IG credentials (checked accountX.env.* and .env)")
        return

    # Persist the exact config for this experiment run
    config_file = EXP_LOG_DIR / "experiment_config.json"
    config_file.write_text(json.dumps(CONFIG.as_dict(), indent=2), encoding="utf-8")
    logger.info(f"Experiment config saved → {config_file}")

    logger.info(f"{CONFIG.bar_minutes}-min bars + KC (period={CONFIG.kc_period}, mult={CONFIG.kc_multiplier}) → {EXP_LOG_DIR}")
    logger.info("Auto-reconnect enabled. Press Ctrl+C to stop.\n")
    print(f"[HEARTBEAT] Every {HEARTBEAT_MINUTES} minutes to terminal only (not logged).\n", flush=True)

    MAX_RETRIES = 10
    BASE_DELAY = 5  # seconds

    retry_count = 0
    last_heartbeat = time.time()

    def shutdown(signum, frame):
        logger.info("Shutdown signal received...")
        logger.info("Exiting.")
        os._exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    while True:
        try:
            ig_stream, listener, disconnect_event = run_stream()
            retry_count = 0  # reset on successful connect
            last_heartbeat = time.time()  # reset heartbeat timer on fresh connect

            # Keep the main thread alive and watch for disconnects
            while True:
                time.sleep(2)

                # Terminal heartbeat (not logged)
                if time.time() - last_heartbeat >= HEARTBEAT_MINUTES * 60:
                    print(f"[HEARTBEAT] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - still running", flush=True)
                    last_heartbeat = time.time()

                if disconnect_event["triggered"]:
                    logger.warning("[RECONNECT] Connection lost detected. Cleaning up...")
                    try:
                        ig_stream.disconnect()
                    except Exception:
                        pass

                    retry_count += 1
                    if retry_count > MAX_RETRIES:
                        logger.error("[RECONNECT] Too many failures, giving up.")
                        return

                    delay = min(BASE_DELAY * (2 ** (retry_count - 1)), 120)
                    logger.info(f"[RECONNECT] Attempt {retry_count}/{MAX_RETRIES} in {delay}s...")
                    time.sleep(delay)
                    break  # break inner loop to reconnect

        except Exception as e:
            logger.exception(f"[FATAL] Unexpected error: {e}")
            retry_count += 1
            if retry_count > MAX_RETRIES:
                logger.error("Too many failures, exiting.")
                return
            delay = min(BASE_DELAY * (2 ** (retry_count - 1)), 120)
            logger.info(f"Retrying in {delay}s...")
            time.sleep(delay)


if __name__ == "__main__":
    main()