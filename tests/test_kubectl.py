"""
kubectl namespace repair (#2).
"""

import pytest

from analyzers import kubectl
from analyzers.react import ReActEngine
from core.i18n import t


@pytest.mark.parametrize("given, expected", [
    # the reported failure
    ("kubectl describe pod ai/ollama-xxx -n ai", "kubectl describe pod ollama-xxx -n ai"),
    # namespace only in the name
    ("kubectl describe pod ai/ollama-0", "kubectl describe pod ollama-0 -n ai"),
    # flag forms
    ("kubectl describe pod ai/ollama-0 --namespace ai", "kubectl describe pod ollama-0 -n ai"),
    ("kubectl describe pod ai/ollama-0 --namespace=ai", "kubectl describe pod ollama-0 -n ai"),
    ("kubectl describe -n ai pod ai/ollama-0", "kubectl describe pod ollama-0 -n ai"),
    # logs takes a pod name directly
    ("kubectl logs ai/ollama-0 -n ai --tail=50", "kubectl logs ollama-0 --tail=50 -n ai"),
    # inside a pipeline
    ("kubectl describe pod ai/ollama-0 -n ai | tail -20", "kubectl describe pod ollama-0 -n ai | tail -20"),
])
def test_namespace_slash_name_is_rewritten(given, expected):
    assert kubectl.normalize(given) == expected


@pytest.mark.parametrize("cmd", [
    "kubectl describe pod ollama-0 -n ai",
    "kubectl describe pod/ollama-0 -n ai",       # valid <type>/<name>
    "kubectl logs deploy/ollama -n ai",         # valid <type>/<name>
    "kubectl get pods -A",
    "kubectl get pods -n ai -o wide",
    "ps aux | head -5",
])
def test_valid_commands_are_unchanged(cmd):
    assert kubectl.normalize(cmd) == cmd


def test_react_runs_repaired_command(en):
    engine = ReActEngine(llm=None)
    reply = f"{t('react_analysis')} check pod\n{t('react_command')} kubectl describe pod ai/ollama-0 -n ai"
    _, command, _ = engine._parse(reply)
    assert command == "kubectl describe pod ollama-0 -n ai"


def test_pod_issue_text_does_not_suggest_slash_form(lang):
    text = t("issue_pod", "ai", "ollama-0", "CrashLoopBackOff")
    assert "ai/ollama-0" not in text
    assert "ollama-0" in text and "ai" in text
