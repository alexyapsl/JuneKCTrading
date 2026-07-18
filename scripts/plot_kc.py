#!/usr/bin/env python3
"""
KC Log Visualizer

Plots Keltner Channel (upper/mid/lower) + candlesticks from kc_*.jsonl logs.

When the log file lives under logs/experiments/<config_id>/ (or contains a config_id field),
charts are automatically saved to results/experiments/<config_id>/ instead of the top-level results/ folder.

Usage:
    python scripts/plot_kc.py                              # latest log (top-level or any experiment), interactive HTML
    python scripts/plot_kc.py logs/kc_2026-07-06.jsonl
    python scripts/plot_kc.py --date 2026-07-08           # US trading session only (09:30–16:00 ET)
    python scripts/plot_kc.py --date 2026-07-08 --full-day  # Full US Eastern calendar day (00:00–23:59 ET)
    python scripts/plot_kc.py --export png                # also save static PNG
"""

import argparse
import json
import re
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
    # Search both top-level logs/ and all experiment subfolders
    candidates = []
    candidates.extend(logs_dir.glob("kc_*.jsonl"))
    candidates.extend(logs_dir.glob("experiments/*/*.jsonl"))
    if not candidates:
        print("No kc_*.jsonl files found in logs/ or logs/experiments/")
        sys.exit(1)
    # Sort by mtime so the most recently written file wins
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
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

    # Flatten signal fields for easier access in plotting
    if "signal" in df.columns:
        sig_df = df["signal"].apply(lambda x: x if isinstance(x, dict) else {})
        df["signal_id"] = sig_df.apply(lambda x: x.get("signal_id"))
        df["signal_direction"] = sig_df.apply(lambda x: x.get("direction"))
        df["signal_entry"] = sig_df.apply(lambda x: x.get("entry_price"))
        df["signal_stop"] = sig_df.apply(lambda x: x.get("stop_loss"))
        # NEW: include KC context that was missing from older signal records
        df["signal_kc_upper"] = sig_df.apply(lambda x: x.get("kc_upper"))
        df["signal_kc_lower"] = sig_df.apply(lambda x: x.get("kc_lower"))
    else:
        df["signal_id"] = None
        df["signal_direction"] = None
        df["signal_entry"] = None
        df["signal_stop"] = None
        df["signal_kc_upper"] = None
        df["signal_kc_lower"] = None

    # Flatten KC fields (the original JSONL stores them nested under "kc")
    if "kc" in df.columns:
        kc_df = df["kc"].apply(lambda x: x if isinstance(x, dict) else {})
        df["kc_mid"] = kc_df.apply(lambda x: x.get("mid"))
        df["kc_upper"] = kc_df.apply(lambda x: x.get("upper"))
        df["kc_lower"] = kc_df.apply(lambda x: x.get("lower"))
        df["atr"] = kc_df.apply(lambda x: x.get("atr"))
    else:
        df["kc_mid"] = None
        df["kc_upper"] = None
        df["kc_lower"] = None
        df["atr"] = None

    return df


# === Execution status (Tier 1 + fallback) ===

STATUS_COLORS = {
    "IGNORED_RR": "#9E9E9E",      # grey
    "DUPLICATE": "#9E9E9E",
    "ATTEMPTED": "#FF9800",       # orange
    "REJECTED": "#F44336",        # red
    "ACCEPTED": "#4CAF50",        # green
    "UNKNOWN": "#607D8B",         # blue-grey
}

STATUS_LABELS = {
    "IGNORED_RR": "Ignored (RR < 1.5)",
    "DUPLICATE": "Duplicate",
    "ATTEMPTED": "Order Sent",
    "REJECTED": "Rejected by IG",
    "ACCEPTED": "Entered (live)",
    "UNKNOWN": "Unknown",
}


