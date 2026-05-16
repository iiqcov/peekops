"""
analyzers/llm.py
Ollama LLM client.
"""

from __future__ import annotations
import requests
import config


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

    def generate(self, prompt: str) -> str:
        try:
            resp = requests.post(self._url, json={
                "model"  : self._model,
                "prompt" : prompt,
                "think"  : False,
                "stream" : False,
                "options": {
                    "num_ctx"    : 2048,
                    "num_predict": self._max_tokens,
                    "temperature": 0.1,
                },
            }, timeout=self._timeout)
            return resp.json().get("response", "").strip()
        except requests.exceptions.Timeout:
            return "ERROR:timeout"
        except Exception as e:
            return f"ERROR:{e}"
