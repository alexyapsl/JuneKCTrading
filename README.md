# JuneKCTrading

Production-grade IG Markets streaming client for building 3-minute OHLC candles on the Dow Jones Index (IX.D.DOW.IFS.IP).

## Features

- Streams real-time 1-minute OHLC data via IG's Lightstreamer API
- Aggregates into **3-minute candles**
- **Daily rotating JSONL logs** — safe for 24/7 operation
- Proper `SubscriptionListener` implementation (tested and working)
- Graceful shutdown handling
- Structured logging (console + file)

## Project Structure

```
JuneKCTrading/
├── src/
│   └── ig_dow_candle_stream.py      # Main production streamer
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

### 3. Run the streamer

```powershell
cd C:\Users\alexy\.openclaw\workspace\JuneKCTrading
py src\ig_dow_candle_stream.py
```

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

*Last updated: 2026-07-01*