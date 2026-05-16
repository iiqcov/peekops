"""
config.py
─────────────────────────────────────────────────────────
All user-configurable settings for AIOps Server Monitor.
Copy this file and edit values, or set environment variables.

Environment variables override values defined here.
─────────────────────────────────────────────────────────
"""

import os

# ── Language ──────────────────────────────────────────
# "en" or "ko"
LANGUAGE: str = os.getenv("AIOPS_LANG", "en")

# ── Ollama ────────────────────────────────────────────
OLLAMA_URL: str = os.getenv("OLLAMA_URL",   "http://localhost:11434/api/generate")
OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "qwen3:8b")

# ── Discord ───────────────────────────────────────────
# Alert webhook  — triggered when severity is HIGH / MED / LOW
DISCORD_ALERT_WEBHOOK: str = os.getenv("DISCORD_ALERT_WEBHOOK", "")

# Daily report webhook — triggered every morning
DISCORD_REPORT_WEBHOOK: str = os.getenv("DISCORD_REPORT_WEBHOOK", "")

# ── Paths ─────────────────────────────────────────────
LOG_FILE: str = os.getenv("LOG_FILE", "/home/qcov/diagnostics.csv")
PID_FILE: str = "/tmp/aiops_diagnostics.pid"
PROMETHEUS_MULTIPROC_DIR: str = "/tmp/aiops_prometheus_multiproc"

# ── Prometheus ────────────────────────────────────────
PROMETHEUS_PORT: int = int(os.getenv("PROMETHEUS_PORT", "8000"))

# ── Scheduling ────────────────────────────────────────
# Interval between diagnostic runs (seconds)
INTERVAL_SEC: int = 600      # 10 minutes

# Maximum allowed runtime before force-kill (seconds)
MAX_RUN_SEC: int  = 540      # 9 minutes

# ── Thresholds ────────────────────────────────────────
# Issue is triggered when metric exceeds these values
THRESHOLD: dict = {
    "cpu_percent"   : 85,    # %
    "mem_percent"   : 85,    # %
    "disk_percent"  : 90,    # %
    "swap_percent"  : 70,    # %
    "load_avg_ratio": 2.0,   # load_1m / cpu_cores
    "net_err_total" : 100,   # err_in + err_out
}

# ── ReAct ─────────────────────────────────────────────
# Maximum number of LLM reasoning turns per incident
MAX_REACT_TURNS: int = 4

# LLM call timeout (seconds)
LLM_TIMEOUT: int = 300

# LLM max tokens per response
LLM_MAX_TOKENS: int = 200

# CPU threshold above which LLM is skipped (rule-based fallback)
LLM_SKIP_CPU_THRESHOLD: int = 95
