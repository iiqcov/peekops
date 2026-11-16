"""
Short ps hints (#21): in the memory experiment `ps aux` showed only the header
and one long llama-server line within 600 chars, so the growing process was
never visible and the resident LLM was blamed.
"""

import subprocess

from analyzers.command import OUTPUT_LIMIT, CommandRunner
from analyzers.react import ISSUE_HINTS
from core.i18n import t

PS_HINTS = sorted({h for h in ISSUE_HINTS.values() if h.startswith("ps")})


def test_no_hint_prints_full_command_lines():
    assert PS_HINTS
    assert all("aux" not in h for h in PS_HINTS)


def test_hints_pass_the_command_policy():
    assert all(CommandRunner.sanitize(h) for h in PS_HINTS)


def test_top_ten_rows_fit_in_the_budget():
    for hint in (h for h in PS_HINTS if "head -10" in h):
        out = CommandRunner.run(hint)
        assert len(out) <= OUTPUT_LIMIT
        full = subprocess.run(hint, shell=True, capture_output=True, text=True).stdout.strip()
        # header + up to 10 processes, nothing dropped by the 600-char budget
        assert len(out.splitlines()) == len(full.splitlines()) and len(full) <= OUTPUT_LIMIT
        assert max(len(r) for r in out.splitlines()) < 80


def test_prompt_rule_mentions_short_ps(lang):
    assert "ps -eo" in t("react_rules")
