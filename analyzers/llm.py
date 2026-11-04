"""
analyzers/llm.py
Ollama LLM client.
"""

from __future__ import annotations
import re
import requests
import config

# Closed reasoning blocks, e.g. "<think>...</think>" (qwen3, deepseek-r1).
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
# An opening tag with no closing tag: the answer was cut off mid-thought.
_THINK_OPEN  = re.compile(r"<think>.*\Z", re.DOTALL | re.IGNORECASE)

# Qwen3 soft switch that turns off thinking for a single prompt.
NO_THINK_SWITCH = "/no_think"


def strip_thinking(text: str) -> str:
    """Remove reasoning blocks so only the final answer is parsed."""
    text = _THINK_BLOCK.sub("", text or "")
    text = _THINK_OPEN.sub("", text)
    return text.replace("</think>", "").strip()


class LLMClient:
    def __init__(
        self,
        url      : str = config.OLLAMA_URL,
        model    : str = config.OLLAMA_MODEL,
        timeout  : int = config.LLM_TIMEOUT,
        max_tokens: int = config.LLM_MAX_TOKENS,
    ):
        self._url       = url
        self._model     = model
        self._timeout   = timeout
        self._max_tokens = max_tokens

    def _prepare(self, prompt: str) -> str:
        # Some Ollama versions (e.g. 0.23.x) ignore "think": false and stream the
        # reasoning into "response". For Qwen3 the in-prompt switch still works.
        if "qwen3" in self._model.lower() and NO_THINK_SWITCH not in prompt:
            return f"{prompt.rstrip()}\n{NO_THINK_SWITCH}"
        return prompt

    def generate(self, prompt: str) -> str:
        try:
            resp = requests.post(self._url, json={
                "model"  : self._model,
                "prompt" : self._prepare(prompt),
                "think"  : False,
                "stream" : False,
                "options": {
                    "num_ctx"    : 2048,
                    "num_predict": self._max_tokens,
                    "temperature": 0.1,
                },
            }, timeout=self._timeout)
            raw = resp.json().get("response", "")
        except requests.exceptions.Timeout:
            return "ERROR:timeout"
        except Exception as e:
            return f"ERROR:{e}"

        answer = strip_thinking(raw)
        if not answer and raw.strip():
            # Token budget spent inside <think>; no answer to parse.
            return "ERROR:thinking-only response (answer truncated)"
        return answer
