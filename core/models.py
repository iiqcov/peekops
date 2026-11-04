"""
core/models.py
Shared dataclasses used across the codebase.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ServerSnapshot:
    """Full server state at a single point in time."""
    timestamp : str
    cpu       : dict[str, Any]
    memory    : dict[str, Any]
    disk      : list[dict[str, Any]]
    network   : dict[str, Any]
    os        : dict[str, Any]
    k8s       : dict[str, Any]


@dataclass(frozen=True)
class Anchor:
    """
    The concrete thing an issue points at: a process (PID) or a pod.
    Like a trace_id across services, it ties metrics, logs and the
    investigation together on a single host.
    """
    kind     : str            # "pid" | "pod"
    pid      : int | None = None
    name     : str = ""       # process name or pod name
    namespace: str = ""       # pods only
    reason   : str = ""       # why this is the anchor, e.g. "top RSS 6.1GB"

    @property
    def key(self) -> str:
        return f"pid:{self.pid}" if self.kind == "pid" else f"pod:{self.namespace}/{self.name}"

    @property
    def label(self) -> str:
        if self.kind == "pid":
            return f"PID {self.pid} ({self.name})"
        return f"Pod {self.name} (namespace {self.namespace})"


@dataclass
class ReActTurn:
    """Single turn in a ReAct reasoning loop."""
    turn    : int
    analysis: str
    command : str = ""
    result  : str = ""
    issue   : str = ""      # the issue this turn investigated (per-issue loops)


@dataclass
class DiagnosticResult:
    """Output produced after one full diagnostic run."""
    answer      : str
    latency_sec : float
    tokens_out  : int
    tok_per_sec : float
    turns       : list[ReActTurn] = field(default_factory=list)
