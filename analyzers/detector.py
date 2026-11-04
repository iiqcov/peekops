"""
analyzers/detector.py
Threshold-based issue detector.
"""

from __future__ import annotations
import config
from core.i18n import t
from core.models import Anchor, ServerSnapshot


def _top_proc(snap: ServerSnapshot, key: str, field: str, unit: str, metric: str) -> Anchor | None:
    procs = [p for p in snap.os.get(key, []) if isinstance(p, dict) and p.get("pid")]
    if not procs or not procs[0].get(field):
        return None
    p = procs[0]
    return Anchor(kind="pid", pid=int(p["pid"]), name=str(p.get("name") or ""),
                  reason=f"top {field} {p.get(field)}{unit}", metric=metric)


def _zombie_parent(snap: ServerSnapshot) -> Anchor | None:
    parents: dict[int, int] = {}
    for z in snap.os.get("zombies", []):
        if z.get("ppid"):
            parents[int(z["ppid"])] = parents.get(int(z["ppid"]), 0) + 1
    if not parents:
        return None
    ppid, count = max(parents.items(), key=lambda kv: kv[1])
    return Anchor(kind="pid", pid=ppid, name="", reason=f"parent of {count} zombie(s)")


class IssueDetector:
    """Checks a ServerSnapshot against configured thresholds."""

    def detect(self, snap: ServerSnapshot) -> list[str]:
        return [issue for issue, _ in self.detect_anchored(snap)]

    def detect_anchored(self, snap: ServerSnapshot) -> list[tuple[str, Anchor | None]]:
        """Issues paired with the process or pod each one points at (#6)."""
        found: list[tuple[str, Anchor | None]] = []
        thr = config.THRESHOLD

        def add(text: str, anchor: Anchor | None = None) -> None:
            found.append((text, anchor))

        def cpu_anchor() -> Anchor | None:
            return _top_proc(snap, "top_cpu_procs", "cpu_percent", "%", "cpu")

        def mem_anchor() -> Anchor | None:
            return _top_proc(snap, "top_mem_procs", "rss_mb", "MB", "memory")

        cpu = snap.cpu
        if cpu.get("percent", 0) > thr["cpu_percent"]:
            add(t("issue_cpu", cpu["percent"]), cpu_anchor())
        if cpu.get("load_ratio", 0) > thr["load_avg_ratio"]:
            add(t("issue_load", cpu.get("load_1m"), cpu.get("load_ratio")), cpu_anchor())

        mem = snap.memory
        if mem.get("percent", 0) > thr["mem_percent"]:
            add(t("issue_mem", mem["percent"], mem.get("available_gb")), mem_anchor())
        if mem.get("swap_percent", 0) > thr["swap_percent"]:
            add(t("issue_swap", mem["swap_percent"]), mem_anchor())

        for disk in snap.disk:
            if disk.get("percent", 0) > thr["disk_percent"]:
                add(t("issue_disk", disk["mount"], disk["percent"], disk.get("free_gb")))

        net = snap.network
        if not net.get("internet_ok", True):
            add(t("issue_net"))
        if net.get("err_in", 0) + net.get("err_out", 0) > thr["net_err_total"]:
            add(t("issue_net_err", net.get("err_in"), net.get("err_out")))

        if snap.os.get("zombie_count", 0) > 0:
            add(t("issue_zombie", snap.os["zombie_count"]), _zombie_parent(snap))

        k8s = snap.k8s
        if "error" not in k8s:
            for pod in k8s.get("not_running", []):
                add(t("issue_pod", pod["namespace"], pod["name"], pod["status"]),
                    Anchor(kind="pod", name=pod["name"], namespace=pod["namespace"],
                           reason=f"status {pod['status']}"))

        return found
