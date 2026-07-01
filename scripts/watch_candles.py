#!/usr/bin/env python3
"""
Candle Watcher (Python + rich)
==============================

Live view of the latest 3-minute candles being collected.

Usage:
    pip install rich
    python scripts/watch_candles.py

It will automatically find the newest dow_3min_*.jsonl file
and show the last 15 candles, refreshing every 10 seconds.
"""

import json
import time
from pathlib import Path
from datetime import datetime
from rich.console import Console
from rich.table import Table
from rich.live import Live

LOG_DIR = Path(__file__).parent.parent / "logs"
CONSOLE = Console()


def get_latest_log_file() -> Path | None:
    files = sorted(LOG_DIR.glob("dow_3min_*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def load_candles(filepath: Path, limit: int = 15) -> list[dict]:
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()[-limit:]
        return [json.loads(line) for line in lines if line.strip()]
    except Exception:
        return []


def make_table(candles: list[dict], filename: str) -> Table:
    table = Table(title=f"Latest 3-min Candles  •  {filename}", show_header=True)
    table.add_column("Time (UTC)", style="cyan", no_wrap=True)
    table.add_column("Open", justify="right")
    table.add_column("High", justify="right")
    table.add_column("Low", justify="right")
    table.add_column("Close", justify="right")
    table.add_column("Range", justify="right")

    for c in candles:
        ts = datetime.fromisoformat(c["timestamp_utc"]).strftime("%Y-%m-%d %H:%M")
        o, h, l, cl = c["open"], c["high"], c["low"], c["close"]
        rng = h - l
        table.add_row(ts, f"{o:.2f}", f"{h:.2f}", f"{l:.2f}", f"{cl:.2f}", f"{rng:.2f}")

    return table


def main():
    CONSOLE.print("[bold green]Starting Candle Watcher...[/bold green]")
    CONSOLE.print("Looking for logs in:", LOG_DIR)

    last_file = None
    last_count = 0

    with Live(refresh_per_second=1) as live:
        while True:
            latest = get_latest_log_file()
            if not latest:
                live.update("[yellow]No log files found yet. Waiting...[/yellow]")
                time.sleep(5)
                continue

            candles = load_candles(latest, limit=15)

            if str(latest) != str(last_file) or len(candles) != last_count:
                last_file = latest
                last_count = len(candles)
                table = make_table(candles, latest.name)
                live.update(table)

            time.sleep(10)


if __name__ == "__main__":
    main()
