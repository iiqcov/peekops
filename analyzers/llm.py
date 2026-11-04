"""
analyzers/llm.py
LLM clients. Ollama (local) is the default; other providers are opt-in.

  LLM_PROVIDER=ollama   local Ollama (default)
  LLM_PROVIDER=openai   any OpenAI-compatible /chat/completions server
                        (llama.cpp server, vLLM, LM Studio locally, or a cloud API)
  LLM_PROVIDER=gemini   Google Gemini API

The prompt carries server state and command output (process lists, logs).
Sending it to a non-local endpoint requires LLM_ALLOW_EXTERNAL=true.
"""

from __future__ import annotations
import re
from urllib.parse import urlparse

import requests
import config

# Closed reasoning blocks, e.g. "<think>...</think>" (qwen3, deepseek-r1).
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
# An opening tag with no closing tag: the answer was cut off mid-thought.
_THINK_OPEN  = re.compile(r"<think>.*\Z", re.DOTALL | re.IGNORECASE)

# Qwen3 soft switch that turns off thinking for a single prompt.
NO_THINK_SWITCH = "/no_think"

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def strip_thinking(text: str) -> str:
    """Remove reasoning blocks so only the final answer is parsed."""
    text = _THINK_BLOCK.sub("", text or "")
    text = _THINK_OPEN.sub("", text)
    return text.replace("</think>", "").strip()