def build_execution_lookup(df: pd.DataFrame, log_path: Path) -> dict:
    """
    Tier 2: Build a signal_id -> execution info lookup.

    Priority 1: Use the 'execution' column written by the runner (Tier 1).
    Priority 2: Fall back to parsing kc_stream.log for older files.
    """
    lookup = {}

    # === Tier 1: execution field inside the JSONL ===
    if "execution" in df.columns:
        for _, row in df.iterrows():
            sig_id = row.get("signal_id")
            exec_data = row.get("execution")
            if pd.isna(sig_id) or not isinstance(exec_data, dict):
                continue
            lookup[sig_id] = {
                "status": exec_data.get("status", "UNKNOWN"),
                "deal_id": exec_data.get("deal_id"),
                "rr": exec_data.get("rr"),
                "reason": exec_data.get("reason"),
                "deal_reference": exec_data.get("deal_reference"),
                # NEW: market distance and snapshot for rejection analysis
                "entry_distance_points": exec_data.get("entry_distance_points"),
                "market_price_snapshot": exec_data.get("market_price_snapshot"),
                "ig_description": exec_data.get("ig_description") or exec_data.get("reason"),
            }

    if lookup:
        return lookup

    # === Fallback (legacy files without execution field) ===
    return parse_stream_log_fallback(log_path)


def parse_stream_log_fallback(log_path: Path) -> dict:
    """
    Legacy parser for old runs that only have kc_stream.log.
    """
    stream_log = log_path.parent / "kc_stream.log"
    if not stream_log.exists():
        return {}

    lookup = {}
    signal_pattern = re.compile(r"sig_\d{8}_\d{4}_(long|short)")
    deal_pattern = re.compile(r"'dealId':\s*'([^']+)'")
    rr_pattern = re.compile(r"RR=([0-9.]+)")
    reason_pattern = re.compile(r"'reason':\s*'([^']+)'")

    with open(stream_log, encoding="utf-8", errors="ignore") as f:
        for line in f:
            if "[OrderManager]" not in line and "[SIGNAL]" not in line:
                continue

            sig_match = signal_pattern.search(line)
            if not sig_match:
                continue
            sig_id = sig_match.group(0)

            status = "UNKNOWN"
            deal_id = None
            rr = None
            reason = None

            if "rejected: RR=" in line:
                status = "IGNORED_RR"
                m = rr_pattern.search(line)
                if m:
                    rr = float(m.group(1))
            elif "Working order placed for" in line:
                status = "ATTEMPTED"
                m = rr_pattern.search(line)
                if m:
                    rr = float(m.group(1))
            elif "dealStatus" in line or "ATTACHED_ORDER_LEVEL_ERROR" in line:
                if "REJECTED" in line or "ATTACHED_ORDER_LEVEL_ERROR" in line:
                    status = "REJECTED"
                    m = reason_pattern.search(line)
                    if m:
                        reason = m.group(1)
                elif "ACCEPTED" in line or "OPEN" in line:
                    status = "ACCEPTED"
                m = deal_pattern.search(line)
                if m:
                    deal_id = m.group(1)

            lookup[sig_id] = {
                "status": status,
                "deal_id": deal_id,
                "rr": rr,
                "reason": reason,
            }

    return lookup


def get_signal_status(sig_id: str, exec_lookup: dict) -> dict:
    if not sig_id or sig_id not in exec_lookup:
        return {"status": "UNKNOWN", "deal_id": None, "rr": None, "reason": None}
    return exec_lookup[sig_id]


