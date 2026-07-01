"""
IG Dow Jones 3-Minute Candle Streamer (Production Version)
==========================================================

Streams 1-minute OHLC from IG's Lightstreamer CHART endpoint for
IX.D.DOW.IFS.IP and aggregates into 3-minute candles.

Features:
- Proper Lightstreamer SubscriptionListener implementation
- 3-minute candle aggregation from 1-minute data
- Daily JSONL logging (append-only, safe for 24/7 operation)
- Graceful shutdown handling
- Clear debug / status messages

Requirements:
    pip install trading-ig python-dotenv lightstreamer-client

Usage:
    python ig_dow_candle_stream.py

Environment (.env file in same directory):
    IG_USERNAME=...
    IG_PASSWORD=...
    IG_API_KEY=...
    IG_ACC_TYPE=DEMO     # or LIVE

Author: Generated for Alex — JuneKCTrading project
Date: 2026-07-01
"""

import os
import json
import time
import signal
import logging
from datetime import datetime, timezone
from collections import defaultdict
from pathlib import Path

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
RESOLUTION       = "1MINUTE"          # IG native resolution
TARGET_MINUTES   = 3                  # Target candle size

# Always write logs to the project root (JuneKCTrading/logs/)
# regardless of where the script is executed from
LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / "stream.log", encoding="utf-8")
    ]
)
logger = logging.getLogger(__name__)


# ====================== JSONL HELPERS ======================
def get_log_filename(bucket: datetime, prefix: str = "dow_3min") -> Path:
    """Return daily rotating log file path."""
    date_str = bucket.strftime("%Y-%m-%d")
    return LOG_DIR / f"{prefix}_{date_str}.jsonl"


def append_jsonl(filepath: Path, record: dict):
    """Append one JSON object per line (JSONL format)."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ====================== CANDLE AGGREGATION ======================
current_candles = {}
last_bucket = None


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
        # New 3-min bucket → save previous candle
        if last_bucket and last_bucket in current_candles:
            prev = current_candles[last_bucket]
            record = {
                "timestamp_utc": last_bucket.isoformat(),
                "open":  prev["open"],
                "high":  prev["high"],
                "low":   prev["low"],
                "close": prev["close"],
                "resolution": f"{TARGET_MINUTES}min",
                "epic": EPIC
            }
            log_file = get_log_filename(last_bucket)
            append_jsonl(log_file, record)
            logger.info(f"[CANDLE] {last_bucket.strftime('%H:%M')} "
                        f"O:{prev['open']:.2f} H:{prev['high']:.2f} "
                        f"L:{prev['low']:.2f} C:{prev['close']:.2f} → {log_file.name}")

        last_bucket = bucket
        current_candles[bucket] = {"open": o, "high": h, "low": l, "close": c}
    else:
        # Update current 3-min candle
        candle = current_candles.setdefault(bucket, {"open": o, "high": h, "low": l, "close": c})
        candle["high"] = max(candle.get("high", h), h)
        candle["low"]  = min(candle.get("low",  l), l)
        candle["close"] = c

    # Live tick inside the bucket
    logger.debug(f"  [{now.strftime('%H:%M:%S')}] "
                 f"O:{o:.2f} H:{h:.2f} L:{l:.2f} C:{c:.2f} "
                 f"(updating {bucket.strftime('%H:%M')} candle)")


# ====================== LIGHTSTREAMER LISTENER ======================
class ChartListener(SubscriptionListener):
    """Production-grade listener for IG CHART subscriptions."""

    def onListenStart(self):
        logger.info("[LS] Listener attached to subscription")

    def onListenEnd(self):
        logger.info("[LS] Listener removed from subscription")

    def onSubscription(self):
        logger.info("[LS] Subscription successful — receiving updates")

    def onUnsubscription(self):
        logger.info("[LS] Unsubscribed")

    def onSubscriptionError(self, code, message):
        logger.error(f"[LS] Subscription error: code={code}, message={message}")

    def onItemUpdate(self, update):
        try:
            values = update.getFields()
            if values:
                process_1min_candle(values)
        except Exception as e:
            logger.exception(f"[LS] Error processing update: {e}")

    def onEndOfSnapshot(self, itemName, itemPos):
        logger.debug(f"[LS] Snapshot complete for {itemName}")


# ====================== MAIN ======================
def main():
    logger.info("=== Starting IG Dow 3-Minute Candle Streamer ===")

    if not all([USERNAME, PASSWORD, API_KEY]):
        logger.error("Missing IG credentials in .env file")
        return

    ig_service = IGService(USERNAME, PASSWORD, API_KEY, ACC_TYPE)
    ig_service.create_session()

    ig_stream = IGStreamService(ig_service)
    ig_stream.create_session()

    item = f"CHART:{EPIC}:{RESOLUTION}"
    fields = ["OFR_OPEN", "OFR_HIGH", "OFR_LOW", "OFR_CLOSE",
              "BID_OPEN", "BID_HIGH", "BID_LOW", "BID_CLOSE"]

    listener = ChartListener()
    sub = LSSubscription("MERGE", [item], fields)
    sub.addListener(listener)

    ig_stream.ls_client.subscribe(sub)
    logger.info(f"Subscribed to {item}")
    logger.info(f"Building {TARGET_MINUTES}-minute candles → logs/ directory")
    logger.info("Press Ctrl+C to stop cleanly.\n")

    # Graceful shutdown
    def shutdown(signum, frame):
        logger.info("Shutdown signal received...")
        try:
            ig_stream.disconnect()
        except Exception:
            pass
        logger.info("Disconnected. Exiting.")
        os._exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        shutdown(None, None)


if __name__ == "__main__":
    main()
