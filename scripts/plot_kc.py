#!/usr/bin/env python3
"""
KC Log Visualizer

Plots Keltner Channel (upper/mid/lower) + candlesticks from kc_*.jsonl logs.

Usage:
    python scripts/plot_kc.py                    # latest log, interactive HTML
    python scripts/plot_kc.py logs/kc_2026-07-06.jsonl
    python scripts/plot_kc.py --export png       # also save static PNG
"""

import argparse
import json
from datetime import datetime
from pathlib import Path
import sys

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots


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
    args = parser.parse_args()

    log_path = Path(args.logfile) if args.logfile else find_latest_log()
    print(f"Loading {log_path.name} ...")

    df = load_kc_log(log_path)
    print(f"Loaded {len(df)} bars from {df['timestamp'].min()} to {df['timestamp'].max()}")

    title = f"Keltner Channel — {log_path.stem} ({df['kc'].iloc[0]['period']} period × {df['kc'].iloc[0]['multiplier']})"
    fig = create_figure(df, title)

    base = args.output or log_path.stem
    out_dir = Path(__file__).parent.parent / "results"
    out_dir.mkdir(exist_ok=True)

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
