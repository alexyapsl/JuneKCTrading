r"""
IG Streaming - 1-Minute Chart Subscription → 3-Minute OHLC Builder
================================================================

This script subscribes to IG's streaming CHART endpoint for IX.D.DOW.IFS.IP
and builds 3-minute OHLC candles from the 1-minute data.

How to run (PowerShell):
    cd C:/Users/alexy/.openclaw/workspace
    python ig_1min_chart_stream.py

Requirements:
    pip install trading-ig python-dotenv
"""

from trading_ig import IGService, IGStreamService
import time
from lightstreamer.client import Subscription as LSSubscription, SubscriptionListener
from datetime import datetime, timedelta
from collections import defaultdict
import os
from dotenv import load_dotenv

# Load credentials from .env file in the same directory
load_dotenv()

# ====================== CONFIGURATION ======================
# Credentials are loaded from .env file (recommended)
# Create a file called .env in the same folder with:
#   IG_USERNAME=your_username
#   IG_PASSWORD=your_password
#   IG_API_KEY=your_api_key
#   IG_ACC_TYPE=DEMO          # or LIVE

USERNAME = os.getenv("IG_USERNAME")
PASSWORD = os.getenv("IG_PASSWORD")
API_KEY  = os.getenv("IG_API_KEY")
ACC_TYPE = os.getenv("IG_ACC_TYPE", "DEMO")          # "DEMO" or "LIVE"

EPIC = "IX.D.DOW.IFS.IP"
RESOLUTION = "1MINUTE"          # Native resolution from IG
TARGET_MINUTES = 3              # We want 3-minute candles
# ===========================================================


def get_3min_bucket(dt: datetime) -> datetime:
    """Round timestamp down to the nearest 3-minute bucket."""
    minute = (dt.minute // TARGET_MINUTES) * TARGET_MINUTES
    return dt.replace(minute=minute, second=0, microsecond=0)


# ====================== CANDLE STORAGE ======================
current_candles = {}          # key = bucket_start_time
last_bucket = None


def process_1min_candle(vals: dict):
    """Receive one 1-minute candle and aggregate into 3-minute candles."""
    global last_bucket

    now = datetime.now()
    bucket = get_3min_bucket(now)

    # Use OFR (offer) prices as primary, fall back to BID
    try:
        o = float(vals.get("OFR_OPEN")  or vals.get("BID_OPEN", 0))
        h = float(vals.get("OFR_HIGH")  or vals.get("BID_HIGH", 0))
        l = float(vals.get("OFR_LOW")   or vals.get("BID_LOW", 0))
        c = float(vals.get("OFR_CLOSE") or vals.get("BID_CLOSE", 0))
    except (TypeError, ValueError):
        return

    if bucket != last_bucket:
        # New 3-minute bucket started - print previous complete candle
        if last_bucket and last_bucket in current_candles:
            prev = current_candles[last_bucket]
            print(f"[{last_bucket.strftime('%H:%M')}] "
                  f"O:{prev['open']:.2f}  H:{prev['high']:.2f}  "
                  f"L:{prev['low']:.2f}  C:{prev['close']:.2f}   "
                  f"(3-min candle complete)")
        last_bucket = bucket
        current_candles[bucket] = {"open": o, "high": h, "low": l, "close": c}
    else:
        # Update current 3-min candle
        candle = current_candles.setdefault(bucket, {"open": o, "high": h, "low": l, "close": c})
        candle["high"] = max(candle["high"], h)
        candle["low"]  = min(candle["low"],  l)
        candle["close"] = c

    # Live update (every 1-min tick inside the 3-min window)
    print(f"  [{now.strftime('%H:%M:%S')}] "
          f"O:{o:.2f} H:{h:.2f} L:{l:.2f} C:{c:.2f}   "
          f"(updating {bucket.strftime('%H:%M')} candle)")


# ====================== STREAMING LISTENER ======================
class ChartListener(SubscriptionListener):
    """Proper Lightstreamer SubscriptionListener for CHART data."""

    def onListenStart(self):
        print("[Listener] onListenStart - Listener attached to subscription")

    def onListenEnd(self):
        print("[Listener] onListenEnd - Listener removed")

    def onSubscription(self):
        print("[Listener] onSubscription - Subscribed successfully, waiting for updates...")

    def onUnsubscription(self):
        print("[Listener] onUnsubscription")

    def onSubscriptionError(self, code, message):
        print(f"[Listener] onSubscriptionError: code={code}, message={message}")

    def onItemUpdate(self, update):
        """Handle incoming item update from Lightstreamer."""
        try:
            # The 'update' object is a Java/Haxe wrapper (ItemUpdateBase)
            # Use its public methods instead of treating it as a dict
            item_name = update.getItemName()
            print(f"[Listener] onItemUpdate: item={item_name}")

            # Get all field values (returns a dict-like mapping in most versions)
            values = update.getFields()
            if values:
                print(f"  -> fields: {list(values.keys())[:5]}...")  # first 5 keys
                process_1min_candle(values)
            else:
                print("  -> No fields in update")

        except AttributeError as e:
            print(f"[Listener] onItemUpdate AttributeError: {e}")
            print(f"  -> update type: {type(update)}")
            print(f"  -> dir(update): {[x for x in dir(update) if not x.startswith('_')][:15]}")
        except Exception as e:
            print(f"[Listener] onItemUpdate error: {type(e).__name__}: {e}")

    def onEndOfSnapshot(self, itemName, itemPos):
        print(f"[Listener] onEndOfSnapshot: {itemName}")


# ====================== MAIN ======================
def main():
    print("Connecting to IG Streaming API...")
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

    print(f"\nSubscribed to: {item}")
    print(f"Building {TARGET_MINUTES}-minute candles from 1-minute data...\n")
    print("Press Ctrl+C to stop.\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nDisconnecting from IG Streaming...")
        ig_stream.disconnect()
        print("Done.")


if __name__ == "__main__":
    main()
