"""
collectors/__init__.py
ServerCollector — facade that runs all collectors and returns a ServerSnapshot.
"""

from __future__ import annotations
from datetime import datetime

from collectors.system import CpuCollector, MemoryCollector, DiskCollector, NetworkCollector, OsCollector
from collectors.kubernetes import KubernetesCollector
from core.models import ServerSnapshot


class ServerCollector:
    """Runs all sub-collectors and assembles a ServerSnapshot."""

    def __init__(self):
        self._cpu     = CpuCollector()
        self._memory  = MemoryCollector()
        self._disk    = DiskCollector()
        self._network = NetworkCollector()
        self._os      = OsCollector()
        self._k8s     = KubernetesCollector()

    def collect(self) -> ServerSnapshot:
        return ServerSnapshot(
            timestamp = datetime.now().isoformat(),
            cpu       = self._cpu.safe_collect(),
            memory    = self._memory.safe_collect(),
            disk      = self._disk.safe_collect(),
            network   = self._network.safe_collect(),
            os        = self._os.safe_collect(),
            k8s       = self._k8s.safe_collect(),
        )
