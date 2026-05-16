#!/usr/bin/env python3
"""
report.py
Aggregates yesterday's diagnostics.csv and sends a daily summary to Discord.

Usage     : python3 report.py
Scheduling: systemd timer (daily 09:00)
"""

from __future__ import annotations
import csv
import os
from datetime import datetime, timedelta

import config
from core.i18n import t
from notifiers.discord import ReportNotifier


def load_yesterday(filepath: str) -> list[dict]:
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    rows: list[dict] = []
    if not os.path.exists(filepath):
        print(f"[report] CSV not found: {filepath}")
        return rows
    with open(filepath, newline="") as f:
        for row in csv.DictReader(f):
            if row.get("timestamp", "").startswith(yesterday):
                rows.append(row)
    return rows


def aggregate(rows: list[dict]) -> dict | None:
    if not rows:
        return None

    def sf(val):
        try:
            return float(val)
        except (ValueError, TypeError):
            return None

    cpu_vals  = [v for r in rows if (v := sf(r.get("cpu_pct")))    is not None]
    mem_vals  = [v for r in rows if (v := sf(r.get("mem_pct")))    is not None]
    swap_vals = [v for r in rows if (v := sf(r.get("swap_pct")))   is not None]
    lat_vals  = [v for r in rows if (v := sf(r.get("ai_latency"))) is not None and v > 0]

    sev: dict[str, int] = {"HIGH": 0, "MED": 0, "LOW": 0, "NORMAL": 0, "UNKNOWN": 0}
    for r in rows:
        key = r.get("ai_severity", "UNKNOWN").strip()
        sev[key] = sev.get(key, 0) + 1

    issue_rows = [r for r in rows if sf(r.get("issues_count", 0)) and sf(r.get("issues_count", 0)) > 0]
    react_rows = [r for r in rows if sf(r.get("ai_latency", 0))   and sf(r.get("ai_latency", 0))   > 0]

    avg = lambda vals: round(sum(vals) / len(vals), 1) if vals else 0

    return {
        "date"           : (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"),
        "total_rows"     : len(rows),
        "issue_count"    : len(issue_rows),
        "severity"       : sev,
        "cpu_avg"        : avg(cpu_vals),
        "cpu_max"        : round(max(cpu_vals),  1) if cpu_vals  else 0,
        "cpu_over_count" : sum(1 for v in cpu_vals  if v > 85),
        "mem_avg"        : avg(mem_vals),
        "mem_max"        : round(max(mem_vals),  1) if mem_vals  else 0,
        "swap_avg"       : avg(swap_vals),
        "swap_max"       : round(max(swap_vals), 1) if swap_vals else 0,
        "swap_over_count": sum(1 for v in swap_vals if v > 70),
        "ai_latency_avg" : avg(lat_vals),
        "ai_latency_max" : round(max(lat_vals),  1) if lat_vals  else 0,
        "react_runs"     : len(react_rows),
    }


def main() -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] generating daily report...")
    rows = load_yesterday(config.LOG_FILE)
    if not rows:
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        print(f"[report] {t('report_no_data', yesterday)} → skipping")
        return

    stats = aggregate(rows)
    if not stats:
        print("[report] aggregation failed")
        return

    print(f"[report] {stats['date']} | {stats['total_rows']} runs | {stats['issue_count']} issues")
    ReportNotifier().notify(stats)


if __name__ == "__main__":
    main()
