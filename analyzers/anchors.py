"""
analyzers/anchors.py
Pre-collects context for an issue's anchor (PID or pod) before the ReAct loop,
so the LLM starts at the culprit instead of spending turns finding it (#6).
"""

from __future__ import annotations
import re
from typing import Callable

from core.models import Anchor

# Kubernetes object names (RFC 1123). Anything else is never put in a command.
_K8S_NAME = re.compile(r"^[a-z0-9]([-a-z0-9.]{0,251}[a-z0-9])?$")
_POD_UID  = re.compile(r"pod([0-9a-f]{8}[-_][0-9a-f]{4}[-_][0-9a-f]{4}[-_][0-9a-f]{4}[-_][0-9a-f]{12})")

STATUS_FIELDS = ("Name", "State", "PPid", "Threads", "VmRSS", "VmSwap")
CRASH_HINTS   = ("CrashLoop", "Error", "OOMKilled", "BackOff")

# The prompt runs with a 2048-token context; keep the pre-collected part small.
PART_LIMIT    = 300
CONTEXT_LIMIT = 700

Runner = Callable[[str], str]


def _read(path: str) -> str | None:
    try:
        with open(path, "rb") as f:
            return f.read(8192).decode("utf-8", "replace")
    except OSError:
        return None


def _clip(text: str, n: int = PART_LIMIT) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[: n - 3] + "..."


def pid_context(pid: int, run: Runner, proc_root: str = "/proc") -> str:
    status = _read(f"{proc_root}/{pid}/status")
    if status is None:
        return f"process {pid} no longer exists"

    lines = [l for l in status.splitlines() if l.split(":", 1)[0] in STATUS_FIELDS]
    parts = ["status: " + " | ".join(" ".join(l.split()) for l in lines)]

    cmdline = (_read(f"{proc_root}/{pid}/cmdline") or "").replace("\x00", " ").strip()
    if cmdline:
        parts.append(f"cmdline: {_clip(cmdline, 160)}")

    cgroup = _read(f"{proc_root}/{pid}/cgroup") or ""
    uid = _POD_UID.search(cgroup)
    if uid:
        parts.append(f"kubernetes pod UID: {uid.group(1).replace('_', '-')}")
    elif cgroup.strip():
        parts.append(f"cgroup: {_clip(cgroup.splitlines()[0], 120)}")

    journal = run(f"journalctl _PID={int(pid)} -n 10 --no-pager")
    parts.append(f"journal:\n{_clip(journal)}")
    return "\n".join(parts)


def pod_context(name: str, namespace: str, reason: str, run: Runner) -> str:
    if not (_K8S_NAME.match(name) and _K8S_NAME.match(namespace)):
        return "invalid pod reference, context not collected"

    parts = [f"describe (tail):\n{_clip(run(f'kubectl describe pod {name} -n {namespace} | tail -15'))}"]
    if any(h in reason for h in CRASH_HINTS):
        logs = run(f"kubectl logs {name} -n {namespace} --previous --tail=15")
        parts.append(f"logs (previous container):\n{_clip(logs)}")
    else:
        logs = run(f"kubectl logs {name} -n {namespace} --tail=15")
        parts.append(f"logs:\n{_clip(logs)}")
    return "\n".join(parts)


def collect(anchor: Anchor, run: Runner) -> str:
    """Return a short text block describing the anchor, never raising."""
    try:
        if anchor.kind == "pid" and anchor.pid:
            body = pid_context(anchor.pid, run)
        elif anchor.kind == "pod":
            body = pod_context(anchor.name, anchor.namespace, anchor.reason, run)
        else:
            return ""
    except Exception as e:  # context is best-effort; never block the diagnosis
        body = f"context collection failed: {e}"
    return _clip(body, CONTEXT_LIMIT)


def hints(anchor: Anchor) -> list[str]:
    """Concrete next commands for the anchor (replace generic <name> hints)."""
    if anchor.kind == "pid" and anchor.pid:
        return [f"cat /proc/{anchor.pid}/status", f"ls /proc/{anchor.pid}/fd | wc -l"]
    if anchor.kind == "pod":
        n, ns = anchor.name, anchor.namespace
        return [f"kubectl describe pod {n} -n {ns}", f"kubectl logs {n} -n {ns} --previous --tail=50",
                f"kubectl get events -n {ns} --field-selector involvedObject.name={n}"]
    return []
