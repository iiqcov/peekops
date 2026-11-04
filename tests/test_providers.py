"""
LLM providers (#5): Ollama stays the default; others are opt-in and
non-local endpoints need LLM_ALLOW_EXTERNAL=true.
"""

import pytest

import config
from analyzers import llm as llm_mod
from analyzers.llm import (DisabledClient, ExternalLLMNotAllowed, GeminiClient, OllamaClient,
                           OpenAICompatibleClient, create_client, create_client_or_disabled, is_local)
from analyzers.react import ReActEngine

ANSWER = "analysis: ollama holds swap\ncommand: DONE"


class Resp:
    def __init__(self, data, status=200):
        self._data, self.status_code = data, status

    def json(self):
        return self._data


@pytest.fixture
def post(monkeypatch):
    calls = []

    def install(data, status=200):
        def fake_post(url, json, timeout, headers=None):
            calls.append({"url": url, "json": json, "headers": headers or {}})
            return Resp(data, status)
        monkeypatch.setattr(llm_mod.requests, "post", fake_post)
        return calls
    return install


# ── factory / privacy guard ──────────────────────────────────────────────────

def test_default_is_local_ollama(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")
    assert isinstance(create_client(), OllamaClient)


@pytest.mark.parametrize("url, local", [
    ("http://localhost:11434/api/generate", True),
    ("http://127.0.0.1:8080/v1", True),
    ("http://192.168.0.10:11434/api/generate", True),
    ("http://ollama.ai.svc.cluster.local:11434/api/generate", True),
    ("https://generativelanguage.googleapis.com/v1beta/models/x", False),
    ("https://api.openai.com/v1", False),
])
def test_is_local(url, local):
    assert is_local(url) is local


def test_external_provider_is_refused_without_opt_in(monkeypatch):
    monkeypatch.setattr(config, "LLM_ALLOW_EXTERNAL", False)
    with pytest.raises(ExternalLLMNotAllowed):
        create_client("gemini")


def test_external_provider_allowed_with_opt_in(monkeypatch):
    monkeypatch.setattr(config, "LLM_ALLOW_EXTERNAL", True)
    assert isinstance(create_client("gemini"), GeminiClient)


def test_local_openai_compatible_needs_no_opt_in(monkeypatch):
    monkeypatch.setattr(config, "LLM_ALLOW_EXTERNAL", False)
    assert isinstance(create_client("openai"), OpenAICompatibleClient)  # default localhost:8080


def test_misconfiguration_falls_back_to_rule_based(snapshot, en, monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "nope")
    client = create_client_or_disabled()
    assert isinstance(client, DisabledClient)
    result = ReActEngine(llm=client).run(snapshot(), ["Swap overuse: 80%"])
    assert result.latency_sec == -1   # rule-based, no crash


# ── request / response shapes ────────────────────────────────────────────────

def test_openai_compatible_request(post):
    calls = post({"choices": [{"message": {"content": f"<think>x</think>{ANSWER}"}}]})
    client = OpenAICompatibleClient(base_url="http://localhost:8080/v1/", model="qwen3-8b", api_key="k")
    assert client.generate("prompt") == ANSWER
    call = calls[0]
    assert call["url"] == "http://localhost:8080/v1/chat/completions"
    assert call["headers"] == {"Authorization": "Bearer k"}
    assert call["json"]["messages"] == [{"role": "user", "content": "prompt"}]
    assert call["json"]["max_tokens"] == config.LLM_MAX_TOKENS


def test_gemini_request(post):
    calls = post({"candidates": [{"content": {"parts": [
        {"text": "internal reasoning", "thought": True}, {"text": ANSWER}]}}]})
    client = GeminiClient(model="gemini-2.5-flash", api_key="secret")
    assert client.generate("prompt") == ANSWER
    call = calls[0]
    assert call["url"].endswith("/models/gemini-2.5-flash:generateContent")
    assert "secret" not in call["url"]                       # key in header, not URL
    assert call["headers"] == {"x-goog-api-key": "secret"}
    assert call["json"]["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}
    assert call["json"]["contents"][0]["parts"][0]["text"] == "prompt"


def test_gemini_without_key_is_error(post):
    post({})
    assert GeminiClient(api_key="").generate("p").startswith("ERROR:")


def test_http_error_is_reported(post):
    post({"error": {"message": "API key not valid"}}, status=400)
    out = GeminiClient(api_key="bad").generate("p")
    assert out.startswith("ERROR:gemini HTTP 400") and "API key not valid" in out


def test_unexpected_shape_is_error(post):
    post({"choices": []})
    assert OpenAICompatibleClient(model="m").generate("p").startswith("ERROR:openai unexpected response")
