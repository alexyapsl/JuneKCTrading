#!/usr/bin/env python3
"""
KC Log Visualizer

Plots Keltner Channel (upper/mid/lower) + candlesticks from kc_*.jsonl logs.

When the log file lives under logs/experiments/<config_id>/ (or contains a config_id field),
charts are automatically saved to results/experiments/<config_id>/ instead of the top-level results/ folder.

Usage:
    python scripts/plot_kc.py                              # latest log, interactive HTML
    python scripts/plot_kc.py logs/kc_2026-07-06.jsonl
    python scripts/plot_kc.py --date 2026-07-08           # US trading session only (09:30–16:00 ET)
    python scripts/plot_kc.py --date 2026-07-08 --full-day  # Full US Eastern calendar day (00:00–23:59 ET)
    python scripts/plot_kc.py --export png                # also save static PNG
"""

import argparse
import json
from datetime import datetime, time
from pathlib import Path
import sys

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from pytz import timezone as ZoneInfo  # fallback for older Python


# === Experiment-aware output helpers ===
def detect_config_id(log_path: Path, df: pd.DataFrame | None = None) -> str | None:
    """
    Try to determine the experiment config_id from:
    1. The parent directory name if we're inside logs/experiments/<config_id>/
    2. The first record's 'config_id' field in the loaded dataframe
    Returns None if it can't be reliably detected.
    """
    # 1. Check if log lives under logs/experiments/<something>/
    parent = log_path.parent
    if parent.parent.name == "experiments":
        cid = parent.name
        if cid and cid.lower() not in ("nan", "none", "null"):
            return cid

    # 2. Fall back to the config_id stored inside the data
    if df is not None and not df.empty and "config_id" in df.columns:
        cid = df["config_id"].iloc[0]
        # Guard against pandas NaN, None, empty, or the literal string "nan"
        if pd.notna(cid):
            cid_str = str(cid).strip()
            if cid_str and cid_str.lower() not in ("nan", "none", "null"):
                return cid_str
    return None


def get_results_dir(config_id: str | None) -> Path:
    """
    Return the appropriate results directory.
    If we have a config_id, use results/experiments/<config_id>/
    Otherwise fall back to the top-level results/ folder.
    """
    base = Path(__file__).parent.parent / "results"
    if config_id:
        target = base / "experiments" / config_id
        target.mkdir(parents=True, exist_ok=True)
        return target
    base.mkdir(exist_ok=True)
    return base


