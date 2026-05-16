"""
exporters/prometheus.py
Prometheus gauge definitions and update logic.
"""

from __future__ import annotations
import os
import config

os.environ['PROMETHEUS_MULTIPROC_DIR'] = config.PROMETHEUS_MULTIPROC_DIR
os.makedirs(config.PROMETHEUS_MULTIPROC_DIR, exist_ok=True)

try:
    from prometheus_client import Gauge, start_http_server as _start

    _cpu      = Gauge('aiops_cpu_percent',    'CPU usage (%)')
    _mem      = Gauge('aiops_mem_percent',    'Memory usage (%)')
    _swap     = Gauge('aiops_swap_percent',   'Swap usage (%)')
    _disk     = Gauge('aiops_disk_percent',   'Root disk usage (%)')
    _load     = Gauge('aiops_load_ratio',     'Load average ratio')
    _zombie   = Gauge('aiops_zombie_count',   'Zombie process count')
    _issues   = Gauge('aiops_issues_count',   'Detected issue count')
    _severity = Gauge('aiops_severity_level', 'AI severity (0=NORMAL 1=LOW 2=MED 3=HIGH)')
    _latency  = Gauge('aiops_ai_latency_sec', 'AI response time (seconds)')
    _turns    = Gauge('aiops_react_turns',    'ReAct loop turn count')

    AVAILABLE = True
except ImportError:
    AVAILABLE = False

SEVERITY_MAP = {"NORMAL": 0, "LOW": 1, "MED": 2, "HIGH": 3}
_started     = False


class PrometheusExporter:
    """Updates Prometheus gauges from diagnostic results."""

    def start(self, port: int = config.PROMETHEUS_PORT) -> None:
        global _started
        if not AVAILABLE or _started:
            return
        try:
            _start(port)
            _started = True
            print(f"  [Prometheus] exporter started → http://localhost:{port}/metrics")
        except OSError:
            _started = True  # already running via aiops_exporter.py

    def update(self, snap, issues: list[str], result) -> None:
        if not AVAILABLE:
            return

        from core.i18n import t
        cpu  = snap.cpu
        mem  = snap.memory
        disk = snap.disk
        os_  = snap.os

        _cpu.set(cpu.get("percent", 0))
        _mem.set(mem.get("percent", 0))
        _swap.set(mem.get("swap_percent", 0))
        _load.set(cpu.get("load_ratio", 0))
        _zombie.set(os_.get("zombie_count", 0))
        _issues.set(len(issues))

        for d in disk:
            if isinstance(d, dict) and d.get("mount") == "/":
                _disk.set(d.get("percent", 0))
                break

        sev_key  = t("ans_severity")
        severity = "NORMAL"
        for line in result.answer.splitlines():
            if line.startswith(f"{sev_key}:"):
                severity = line.split(":", 1)[-1].strip()
                break
        _severity.set(SEVERITY_MAP.get(severity, 0))

        if result.latency_sec and result.latency_sec > 0:
            _latency.set(result.latency_sec)
        _turns.set(len(result.turns))
