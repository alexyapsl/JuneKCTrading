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
- Log both bar + KC values to logs/kc_YYYY-Www.jsonl (weekly, full US session)
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
from typing import Optional, List

from trading_ig import IGService, IGStreamService
from lightstreamer.client import Subscription as LSSubscription, SubscriptionListener
from dotenv import load_dotenv

# ====================== CONFIG ======================
load_dotenv()

USERNAME   = os.getenv("IG_USERNAME")
PASSWORD   = os.getenv("IG_PASSWORD")
API_KEY    = os.getenv("IG_API_KEY")
ACC_TYPE   = os.getenv("IG_ACC_TYPE", "DEMO")

EPIC             = "IX.D.DOW.IFS.IP"
RESOLUTION       = "1MINUTE"
TARGET_MINUTES   = 3

KC_PERIOD        = 13
KC_MULTIPLIER    = 1.6

HEARTBEAT_MINUTES = 15   # Print a heartbeat to terminal (not log) every N minutes

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / "kc_stream.log", encoding="utf-8")
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
    y, w, _ = bucket.isocalendar()
    week_str = f"{y}-W{w:02d}"
    return LOG_DIR / f"{prefix}_{week_str}.jsonl"


def append_jsonl(filepath: Path, record: dict):
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


# ====================== CANDLE AGGREGATION + KC ======================
current_candles = {}
last_bucket = None
kc = KeltnerChannel(period=KC_PERIOD, multiplier=KC_MULTIPLIER)


def get_3min_bucket(dt: datetime) -> datetime:
    minute = (dt.minute // TARGET_MINUTES) * TARGET_MINUTES
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

            record = {
                "timestamp_utc": last_bucket.isoformat(),
                "open": prev["open"],
                "high": prev["high"],
                "low": prev["low"],
                "close": prev["close"],
                "resolution": f"{TARGET_MINUTES}min",
                "epic": EPIC,
                "kc": {
                    "mid": kc_values.mid,
                    "upper": kc_values.upper,
                    "lower": kc_values.lower,
                    "atr": kc_values.atr,
                    "period": KC_PERIOD,
                    "multiplier": KC_MULTIPLIER,
                }
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
        logger.error("Missing IG credentials in .env file")
        return

    logger.info(f"3-min bars + KC (period={KC_PERIOD}, mult={KC_MULTIPLIER}) → logs/kc_*.jsonl")
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