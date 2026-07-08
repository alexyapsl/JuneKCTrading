# JuneKCTrading

Production-grade IG Markets streaming client for building 3-minute OHLC candles on the Dow Jones Index (IX.D.DOW.IFS.IP).

## Features

- Streams real-time 1-minute OHLC data via IG's Lightstreamer API
- Aggregates into **3-minute candles**
- **Daily rotating JSONL logs** — safe for 24/7 operation
- Proper `SubscriptionListener` implementation (tested and working)
- Graceful shutdown handling
- Structured logging (console + file)
- **Keltner Channel calculator** with incremental (real-time) updates
- Live runner that streams candles + computes KC on the fly

## Project Structure

```
JuneKCTrading/
├── src/
│   ├── ig_dow_candle_stream.py      # Main production streamer
│   └── keltner.py                   # Keltner Channel calculator (EMA + Wilder ATR)
├── run_kc_live.py                 # Live runner: streams candles + computes KC in real time
├── docs/
│   └── HOW_TO_PUSH_TO_GITHUB.md
├── logs/                            # Daily JSONL files (gitignored)
├── .env.example
├── .gitignore
└── README.md
```

## Quick Start

### 1. Install dependencies

```bash
pip install trading-ig python-dotenv lightstreamer-client
```

### 2. Configure credentials

Copy the example file and fill in your IG account details:

```bash
cp .env.example .env
```

Edit `.env`:

```env
IG_USERNAME=your_username
IG_PASSWORD=your_password
IG_API_KEY=your_api_key
IG_ACC_TYPE=DEMO          # or LIVE
```

### 3. Run the live KC runner (recommended)

This runs the streamer + Keltner Channel calculator together:

```powershell
cd C:\Users\alexy\.openclaw\workspace\JuneKCTrading
py run_kc_live.py
```

It will:
- Stream 3-minute Dow candles from IG in real time
- Compute Keltner Channels (period=13, multiplier=1.6 by default)
- Log both bar + KC values to `logs/kc_YYYY-MM-DD.jsonl`
- Auto-reconnect if the Lightstreamer connection drops

### 4. Run the streamer only

```powershell
cd C:\Users\alexy\.openclaw\workspace\JuneKCTrading
py src\ig_dow_candle_stream.py
```

### 5. Visualize logs

After running the live KC runner (or streamer), you can visualize the Keltner Channel + candlesticks:

```powershell
cd C:\Users\alexy\.openclaw\workspace\JuneKCTrading
py scripts/plot_kc.py                              # latest kc_*.jsonl, interactive HTML
py scripts/plot_kc.py logs/kc_2026-W28.jsonl
py scripts/plot_kc.py --export png                  # also save static PNG

# Filter to a specific US trading day (ET timezone)
py scripts/plot_kc.py --date 2026-07-08            # US trading session only (09:30–16:00 ET)
py scripts/plot_kc.py --date 2026-07-08 --full-day  # Full US Eastern calendar day (00:00–23:59 ET)
```

The `--date` filter uses **US Eastern Time (ET)**:

- `--date 2026-07-08` → only the **regular trading session** (09:30–16:00 ET)
- `--date 2026-07-08 --full-day` → the **entire calendar day** in New York time (00:00–23:59 ET / EDT)

Both modes correctly convert the requested window from Eastern Time to UTC when filtering the log files. Output filenames are automatically suffixed with the date (e.g. `kc_2026-W28_2026-07-08.html`).

Plots are saved to the `results/` folder.

Install the required packages once:

```bash
pip install pandas plotly kaleido
```

Generated plots are saved to `results/` (gitignored).

> **Important:** The OHLC candles are built using **Offer (Ask)** prices as primary, with **Bid** as fallback when Offer is unavailable.

The script will:
- Connect to IG Streaming API
- Subscribe to `CHART:IX.D.DOW.IFS.IP:1MINUTE`
- Build 3-minute candles in real time
- Append completed candles to `logs/dow_3min_YYYY-MM-DD.jsonl`

## Log Format (JSONL)

Each line is a complete 3-minute candle:

```json
{
  "timestamp_utc": "2026-07-01T17:12:00+00:00",
  "open": 52151.4,
  "high": 52182.3,
  "low": 52151.4,
  "close": 52174.6,
  "resolution": "3min",
  "epic": "IX.D.DOW.IFS.IP"
}
```

Logs rotate daily. Old log files are safe to archive or delete.

## Requirements

- Python 3.9+
- Valid IG Markets account with API access
- `trading-ig` + Lightstreamer client libraries

## Security

- Never commit `.env` or any file containing credentials
- Use fine-grained GitHub Personal Access Tokens when pushing
- The `.gitignore` is configured to exclude secrets and logs

## License

Internal use for JuneKCTrading project.

---

*Last updated: 2026-07-09*