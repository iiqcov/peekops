"""
Localized answer and alert fields (#3).
With AIOPS_LANG=ko no English placeholder may reach the Discord alert.
"""

import pytest

import config
from analyzers.react import ReActEngine
from core.models import DiagnosticResult, ReActTurn
from main import DiagnosticRunner
from notifiers import discord
from notifiers.discord import AlertNotifier, ReportNotifier

ENGLISH_PLACEHOLDERS = ["refer to", "none", "unknown", "healthy", "ran:", "[turn", " runs", " times"]


@pytest.fixture
def ko(monkeypatch):
    monkeypatch.setattr(config, "LANGUAGE", "ko")


def _assert_korean(text):
    lowered = text.lower()
    found = [w for w in ENGLISH_PLACEHOLDERS if w in lowered]
    assert not found, f"English placeholders {found} in:\n{text}"


def test_react_answer_action_is_localized(ko):
    history = [ReActTurn(turn=1, analysis="스왑 사용 중", command="free -m", result="..."),
               ReActTurn(turn=2, analysis="ollama 메모리 점유")]
    answer = ReActEngine(llm=None)._build_answer(["스왑 과다 사용: 80%"], history)
    _assert_korean(answer)
    assert "즉시조치: 위 조사 과정 참고" in answer


def test_react_answer_without_issues(ko):
    answer = ReActEngine(llm=None)._build_answer([], [ReActTurn(turn=1, analysis="정상")])
    _assert_korean(answer)


def test_fallback_answer_is_localized(ko):
    _assert_korean(ReActEngine(llm=None)._fallback([]).answer)


def test_healthy_answer_is_localized(ko):
    _assert_korean(DiagnosticRunner._healthy_result(None).answer)


def test_english_default_is_unchanged(en):
    history = [ReActTurn(turn=1, analysis="swap in use")]
    answer = ReActEngine(llm=None)._build_answer(["Swap overuse: 80%"], history)
    assert "action: Refer to the investigation steps above" in answer


@pytest.fixture
def sent(monkeypatch):
    payloads = []

    class Resp:
        status_code = 204

    def fake_post(url, json, timeout):
        payloads.append(json)
        return Resp()
    monkeypatch.setattr(discord.requests, "post", fake_post)
    return payloads


def _payload_text(payload):
    embed = payload["embeds"][0]
    return "\n".join([embed["title"]] + [f"{f['name']} {f['value']}" for f in embed["fields"]])


def test_alert_with_missing_fields_is_localized(ko, sent):
    result = DiagnosticResult(answer="심각도: MED", latency_sec=1.0, tokens_out=200, tok_per_sec=10.0)
    AlertNotifier(webhook="https://example.invalid/hook").notify([], result)
    _assert_korean(_payload_text(sent[0]))


def test_report_units_are_localized(ko, sent):
    stats = {"date": "2026-11-03", "total_rows": 144, "issue_count": 3,
             "severity": {"HIGH": 0, "MED": 1, "LOW": 2},
             "cpu_avg": 10, "cpu_max": 50, "cpu_over_count": 0,
             "mem_avg": 30, "mem_max": 60, "swap_avg": 5, "swap_max": 20, "swap_over_count": 0,
             "ai_latency_avg": 12, "ai_latency_max": 30, "react_runs": 3}
    ReportNotifier(webhook="https://example.invalid/hook").notify(stats)
    text = _payload_text(sent[0])
    assert "144회" in text and "3회" in text
    assert " runs" not in text and " times" not in text
