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
