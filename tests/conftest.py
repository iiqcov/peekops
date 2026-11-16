"""
tests/conftest.py
Shared fixtures. Tests never touch a real server, Ollama or Discord.
"""

from __future__ import annotations
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config  # noqa: E402
from core.models import ServerSnapshot  # noqa: E402


@pytest.fixture(params=["en", "ko"])
def lang(request, monkeypatch):
    """Run a test once per supported language."""
    monkeypatch.setattr(config, "LANGUAGE", request.param)
    return request.param


@pytest.fixture
def en(monkeypatch):
    monkeypatch.setattr(config, "LANGUAGE", "en")
    return "en"


def make_snapshot(**overrides) -> ServerSnapshot:
    """A healthy snapshot; override any section to create an issue."""
    base = dict(
        timestamp="2026-11-04T12:00:00",
        cpu={"percent": 10.0, "core_count": 4, "load_1m": 0.5, "load_5m": 0.5,
             "load_15m": 0.5, "load_ratio": 0.12},
        memory={"total_gb": 16.0, "used_gb": 4.0, "available_gb": 12.0, "percent": 25.0,
                "swap_total_gb": 4.0, "swap_used_gb": 0.0, "swap_percent": 0.0},
        disk=[{"mount": "/", "total_gb": 100.0, "used_gb": 40.0, "free_gb": 60.0, "percent": 40.0}],
        network={"err_in": 0, "err_out": 0, "conn_by_state": {}, "internet_ok": True},
        os={"hostname": "test", "os": "Linux", "uptime_hours": 1.0, "zombie_count": 0,
            "top_cpu_procs": []},
        k8s={"total": 3, "not_running": []},
    )
    base.update(overrides)
    return ServerSnapshot(**base)


@pytest.fixture
def snapshot():
    return make_snapshot