def is_local(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return (host in LOCAL_HOSTS or host.endswith(".local") or host.endswith(".svc")
            or host.endswith(".svc.cluster.local") or host.startswith("10.")
            or host.startswith("192.168.") or re.match(r"^172\.(1[6-9]|2\d|3[01])\.", host) is not None)


class ExternalLLMNotAllowed(RuntimeError):
    pass


class BaseLLMClient:
    """Shared request handling: errors become "ERROR:..." strings, never exceptions."""

    provider = "base"

    def __init__(self, url: str, model: str, timeout: int = config.LLM_TIMEOUT,
                 max_tokens: int = config.LLM_MAX_TOKENS):
        self._url        = url
        self._model      = model
        self._timeout    = timeout
        self._max_tokens = max_tokens

    @property
    def model(self) -> str:
        return self._model

    def _request(self, prompt: str) -> tuple[str, dict, dict]:
        """Return (url, headers, json body)."""
        raise NotImplementedError

    def _extract(self, data: dict) -> str:
        raise NotImplementedError

    def generate(self, prompt: str) -> str:
        try:
            url, headers, body = self._request(prompt)
            resp = requests.post(url, json=body, headers=headers or None, timeout=self._timeout)
            data = resp.json()
        except requests.exceptions.Timeout:
            return "ERROR:timeout"
        except Exception as e:
            return f"ERROR:{e}"

        if getattr(resp, "status_code", 200) >= 400:
            msg = data.get("error", data) if isinstance(data, dict) else data
            if isinstance(msg, dict):
                msg = msg.get("message", msg)
            return f"ERROR:{self.provider} HTTP {resp.status_code}: {str(msg)[:200]}"
        try:
            raw = self._extract(data) or ""
        except (KeyError, IndexError, TypeError) as e:
            return f"ERROR:{self.provider} unexpected response: {e}"

        answer = strip_thinking(raw)
        if not answer and raw.strip():
            # Token budget spent inside <think>; no answer to parse.
            return "ERROR:thinking-only response (answer truncated)"
        return answer


class OllamaClient(BaseLLMClient):
    provider = "ollama"

    def __init__(self, url: str = config.OLLAMA_URL, model: str = config.OLLAMA_MODEL,
                 timeout: int = config.LLM_TIMEOUT, max_tokens: int = config.LLM_MAX_TOKENS):
        super().__init__(url, model, timeout, max_tokens)

    def _prepare(self, prompt: str) -> str:
        # Some Ollama versions (e.g. 0.23.x) ignore "think": false and stream the
        # reasoning into "response". For Qwen3 the in-prompt switch still works.
        if "qwen3" in self._model.lower() and NO_THINK_SWITCH not in prompt:
            return f"{prompt.rstrip()}\n{NO_THINK_SWITCH}"
        return prompt

    def _request(self, prompt: str):
        return self._url, {}, {
            "model"  : self._model,
            "prompt" : self._prepare(prompt),
            "think"  : False,
            "stream" : False,
            "options": {
                "num_ctx"    : 2048,
                "num_predict": self._max_tokens,
                "temperature": 0.1,
            },
        }

    def _extract(self, data: dict) -> str:
        return data.get("response", "")


class OpenAICompatibleClient(BaseLLMClient):
    provider = "openai"

    def __init__(self, base_url: str = config.OPENAI_BASE_URL, model: str = config.OPENAI_MODEL,
                 api_key: str = config.OPENAI_API_KEY, timeout: int = config.LLM_TIMEOUT,
                 max_tokens: int = config.LLM_MAX_TOKENS):
        super().__init__(base_url.rstrip("/") + "/chat/completions", model, timeout, max_tokens)
        self._api_key = api_key

    def _request(self, prompt: str):
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        return self._url, headers, {
            "model"      : self._model,
            "messages"   : [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "max_tokens" : self._max_tokens,
            "stream"     : False,
        }

    def _extract(self, data: dict) -> str:
        return data["choices"][0]["message"]["content"]


class GeminiClient(BaseLLMClient):
    provider = "gemini"
    BASE = "https://generativelanguage.googleapis.com/v1beta/models"

    def __init__(self, model: str = config.GEMINI_MODEL, api_key: str = config.GEMINI_API_KEY,
                 timeout: int = config.LLM_TIMEOUT, max_tokens: int = config.LLM_MAX_TOKENS):
        super().__init__(f"{self.BASE}/{model}:generateContent", model, timeout, max_tokens)
        self._api_key = api_key

    def _request(self, prompt: str):
        if not self._api_key:
            raise ValueError("GEMINI_API_KEY is not set")
        generation = {"temperature": 0.1, "maxOutputTokens": self._max_tokens}
        if "2.5-flash" in self._model:
            # 2.5 Flash thinks by default and would spend the 200-token budget on it.
            generation["thinkingConfig"] = {"thinkingBudget": 0}
        # Key goes in a header, not the URL, so it never lands in logs.
        return self._url, {"x-goog-api-key": self._api_key}, {
            "contents"        : [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": generation,
        }

    def _extract(self, data: dict) -> str:
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts if not p.get("thought"))


class DisabledClient:
    """Used when the configured provider cannot be built: every call fails, so
    the ReAct engine falls back to rule-based diagnosis instead of crashing."""

    provider = "disabled"

    def __init__(self, reason: str):
        self.reason = reason

    def generate(self, prompt: str) -> str:
        return f"ERROR:LLM disabled — {self.reason}"


# Backward compatible name: LLMClient() is the local Ollama client.
LLMClient = OllamaClient


def create_client_or_disabled(provider: str | None = None) -> BaseLLMClient | DisabledClient:
    try:
        return create_client(provider)
    except (ValueError, ExternalLLMNotAllowed) as e:
        print(f"  [LLM] {e} → rule-based diagnosis only")
        return DisabledClient(str(e))


def create_client(provider: str | None = None) -> BaseLLMClient:
    """Build the configured client. Non-local endpoints need LLM_ALLOW_EXTERNAL=true."""
    provider = (provider or config.LLM_PROVIDER).lower()
    if provider == "ollama":
        client: BaseLLMClient = OllamaClient()
    elif provider == "openai":
        client = OpenAICompatibleClient()
    elif provider == "gemini":
        client = GeminiClient()
    else:
        raise ValueError(f"unknown LLM_PROVIDER: {provider} (ollama | openai | gemini)")

    if not is_local(client._url) and not config.LLM_ALLOW_EXTERNAL:
        raise ExternalLLMNotAllowed(
            f"{provider} endpoint {urlparse(client._url).hostname} is not local. "
            "Server state and command output would leave this host; "
            "set LLM_ALLOW_EXTERNAL=true to allow it."
        )
    return client
