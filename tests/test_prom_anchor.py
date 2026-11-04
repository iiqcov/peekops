"""
Anchor selection by recent growth from Prometheus (#17).

Scenario from the issue: ollama is always the largest process (resident model),
but the crawler is what pushed memory over the threshold.
"""

import pytest

from analyzers import anchors, prom
from analyzers.detector import IssueDetector
from analyzers.prom import QUERIES, PrometheusClient
from core.models import Anchor

OLLAMA = {"pid": 4242, "name": "ollama", "cpu_percent": 3.0, "memory_percent": 70.0, "rss_mb": 6000.0}
CRAWLER = {"pid": 5151, "name": "chrome", "cpu_percent": 90.0, "memory_percent": 30.0, "rss_mb": 3000.0}


def vector(*rows):
    return {"status": "success", "data": {"resultType": "vector", "result": [
        {"metric": labels, "value": [1730700000, str(v)]} for labels, v in rows]}}


CRAWLER_LABELS = {"namespace": "linkat-dev", "pod": "linkat-dev-pipeline-7d9f-abcde", "container": "pipeline"}


class FakeProm:
    """Answers growth/current queries; records every query sent."""

    def __init__(self, growth=None, current=None, fail=False):
        self.growth, self.current, self.fail, self.sent = growth or {}, current or {}, fail, []

    def get(self, url, params, timeout):
        q = params["query"]
        self.sent.append(q)
        if self.fail:
            raise ConnectionError("connection refused")
        metric = "memory" if "memory" in q else "cpu"
        table = self.growth if q.startswith("topk") else self.current
        return Resp(vector(*table.get(metric, [])))


class Resp:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


@pytest.fixture
def fake(monkeypatch):
    def install(**kw):
        f = FakeProm(**kw)
        monkeypatch.setattr(prom.requests, "get", f.get)
        return f
    return install


def memory_issue(snapshot):
    snap = snapshot(memory={"percent": 92.0, "available_gb": 0.5, "swap_percent": 0.0},
                    os={"zombie_count": 0, "zombies": [], "top_cpu_procs": [CRAWLER, OLLAMA],
                        "top_mem_procs": [OLLAMA, CRAWLER]})
    return IssueDetector().detect_anchored(snap)


def refine(found):
    return anchors.refine_with_growth(found, PrometheusClient("http://10.43.187.96"), "30m", 100, 0.2)


# ── the point of #17 ─────────────────────────────────────────────────────────

def test_without_prometheus_the_largest_process_is_blamed(snapshot, en):
    [(_, anchor)] = memory_issue(snapshot)
    assert anchor.name == "ollama"          # largest, but not what changed


def test_growth_moves_the_anchor_to_the_container_that_changed(snapshot, en, fake):
    fake(growth={"memory": [(CRAWLER_LABELS, 2000e6), ({"namespace": "ai", "pod": "ollama-0", "container": "ollama"}, 5e6)]},
         current={"memory": [(CRAWLER_LABELS, 3000e6)]})
    [(_, anchor)] = refine(memory_issue(snapshot))
    assert anchor.kind == "pod"
    assert (anchor.namespace, anchor.name, anchor.container) == ("linkat-dev", "linkat-dev-pipeline-7d9f-abcde", "pipeline")
    assert anchor.since == "30m"
    assert "memory +2000MB in 30m (1000MB → 3000MB)" in anchor.reason


def test_logs_are_limited_to_the_change_window(fake):
    run_cmds = []
    anchor = Anchor(kind="pod", name="linkat-dev-pipeline-7d9f-abcde", namespace="linkat-dev",
                    container="pipeline", since="30m", reason="memory +2000MB")
    anchors.collect(anchor, lambda c: run_cmds.append(c) or "ok")
    assert "kubectl logs linkat-dev-pipeline-7d9f-abcde -n linkat-dev -c pipeline --since=30m --tail=15" in run_cmds
    assert "--since=30m" in anchors.hints(anchor)[0]


# ── keep the old anchor when there is nothing better ─────────────────────────

