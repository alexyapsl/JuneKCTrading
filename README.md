# JuneKCTrading

Production-grade IG Markets streaming client for building 3-minute OHLC candles on the Dow Jones Index (IX.D.DOW.IFS.IP).

## Features

- Streams real-time 1-minute OHLC data via IG's Lightstreamer API
- Aggregates into **3-minute candles**
- **Keltner Channel calculator** with incremental (real-time) updates
- Live runner that streams candles + computes KC on the fly
- **Experiment tracking** — all runs are automatically grouped by config hash
- Charts and logs are routed to per-experiment folders for clean A/B testing

## Project Structure

```
JuneKCTrading/
├── src/
│   ├── ig_dow_candle_stream.py      # Main production streamer
│   ├── keltner.py                   # Keltner Channel calculator (EMA + Wilder ATR)
│   └── signal_detector.py           # Signal detection logic (entry/stop rules)
├── scripts/
│   └── plot_kc.py                   # Visualization tool (auto-routes to experiment folders)
├── run_kc_live.py                 # Live runner: streams candles + computes KC in real time
├── config.py                      # ExperimentConfig — single source of truth for parameters
├── docs/
│   └── HOW_TO_PUSH_TO_GITHUB.md
├── logs/
│   └── experiments/               # Per-experiment output (see below)
├── results/
│   └── experiments/               # Per-experiment charts (see below)
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
- Compute Keltner Channels using parameters from `config.py`
- Automatically create an experiment folder: `logs/experiments/<config_id>/`
- Write `experiment_config.json`, `kc_stream.log`, and weekly JSONL inside it
- Auto-reconnect if the Lightstreamer connection drops

Each run is tagged with a deterministic `config_id` (short hash of all parameters). Changing any setting (period, multiplier, offsets, etc.) produces a new folder so experiments stay cleanly separated.

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

**Experiment-aware chart routing:**

- If the log file lives under `logs/experiments/<config_id>/` (or contains a `config_id` field), charts are automatically saved to `results/experiments/<config_id>/`.
- Old logs without experiment metadata fall back to the top-level `results/` folder.
- This keeps every experiment's HTML/PNG outputs cleanly isolated.

Install the required packages once:

```bash
pip install pandas plotly kaleido
```

Generated plots are saved to `results/experiments/<config_id>/` when an experiment is detected (gitignored).

> **Important:** The OHLC candles are built using **Offer (Ask)** prices as primary, with **Bid** as fallback when Offer is unavailable.

## Experiment Tracking & Folder Structure

All runs are now automatically grouped by a deterministic `config_id` (8-character hash of the full parameter set).

### How it works

1. `config.py` defines a single `ExperimentConfig` dataclass (period, multiplier, offsets, bar size, version, etc.).
2. On every start, `run_kc_live.py` calls `CONFIG.ensure_dirs()` which creates:
   - `logs/experiments/<config_id>/`
   - `results/experiments/<config_id>/`
3. Inside the experiment folder you will find:
   - `experiment_config.json` — exact parameter snapshot for reproducibility
   - `kc_stream.log` — full console + error output for this run
   - `kc_2026-Wxx.jsonl` — the actual 3-minute bars + KC values + any detected signals
4. When you run `scripts/plot_kc.py` on a log inside an experiment folder, charts are written to the matching `results/experiments/<config_id>/` folder.

This design makes A/B testing trivial: change any parameter in `config.py`, restart the runner, and everything lands in a brand-new folder.

**Fallback behavior:** Logs that pre-date the experiment system (or lack a `config_id`) are routed to the top-level `logs/` and `results/` folders.

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