def find_latest_log() -> Path:
    logs_dir = Path(__file__).parent.parent / "logs"
    candidates = sorted(logs_dir.glob("kc_*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        print("No kc_*.jsonl files found in logs/")
        sys.exit(1)
    return candidates[0]


def load_kc_log(path: Path) -> pd.DataFrame:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if not rows:
        print(f"No valid JSON lines in {path}")
        sys.exit(1)
    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp_utc"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    # Flatten KC fields for easy access
    df["kc_mid"] = df["kc"].apply(lambda x: x["mid"])
    df["kc_upper"] = df["kc"].apply(lambda x: x["upper"])
    df["kc_lower"] = df["kc"].apply(lambda x: x["lower"])
    df["atr"] = df["kc"].apply(lambda x: x.get("atr"))
    # Keep experiment metadata if present (so detect_config_id can use it)
    # Do NOT blindly astype(str) — that turns NaN into the literal string "nan"
    # which then gets treated as a valid config_id and creates a folder named nan.
    return df


def create_figure(df: pd.DataFrame, title: str) -> go.Figure:
    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.02,
        row_heights=[0.75, 0.25],
        subplot_titles=(title, "ATR")
    )

    # Candlestick
    fig.add_trace(go.Candlestick(
        x=df["timestamp"],
        open=df["open"],
        high=df["high"],
        low=df["low"],
        close=df["close"],
        name="Price",
        increasing_line_color="#26a69a",
        decreasing_line_color="#ef5350"
    ), row=1, col=1)

    # Keltner Channel bands
    fig.add_trace(go.Scatter(
        x=df["timestamp"], y=df["kc_upper"],
        line=dict(color="#2196F3", width=1.5),
        name="KC Upper"
    ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=df["timestamp"], y=df["kc_mid"],
        line=dict(color="#FF9800", width=2, dash="dot"),
        name="KC Mid (EMA)"
    ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=df["timestamp"], y=df["kc_lower"],
        line=dict(color="#2196F3", width=1.5),
        name="KC Lower",
        fill="tonexty",
        fillcolor="rgba(33,150,243,0.1)"
    ), row=1, col=1)

    # ATR subplot
    if df["atr"].notna().any():
        fig.add_trace(go.Scatter(
            x=df["timestamp"], y=df["atr"],
            line=dict(color="#9C27B0", width=1.5),
            name="ATR"
        ), row=2, col=1)

    fig.update_layout(
        xaxis_rangeslider_visible=False,
        height=800,
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        hovermode="x unified",
        margin=dict(l=60, r=30, t=60, b=40)
    )

    fig.update_yaxes(title_text="Price", row=1, col=1)
    fig.update_yaxes(title_text="ATR", row=2, col=1)

    return fig


def main():
    parser = argparse.ArgumentParser(description="Visualize Keltner Channel logs")
    parser.add_argument("logfile", nargs="?", help="Path to kc_*.jsonl (default: latest)")
    parser.add_argument("--export", choices=["html", "png", "both"], default="html",
                        help="Export format (default: html)")
    parser.add_argument("--output", help="Output filename (without extension)")
    parser.add_argument("--date", help="US trading day in YYYY-MM-DD (ET timezone, e.g. 2026-07-08)")
    parser.add_argument("--full-day", action="store_true",
                        help="Include full US Eastern calendar day (00:00–23:59 ET) instead of only trading hours (09:30–16:00 ET)")
    args = parser.parse_args()

    log_path = Path(args.logfile) if args.logfile else find_latest_log()
    print(f"Loading {log_path.name} ...")

    df = load_kc_log(log_path)

    # Filter by US date if --date is provided
    if args.date:
        try:
            target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            print(f"Invalid date format: {args.date}. Use YYYY-MM-DD.")
            sys.exit(1)

        # US Eastern timezone (handle both zoneinfo and pytz correctly)
        try:
            et = ZoneInfo("America/New_York")
            is_pytz = False
        except Exception:
            et_tz = ZoneInfo("US/Eastern")
            is_pytz = True

        def localize(dt):
            """Attach ET timezone correctly for both zoneinfo and pytz."""
            if is_pytz:
                return et_tz.localize(dt)
            return dt.replace(tzinfo=et)

        if args.full_day:
            # Full US Eastern calendar day: 00:00 ET → 23:59:59.999 ET
            start_naive = datetime.combine(target_date, time(0, 0))
            end_naive = datetime.combine(target_date, time(23, 59, 59, 999000))
            start_et = localize(start_naive)
            end_et = localize(end_naive)
            session_label = "full US ET day"
        else:
            # US trading session only: 09:30 ET → 16:00 ET
            start_naive = datetime.combine(target_date, time(9, 30))
            end_naive = datetime.combine(target_date, time(16, 0))
            start_et = localize(start_naive)
            end_et = localize(end_naive)
            session_label = "US ET trading session"

        # Convert to UTC for filtering (timestamps in log are UTC)
        start_utc = start_et.astimezone(ZoneInfo("UTC"))
        end_utc = end_et.astimezone(ZoneInfo("UTC"))

        mask = (df["timestamp"] >= pd.Timestamp(start_utc)) & (df["timestamp"] <= pd.Timestamp(end_utc))
        df = df.loc[mask].reset_index(drop=True)

        if df.empty:
            print(f"No bars found for {args.date} ({session_label}).")
            sys.exit(1)

        print(f"Filtered to {args.date} ({session_label}): {len(df)} bars from {df['timestamp'].min()} to {df['timestamp'].max()}")
        base = f"{log_path.stem}_{args.date}"
    else:
        print(f"Loaded {len(df)} bars from {df['timestamp'].min()} to {df['timestamp'].max()}")
        base = args.output or log_path.stem

    title = f"Keltner Channel — {log_path.stem} ({df['kc'].iloc[0]['period']} period × {df['kc'].iloc[0]['multiplier']})"
    fig = create_figure(df, title)

    # Determine experiment-aware output folder
    config_id = detect_config_id(log_path, df)
    out_dir = get_results_dir(config_id)

    if config_id:
        print(f"Experiment detected (config_id={config_id}) → writing to {out_dir}")
    else:
        print(f"No experiment config detected → writing to top-level {out_dir}")

    if args.export in ("html", "both"):
        html_path = out_dir / f"{base}.html"
        fig.write_html(html_path, include_plotlyjs="cdn")
        print(f"✓ Saved interactive HTML → {html_path}")

    if args.export in ("png", "both"):
        png_path = out_dir / f"{base}.png"
        fig.write_image(png_path, width=1400, height=900, scale=2)
        print(f"✓ Saved PNG → {png_path}")

    if args.export == "html":
        fig.show()


if __name__ == "__main__":
    main()