def test_small_growth_keeps_top_process(snapshot, en, fake):
    fake(growth={"memory": [(CRAWLER_LABELS, 20e6)]})       # +20MB < 100MB
    [(_, anchor)] = refine(memory_issue(snapshot))
    assert anchor.name == "ollama" and anchor.kind == "pid"


def test_prometheus_down_keeps_top_process(snapshot, en, fake):
    fake(fail=True)
    [(_, anchor)] = refine(memory_issue(snapshot))
    assert anchor.name == "ollama"


def test_no_results_keeps_top_process(snapshot, en, fake):
    fake()
    [(_, anchor)] = refine(memory_issue(snapshot))
    assert anchor.name == "ollama"


def test_pod_and_disk_anchors_are_untouched(snapshot, en, fake):
    f = fake(growth={"memory": [(CRAWLER_LABELS, 2000e6)]})
    pod = Anchor(kind="pod", name="web-0", namespace="ai", reason="status Error")
    found = [("Pod abnormal: web-0", pod), ("Disk full: / 95%", None)]
    assert refine(found) == found
    assert f.sent == []                                       # no query needed


def test_one_query_per_metric_for_many_issues(snapshot, en, fake):
    f = fake(growth={"memory": [(CRAWLER_LABELS, 2000e6)]}, current={"memory": [(CRAWLER_LABELS, 3000e6)]})
    snap = snapshot(memory={"percent": 92.0, "available_gb": 0.5, "swap_percent": 90.0},
                    os={"zombie_count": 0, "zombies": [], "top_cpu_procs": [], "top_mem_procs": [OLLAMA]})
    found = refine(IssueDetector().detect_anchored(snap))     # memory + swap
    assert [a.name for _, a in found] == ["linkat-dev-pipeline-7d9f-abcde"] * 2
    assert sum(q.startswith("topk") for q in f.sent) == 1


def test_cpu_growth(snapshot, en, fake):
    fake(growth={"cpu": [(CRAWLER_LABELS, 1.5)]}, current={"cpu": [(CRAWLER_LABELS, 1.8)]})
    snap = snapshot(cpu={"percent": 92.0, "load_ratio": 0.5},
                    os={"zombie_count": 0, "zombies": [], "top_cpu_procs": [OLLAMA], "top_mem_procs": []})
    [(_, anchor)] = refine(IssueDetector().detect_anchored(snap))
    assert anchor.container == "pipeline" and "cpu +1.50 cores" in anchor.reason


@pytest.mark.parametrize("bad", [
    {"namespace": "ai", "pod": 'x"} or vector(1) #', "container": "c"},
    {"namespace": "ai", "pod": "x; rm -rf /", "container": "c"},
])
def test_unsafe_label_values_are_ignored(fake, bad):
    fake(growth={"memory": [(bad, 2000e6)]})
    assert PrometheusClient("http://p").top_growth("memory", "30m") is None


def test_invalid_window_is_ignored(fake):
    f = fake(growth={"memory": [(CRAWLER_LABELS, 2000e6)]})
    assert PrometheusClient("http://p").top_growth("memory", "30m]) or vector(1") is None
    assert f.sent == []


# ── query syntax (guards the str.format bug found while writing this) ────────

def test_queries_are_valid_promql():
    pp = pytest.importorskip("promql_parser")
    for q in QUERIES.values():
        pp.parse(q["growth"].replace("__WINDOW__", "30m"))
        pp.parse(q["current"].replace("__MATCH__", ',namespace="ai",pod="p",container="c"'))


def test_queries_sent_contain_window_and_match(fake):
    f = fake(growth={"memory": [(CRAWLER_LABELS, 2000e6)]}, current={"memory": [(CRAWLER_LABELS, 3000e6)]})
    PrometheusClient("http://p").top_growth("memory", "45m")
    assert "[45m]" in f.sent[0] and "__" not in f.sent[0]
    assert 'pod="linkat-dev-pipeline-7d9f-abcde"' in f.sent[1] and "__" not in f.sent[1]
