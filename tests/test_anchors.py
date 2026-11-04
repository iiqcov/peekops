"""
Diagnostic anchors (#6): each issue carries the PID or pod it points at,
context is pre-collected once, and issues sharing an anchor share a loop.
"""

import pytest

from analyzers import anchors
from analyzers.detector import IssueDetector
from analyzers.react import CommandRunner, PromptBuilder, ReActEngine
from collectors.system import OsCollector
from core.i18n import t
from core.models import Anchor

OLLAMA = {"pid": 4242, "name": "ollama", "cpu_percent": 97.0, "memory_percent": 70.0, "rss_mb": 6100.0}
IDLE   = {"pid": 1, "name": "systemd", "cpu_percent": 0.0, "memory_percent": 0.1, "rss_mb": 12.0}


def busy_os(**extra):
    base = {"hostname": "t", "os": "Linux", "uptime_hours": 1, "zombie_count": 0, "zombies": [],
            "top_cpu_procs": [OLLAMA, IDLE], "top_mem_procs": [OLLAMA, IDLE]}
    base.update(extra)
    return base


# ── detector ─────────────────────────────────────────────────────────────────

def test_memory_and_swap_point_at_top_rss_process(snapshot, en):
    snap = snapshot(memory={"percent": 92.0, "available_gb": 0.5, "swap_percent": 80.0}, os=busy_os())
    found = dict(IssueDetector().detect_anchored(snap))
    mem = next(i for i in found if i.startswith("Memory pressure"))
    swap = next(i for i in found if i.startswith("Swap overuse"))
    assert found[mem] == found[swap]
    assert found[mem].pid == 4242 and "rss_mb 6100.0MB" in found[mem].reason


def test_cpu_points_at_top_cpu_process(snapshot, en):
    snap = snapshot(cpu={"percent": 96.0, "load_ratio": 0.5}, os=busy_os())
    [(issue, anchor)] = IssueDetector().detect_anchored(snap)
    assert anchor.label == "PID 4242 (ollama)"


def test_no_cpu_anchor_when_usage_unknown(snapshot, en):
    snap = snapshot(cpu={"percent": 96.0, "load_ratio": 0.5}, os=busy_os(top_cpu_procs=[IDLE]))
    [(_, anchor)] = IssueDetector().detect_anchored(snap)
    assert anchor is None


def test_zombie_points_at_parent(snapshot, en):
    zombies = [{"pid": 11, "ppid": 900, "name": "sh"}, {"pid": 12, "ppid": 900, "name": "sh"},
               {"pid": 13, "ppid": 77, "name": "x"}]
    snap = snapshot(os=busy_os(zombie_count=3, zombies=zombies))
    [(_, anchor)] = IssueDetector().detect_anchored(snap)
    assert anchor.pid == 900 and "2 zombie" in anchor.reason


def test_pod_issue_points_at_pod(snapshot, en):
    snap = snapshot(k8s={"total": 1, "not_running": [
        {"namespace": "ai", "name": "ollama-0", "ready": "0/1", "status": "OOMKilled"}]})
    [(_, anchor)] = IssueDetector().detect_anchored(snap)
    assert anchor.key == "pod:ai/ollama-0"


def test_detect_still_returns_plain_strings(snapshot, en):
    snap = snapshot(memory={"percent": 92.0, "available_gb": 0.5, "swap_percent": 0.0}, os=busy_os())
    assert IssueDetector().detect(snap) == ["Memory pressure: 92.0% used (available: 0.5GB)"]


# ── context collection ───────────────────────────────────────────────────────

class Recorder:
    def __init__(self, output="ok"):
        self.cmds, self.output = [], output

    def __call__(self, cmd):
        self.cmds.append(cmd)
        return self.output


@pytest.fixture
def fake_proc(tmp_path):
    d = tmp_path / "4242"
    d.mkdir()
    (d / "status").write_text("Name:\tollama\nUmask:\t0022\nState:\tS (sleeping)\nPPid:\t1\n"
                              "Threads:\t31\nVmRSS:\t6100000 kB\nVmSwap:\t900000 kB\n")
    (d / "cmdline").write_bytes(b"/usr/bin/ollama\x00serve\x00")
    (d / "cgroup").write_text("0::/kubepods.slice/kubepods-burstable.slice/"
                              "kubepods-burstable-pod1b2c3d4e_aaaa_bbbb_cccc_123456789abc.slice/cri-x\n")
    return str(tmp_path)


def test_pid_context(fake_proc):
    run = Recorder("-- No entries --")
    text = anchors.pid_context(4242, run, proc_root=fake_proc)
    assert "VmRSS: 6100000 kB" in text and "Umask" not in text
    assert "cmdline: /usr/bin/ollama serve" in text
    assert "kubernetes pod UID: 1b2c3d4e-aaaa-bbbb-cccc-123456789abc" in text
    assert run.cmds == ["journalctl _PID=4242 -n 10 --no-pager"]


