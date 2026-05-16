"""
analyzers/detector.py
Threshold-based issue detector.
"""

from __future__ import annotations
import config
from core.i18n import t
from core.models import ServerSnapshot


class IssueDetector:
    """Checks a ServerSnapshot against configured thresholds."""

    def detect(self, snap: ServerSnapshot) -> list[str]:
        issues: list[str] = []
        thr = config.THRESHOLD

        cpu = snap.cpu
        if cpu.get("percent", 0) > thr["cpu_percent"]:
            issues.append(t("issue_cpu", cpu["percent"]))
        if cpu.get("load_ratio", 0) > thr["load_avg_ratio"]:
            issues.append(t("issue_load", cpu.get("load_1m"), cpu.get("load_ratio")))

        mem = snap.memory
        if mem.get("percent", 0) > thr["mem_percent"]:
            issues.append(t("issue_mem", mem["percent"], mem.get("available_gb")))
        if mem.get("swap_percent", 0) > thr["swap_percent"]:
            issues.append(t("issue_swap", mem["swap_percent"]))

        for disk in snap.disk:
            if disk.get("percent", 0) > thr["disk_percent"]:
                issues.append(t("issue_disk", disk["mount"], disk["percent"], disk.get("free_gb")))

        net = snap.network
        if not net.get("internet_ok", True):
            issues.append(t("issue_net"))
        if net.get("err_in", 0) + net.get("err_out", 0) > thr["net_err_total"]:
            issues.append(t("issue_net_err", net.get("err_in"), net.get("err_out")))

        if snap.os.get("zombie_count", 0) > 0:
            issues.append(t("issue_zombie", snap.os["zombie_count"]))

        k8s = snap.k8s
        if "error" not in k8s:
            for pod in k8s.get("not_running", []):
                issues.append(t("issue_pod", pod["namespace"], pod["name"], pod["status"]))

        return issues
