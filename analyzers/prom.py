"""
analyzers/prom.py
Reads container resource history from Prometheus (cAdvisor metrics) to find
which container *changed* the most, not which one is the largest (#17).

    metric breach  →  container with the largest recent growth  →  its logs in that window

The history already lives in the observability backend; peekops only queries it.
"""

from __future__ import annotations
import re
from dataclasses import dataclass

import requests

_WINDOW = re.compile(r"^\d{1,3}[mh]$")
# Kubernetes names (RFC 1123); label values are put back into a query and a command.
_K8S_NAME = re.compile(r"^[a-z0-9]([-a-z0-9.]{0,251}[a-z0-9])?$")
# Containers only (no pause container, no cgroup roll-ups).
# Placeholders are plain tokens, not str.format fields: PromQL itself uses {}.
_SEL = 'container!="",container!="POD",pod!=""'
_BY  = "sum by (namespace, pod, container)"

QUERIES = {
    "memory": {
        "growth" : f"topk(3, {_BY} (delta(container_memory_working_set_bytes{{{_SEL}}}[__WINDOW__])))",
        "current": f"{_BY} (container_memory_working_set_bytes{{{_SEL}__MATCH__}})",
        "scale"  : 1e6,   # bytes → MB
    },
    "cpu": {
        "growth" : (f"topk(3, {_BY} (rate(container_cpu_usage_seconds_total{{{_SEL}}}[5m]))"
                    f" - {_BY} (rate(container_cpu_usage_seconds_total{{{_SEL}}}[5m] offset __WINDOW__)))"),
        "current": f"{_BY} (rate(container_cpu_usage_seconds_total{{{_SEL}__MATCH__}}[5m]))",
        "scale"  : 1.0,   # cores
    },
}


@dataclass(frozen=True)
class Growth:
    metric   : str
    namespace: str
    pod      : str
    container: str
    amount   : float     # MB for memory, cores for cpu
    current  : float
    window   : str

    @property
    def reason(self) -> str:
        before = self.current - self.amount
        if self.metric == "memory":
            return (f"memory +{self.amount:.0f}MB in {self.window} "
                    f"({before:.0f}MB → {self.current:.0f}MB), largest growth (Prometheus)")
        return (f"cpu +{self.amount:.2f} cores vs {self.window} ago "
                f"({before:.2f} → {self.current:.2f}), largest growth (Prometheus)")


class PrometheusClient:
    def __init__(self, url: str, timeout: float = 5.0):
        self._url     = url.rstrip("/")
        self._timeout = timeout

    def query(self, q: str) -> list[dict] | None:
        """Instant query. Returns the result vector, or None on any failure."""
        try:
            resp = requests.get(f"{self._url}/api/v1/query", params={"query": q}, timeout=self._timeout)
            data = resp.json()
        except Exception as e:
            print(f"  [prometheus] query failed: {e}")
            return None
        if data.get("status") != "success":
            print(f"  [prometheus] query error: {data.get('error', data)}")
            return None
        return data.get("data", {}).get("result", [])

    def top_growth(self, metric: str, window: str) -> Growth | None:
        """The container whose `metric` grew the most over `window`, if any."""
        if metric not in QUERIES or not _WINDOW.match(window):
            return None
        q = QUERIES[metric]
        rows = self.query(q["growth"].replace("__WINDOW__", window))
        if not rows:
            return None

        best = max(rows, key=lambda r: float(r["value"][1]))
        labels = best.get("metric", {})
        ns, pod, container = labels.get("namespace", ""), labels.get("pod", ""), labels.get("container", "")
        amount = float(best["value"][1]) / q["scale"]
        if amount <= 0 or not all(_K8S_NAME.match(v or "") for v in (ns, pod, container)):
            return None

        match = f',namespace="{ns}",pod="{pod}",container="{container}"'
        cur_rows = self.query(q["current"].replace("__MATCH__", match)) or []
        current = float(cur_rows[0]["value"][1]) / q["scale"] if cur_rows else amount
        return Growth(metric, ns, pod, container, amount, current, window)