def create_figure(df: pd.DataFrame, title: str, exec_lookup: dict) -> go.Figure:
    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.06,
        row_heights=[0.75, 0.25]
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

    # === Signal Markers (color-coded by execution status) ===
    signals = df[df["signal_id"].notna()].copy()
    if signals.empty:
        # fallback: old behaviour if no signal column
        signals = df

    for _, row in signals.iterrows():
        if pd.isna(row.get("signal_id")):
            continue

        info = get_signal_status(row["signal_id"], exec_lookup)
        color = STATUS_COLORS.get(info["status"], "#607D8B")
        label = STATUS_LABELS.get(info["status"], "Unknown")

        y_pos = row["high"] + 8 if row["signal_direction"] == "SHORT" else row["low"] - 8
        symbol = "triangle-down" if row["signal_direction"] == "SHORT" else "triangle-up"

        hover = (
            f"{row['signal_direction']}<br>"
            f"Entry: {row['signal_entry']}<br>"
            f"Stop: {row['signal_stop']}<br>"
            f"ID: {row['signal_id']}<br>"
            f"Status: {label}"
        )
        if info.get("rr") is not None:
            hover += f"<br>RR: {info['rr']}"
        if info.get("deal_id"):
            hover += f"<br><b>dealId: {info['deal_id']}</b>"
        if info.get("entry_distance_points") is not None:
            hover += f"<br>Market distance: {info['entry_distance_points']} pts"
        if info.get("ig_description"):
            hover += f"<br><b>IG: {info['ig_description']}</b>"
        elif info.get("reason"):
            hover += f"<br>Reason: {info['reason']}"
        # NEW: show planned TP vs entry when available (wrong-side detection)
        if row.get("signal_kc_upper") and row.get("signal_direction") == "LONG":
            hover += f"<br>Planned TP (KC upper): {row['signal_kc_upper']}"
        if row.get("signal_kc_lower") and row.get("signal_direction") == "SHORT":
            hover += f"<br>Planned TP (KC lower): {row['signal_kc_lower']}"

        fig.add_trace(go.Scatter(
            x=[row["timestamp"]],
            y=[y_pos],
            mode="markers",
            marker=dict(symbol=symbol, size=15, color=color, line=dict(width=1, color="#333333")),
            name=label,
            hovertext=hover,
            hoverinfo="text+x+y",
            showlegend=False
        ), row=1, col=1)

    # Legend entries (dummy traces)
    for status, color in STATUS_COLORS.items():
        fig.add_trace(go.Scatter(
            x=[None], y=[None],
            mode="markers",
            marker=dict(symbol="circle", size=10, color=color),
            name=STATUS_LABELS[status],
            hoverinfo="skip"
        ), row=1, col=1)

    fig.update_layout(
        xaxis_rangeslider_visible=False,
        height=900,
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        hovermode="x unified",
        margin=dict(l=60, r=30, t=80, b=40),
        title=title
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

    # Tier 2: Build execution lookup (prefers 'execution' field written by runner)
    exec_lookup = build_execution_lookup(df, log_path)
    if exec_lookup:
        src = "JSONL execution field" if any(isinstance(r.get("execution"), dict) for _, r in df.iterrows() if pd.notna(r.get("signal_id"))) else "kc_stream.log (legacy)"
        print(f"Loaded execution status for {len(exec_lookup)} signals from {src}")
    else:
        print("No execution information found – all signals will be shown as UNKNOWN")

    # NEW: Warn about signals that have no execution record (will appear as UNKNOWN)
    total_sig = int(df["signal_id"].notna().sum())
    if total_sig > 0:
        covered = sum(1 for sid in df[df["signal_id"].notna()]["signal_id"] if sid in exec_lookup)
        unknown_cnt = total_sig - covered
        if unknown_cnt > 0:
            print(f"⚠️  {unknown_cnt} of {total_sig} signals have NO execution record → shown as UNKNOWN (blue-grey triangles)")
            print("    These signals were detected but OrderManager never recorded a status (import failed, disabled, or pre-order code).")

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

    # Safe title (kc column may be nested or missing in filtered data)
    try:
        period = df["kc"].iloc[0]["period"]
        multiplier = df["kc"].iloc[0]["multiplier"]
    except Exception:
        period = 13
        multiplier = 1.6
    title = f"Keltner Channel — {log_path.stem} ({period} period × {multiplier})"
    fig = create_figure(df, title, exec_lookup)

    # Determine experiment-aware output folder
    config_id = detect_config_id(log_path, df)
    out_dir = get_results_dir(config_id)

    if config_id:
        print(f"Experiment detected (config_id={config_id}) -> writing to {out_dir}")
    else:
        print(f"No experiment config detected -> writing to top-level {out_dir}")

    if args.export in ("html", "both"):
        html_path = out_dir / f"{base}.html"
        fig.write_html(html_path, include_plotlyjs="cdn")
        print(f"[OK] Saved interactive HTML -> {html_path}")

    if args.export in ("png", "both"):
        png_path = out_dir / f"{base}.png"
        fig.write_image(png_path, width=1400, height=900, scale=2)
        print(f"[OK] Saved PNG -> {png_path}")

    if args.export == "html":
        fig.show()


if __name__ == "__main__":
    main()
