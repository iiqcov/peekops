#!/usr/bin/env python3
"""
main.py
AIOps diagnostic runner.

Usage:
  python3 main.py --once              # single run
  python3 main.py --interval 600      # loop every 600 seconds
"""

from __future__ import annotations
import argparse
import csv
import os
import time
from datetime import datetime

import config
from collectors import ServerCollector
from analyzers.detector import IssueDetector
from analyzers.react import ReActEngine
from notifiers.discord import AlertNotifier
from exporters.prometheus import PrometheusExporter
from core.lock import ProcessLock
from core.models import DiagnosticResult, ServerSnapshot
from core.i18n import t
from analyzers import anchors as anchor_ctx
from analyzers.prom import PrometheusClient


class DiagnosticRunner:
    """Orchestrates one complete diagnostic cycle."""

    def __init__(self):
        self._collector  = ServerCollector()
        self._detector   = IssueDetector()
        self._react      = ReActEngine()
        self._alert      = AlertNotifier()
        self._prometheus = PrometheusExporter()
        self._lock       = ProcessLock()

    def run_once(self) -> None:
        if not self._lock.acquire():
            print(f"[{datetime.now().strftime('%H:%M:%S')}] already running → skip")
            return
        try:
            self._execute()
        finally:
            self._lock.release()

    def _execute(self) -> None:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] starting diagnostics...")
        self._prometheus.start()

        snap     = self._collector.collect()
        anchored = self._detector.detect_anchored(snap)
        if config.PROMETHEUS_QUERY_URL:
            anchored = anchor_ctx.refine_with_growth(
                anchored, PrometheusClient(config.PROMETHEUS_QUERY_URL), config.ANCHOR_CHANGE_WINDOW,
                config.ANCHOR_MIN_MEM_GROWTH_MB, config.ANCHOR_MIN_CPU_GROWTH_CORES)
        issues   = [issue for issue, _ in anchored]
        anchors  = {issue: anchor for issue, anchor in anchored if anchor}

        if issues:
            print(f"  ⚠  {len(issues)} issue(s) → ReAct loop starting...")
            result = self._react.run(snap, issues, anchors)
        else:
            result = self._healthy_result()

        self._print_report(snap, issues, result)
        self._save_csv(snap, issues, result)
        self._alert.notify(issues, result)
        self._prometheus.update(snap, issues, result)

    # ── helpers ──────────────────────────────────────────────────────────────

    def _healthy_result(self) -> DiagnosticResult:
        sk, pk, ck, stk, ak, mk = (
            t("ans_severity"), t("ans_problem"), t("ans_cause"),
            t("ans_steps"), t("ans_action"), t("ans_monitor"),
        )
        none = t("ans_none")
        return DiagnosticResult(
            answer=(f"{sk}: NORMAL\n{pk}: {none}\n{ck}: {t('ans_healthy')}\n"
                    f"{stk}: {none}\n{ak}: {none}\n{mk}: {none}"),
            latency_sec=0, tokens_out=0, tok_per_sec=0,
        )

    def _print_report(self, snap: ServerSnapshot, issues: list[str], result: DiagnosticResult) -> None:
        now  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cpu  = snap.cpu
        mem  = snap.memory
        net  = snap.network
        os_  = snap.os
        thr  = config.THRESHOLD

        print(f"\n{'='*60}\n  AIOps Diagnostics [{now}]\n{'='*60}")
        print(f"\n[CPU]   {cpu.get('percent')}%{'  ⚠' if cpu.get('percent',0) > thr['cpu_percent'] else ''}"
              f"  load {cpu.get('load_1m')}/{cpu.get('load_5m')}/{cpu.get('load_15m')} (ratio {cpu.get('load_ratio')})"
              f"  cores {cpu.get('core_count')}")
        print(f"[MEM]   {mem.get('percent')}%{'  ⚠' if mem.get('percent',0) > thr['mem_percent'] else ''}"
              f"  {mem.get('used_gb')}GB/{mem.get('total_gb')}GB  avail {mem.get('available_gb')}GB"
              f"  swap {mem.get('swap_percent')}%{'  ⚠' if mem.get('swap_percent',0) > thr['swap_percent'] else ''}")
        print("[DISK]")
        for d in snap.disk:
            if "error" in d:
                continue
            flag = "  ⚠" if d.get("percent", 0) > thr["disk_percent"] else ""
            print(f"  {d['mount']:20s} {d['percent']}%  (used {d['used_gb']}GB / {d['total_gb']}GB  free {d['free_gb']}GB){flag}")
        conn = net.get("conn_by_state", {})
        print(f"[NET]   {'✓' if net.get('internet_ok') else '✗ ⚠'}  "
              f"err in={net.get('err_in')} out={net.get('err_out')}  "
              f"ESTAB={conn.get('ESTABLISHED',0)} TW={conn.get('TIME_WAIT',0)}")
        print(f"[OS]    {os_.get('hostname')} | {os_.get('os')} | up {os_.get('uptime_hours')}h | "
              f"zombies {os_.get('zombie_count')}{'  ⚠' if os_.get('zombie_count',0) > 0 else ''}")

        k8s = snap.k8s
        print("[K8S]   ", end="")
        if "error" in k8s:
            print(f"error: {k8s['error']}")
        else:
            print(f"{k8s.get('total',0)} pods")
            for pod in k8s.get("not_running", []):
                print(f"  ⚠  {pod['namespace']}/{pod['name']} ({pod['status']})")
            if not k8s.get("not_running"):
                print(t("all_pods_healthy"))

        print("\n[ISSUES]")
        for iss in issues:
            print(f"  ⚠  {iss}")
        if not issues:
            print(f"  {t('no_issues')}")

        lat = result.latency_sec
        lat_str = f"{lat}s" if lat and lat >= 0 else "rule-based"
        print(f"\n[AI]  {lat_str} / {result.tok_per_sec} tok/s")
        print("-" * 60)
        print(result.answer)
        print("=" * 60 + "\n")

    def _save_csv(self, snap: ServerSnapshot, issues: list[str], result: DiagnosticResult) -> None:
        file_exists = os.path.isfile(config.LOG_FILE)
        with open(config.LOG_FILE, "a", newline="") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow([
                    "timestamp", "cpu_pct", "load_ratio",
                    "mem_pct", "swap_pct",
                    "internet_ok", "net_err_total",
                    "zombie_count", "issues_count",
                    "ai_severity", "ai_latency", "ai_tok_per_s",
                ])
            sev_key  = t("ans_severity")
            severity = "UNKNOWN"
            for line in result.answer.splitlines():
                if line.startswith(f"{sev_key}:"):
                    severity = line.split(":", 1)[-1].strip()
                    break
            writer.writerow([
                snap.timestamp,
                snap.cpu.get("percent"), snap.cpu.get("load_ratio"),
                snap.memory.get("percent"), snap.memory.get("swap_percent"),
                snap.network.get("internet_ok"),
                snap.network.get("err_in", 0) + snap.network.get("err_out", 0),
                snap.os.get("zombie_count"), len(issues),
                severity, result.latency_sec, result.tok_per_sec,
            ])


def main() -> None:
    parser = argparse.ArgumentParser(description="AIOps Server Diagnostics")
    parser.add_argument("--once",     action="store_true", help="run once and exit")
    parser.add_argument("--interval", type=int, default=config.INTERVAL_SEC,
                        help=f"loop interval in seconds (default: {config.INTERVAL_SEC})")
    args = parser.parse_args()

    runner = DiagnosticRunner()

    if args.once:
        runner.run_once()
        return

    print(f"[{datetime.now().strftime('%H:%M:%S')}] monitoring started "
          f"(interval: {args.interval}s) | Ctrl+C to stop")
    while True:
        try:
            runner.run_once()
            print(f"  next run in {args.interval}s\n")
            time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\n[stopped]")
            break
        except Exception as e:
            print(f"[error] {e} — retrying in 30s...")
            time.sleep(30)


if __name__ == "__main__":
    main()
