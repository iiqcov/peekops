"""
LLM response cleanup (#1): reasoning blocks must not reach the ReAct parser.
"""

import pytest
import requests

from analyzers import llm as llm_mod
from analyzers.llm import LLMClient, NO_THINK_SWITCH, strip_thinking
from analyzers.react import ReActEngine

ANSWER = "analysis: swap used by ollama\ncommand: DONE"


@pytest.mark.parametrize("raw", [
    ANSWER,
    f"<think>\nlet me check memory first...\n</think>\n\n{ANSWER}",
    f"<THINK>upper case</THINK>{ANSWER}",
    f"<think>a</think><think>b</think>{ANSWER}",
    f"</think>\n{ANSWER}",  # opening tag consumed by the template
])
def test_answer_survives_cleanup(raw):
    assert strip_thinking(raw) == ANSWER


def test_unclosed_think_is_dropped():
    assert strip_thinking("<think>I should run ps aux and then") == ""


class FakeResponse:
    def __init__(self, text):
        self._text = text

    def json(self):
        return {"response": self._text}


@pytest.fixture
def post(monkeypatch):
    calls = []

    def install(text):
        def fake_post(url, json, timeout):
            calls.append(json)
            return FakeResponse(text)
        monkeypatch.setattr(llm_mod.requests, "post", fake_post)
        return calls
    return install


def test_generate_strips_think_block(post):
    post(f"<think>reasoning</think>{ANSWER}")
    assert LLMClient(model="qwen3:4b").generate("p") == ANSWER


def test_truncated_thinking_is_reported_as_error(post):
    post("<think>still thinking when num_predict ran out")
    assert LLMClient(model="qwen3:4b").generate("p").startswith("ERROR:")


def test_qwen3_prompt_gets_no_think_switch(post):
    calls = post(ANSWER)
    LLMClient(model="qwen3:8b").generate("prompt")
    assert calls[0]["prompt"].endswith(NO_THINK_SWITCH)
    assert calls[0]["think"] is False


def test_other_models_prompt_unchanged(post):
    calls = post(ANSWER)
    LLMClient(model="llama3.1:8b").generate("prompt")
    assert calls[0]["prompt"] == "prompt"


def test_timeout_is_error(monkeypatch):
    def boom(*a, **k):
        raise requests.exceptions.Timeout()
    monkeypatch.setattr(llm_mod.requests, "post", boom)
    assert LLMClient().generate("p") == "ERROR:timeout"


def test_react_parses_answer_after_think(post, snapshot, en):
    post(f"<think>\nhmm\n</think>\n{ANSWER}")
    result = ReActEngine(llm=LLMClient(model="qwen3:4b")).run(snapshot(), ["Swap overuse: 80%"])
    assert result.turns[0].analysis == "swap used by ollama"
    assert result.latency_sec >= 0  # LLM path, not rule-based fallback