def test_pid_gone(tmp_path):
    assert "no longer exists" in anchors.pid_context(1, Recorder(), proc_root=str(tmp_path))


def test_crashing_pod_reads_previous_logs():
    run = Recorder()
    anchors.pod_context("ollama-0", "ai", "status CrashLoopBackOff", run)
    assert run.cmds == ["kubectl describe pod ollama-0 -n ai | tail -15",
                        "kubectl logs ollama-0 -n ai --previous --tail=15"]


@pytest.mark.parametrize("name, ns", [("x; rm -rf /", "ai"), ("ollama-0", "ai && reboot"), ("$(id)", "ai")])
def test_untrusted_pod_names_never_reach_a_command(name, ns):
    run = Recorder()
    assert "not collected" in anchors.pod_context(name, ns, "status Error", run)
    assert run.cmds == []


def test_context_is_clipped():
    text = anchors.collect(Anchor(kind="pod", name="p", namespace="ai", reason="status Error"),
                           Recorder("x" * 5000))
    assert len(text) <= anchors.CONTEXT_LIMIT


def test_collection_errors_do_not_raise():
    def boom(cmd):
        raise RuntimeError("kubectl missing")
    text = anchors.collect(Anchor(kind="pod", name="p", namespace="ai"), boom)
    assert "failed" in text


# ── ReAct integration ────────────────────────────────────────────────────────

def reply(analysis, command="DONE"):
    return f"{t('react_analysis')} {analysis}\n{t('react_command')} {command}"


class FakeLLM:
    def __init__(self, replies):
        self.replies, self.prompts = list(replies), []

    def generate(self, prompt):
        self.prompts.append(prompt)
        return self.replies.pop(0) if self.replies else "ERROR:empty"


def test_issues_sharing_an_anchor_share_one_loop(snapshot, en, monkeypatch):
    collected = []
    monkeypatch.setattr(anchors, "collect", lambda a, run: collected.append(a.key) or "VmRSS: 6100000 kB")
    monkeypatch.setattr(CommandRunner, "run", staticmethod(lambda cmd, timeout=8: "ok"))
    snap = snapshot(memory={"percent": 92.0, "available_gb": 0.5, "swap_percent": 80.0}, os=busy_os())
    found = IssueDetector().detect_anchored(snap)
    llm = FakeLLM([reply("ollama model resident in RAM")])

    result = ReActEngine(llm=llm).run(snap, [i for i, _ in found], {i: a for i, a in found if a})

    assert len(llm.prompts) == 1                       # one loop for both issues
    assert collected == ["pid:4242"]                   # context collected once
    first = llm.prompts[0]
    assert t("react_anchor") in first and "PID 4242 (ollama)" in first and "VmRSS: 6100000 kB" in first
    assert "cat /proc/4242/status" in first            # concrete hint
    assert len(result.turns) == 1


def test_pod_anchor_replaces_placeholder_hints(snapshot, en):
    anchor = Anchor(kind="pod", name="ollama-0", namespace="ai", reason="status OOMKilled")
    prompt = PromptBuilder.build(snapshot(), ["Pod abnormal: ollama-0 (namespace: ai, status: OOMKilled)"],
                                 [], anchor=anchor, anchor_context="Last State: OOMKilled")
    hints = prompt.split(t("react_hints"))[1].split(t("react_history"))[0]
    assert "<name>" not in hints and "<namespace>" not in hints
    assert "kubectl logs ollama-0 -n ai --previous --tail=50" in prompt
    assert "Last State: OOMKilled" in prompt


def test_different_anchors_get_separate_loops(snapshot, en, monkeypatch):
    monkeypatch.setattr(anchors, "collect", lambda a, run: "ctx")
    snap = snapshot(
        memory={"percent": 92.0, "available_gb": 0.5, "swap_percent": 0.0}, os=busy_os(),
        k8s={"total": 1, "not_running": [{"namespace": "ai", "name": "web-0", "ready": "0/1", "status": "Error"}]})
    found = IssueDetector().detect_anchored(snap)
    llm = FakeLLM([reply("a"), reply("b")])
    result = ReActEngine(llm=llm).run(snap, [i for i, _ in found], {i: a for i, a in found if a})
    assert len(llm.prompts) == 2 and len(result.turns) == 2


# ── collector ────────────────────────────────────────────────────────────────

def test_os_collector_samples_cpu_and_reports_anchors_inputs():
    data = OsCollector().collect()
    assert {"top_cpu_procs", "top_mem_procs", "zombies"} <= data.keys()
    assert all({"pid", "name", "cpu_percent", "rss_mb"} <= p.keys() for p in data["top_mem_procs"])
