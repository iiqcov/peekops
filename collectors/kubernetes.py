"""
collectors/kubernetes.py
Kubernetes Pod state collector via kubectl.
"""

from __future__ import annotations
import subprocess
from collectors.base import BaseCollector

# Pod statuses considered healthy
HEALTHY_STATUSES = {"Running", "Completed", "Succeeded"}


class KubernetesCollector(BaseCollector):
    def __init__(self, timeout: int = 10):
        self._timeout = timeout

    def collect(self) -> dict:
        result = subprocess.run(
            ["kubectl", "get", "pods", "-A", "--no-headers"],
            capture_output=True, text=True, timeout=self._timeout,
        )
        if result.returncode != 0:
            return {"error": result.stderr.strip()}

        pods: list[dict] = []
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) < 4:
                continue
            namespace, name, ready, status = parts[0], parts[1], parts[2], parts[3]
            if "/" not in ready:   # skip header / malformed lines
                continue
            pods.append({"namespace": namespace, "name": name, "ready": ready, "status": status})

        not_running = [p for p in pods if p["status"] not in HEALTHY_STATUSES]
        return {"total": len(pods), "not_running": not_running}
