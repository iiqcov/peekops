from analyzers.detector import IssueDetector


def test_healthy_snapshot_has_no_issues(snapshot, en):
    assert IssueDetector().detect(snapshot()) == []


def test_thresholds_trigger_issues(snapshot, en):
    snap = snapshot(
        cpu={"percent": 90.0, "load_1m": 9.0, "load_ratio": 2.5},
        memory={"percent": 90.0, "available_gb": 1.0, "swap_percent": 80.0},
        disk=[{"mount": "/", "percent": 95.0, "free_gb": 2.0}],
        os={"zombie_count": 2},
    )
    issues = IssueDetector().detect(snap)
    text = " | ".join(issues)
    for keyword in ("CPU overload", "High load", "Memory pressure", "Swap overuse", "Disk full", "Zombie"):
        assert keyword in text


def test_abnormal_pod_is_reported(snapshot, en):
    snap = snapshot(k8s={"total": 1, "not_running": [
        {"namespace": "ai", "name": "ollama-0", "ready": "0/1", "status": "CrashLoopBackOff"}]})
    assert IssueDetector().detect(snap) == ["Pod abnormal: ollama-0 (namespace: ai, status: CrashLoopBackOff)"]


def test_kubectl_error_is_not_an_issue(snapshot, en):
    snap = snapshot(k8s={"error": "kubectl not found"})
    assert IssueDetector().detect(snap) == []
