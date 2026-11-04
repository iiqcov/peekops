"""
Per-issue ReAct loops (#4).
"""

import config
from analyzers import react
from analyzers.react import CommandRunner, ReActEngine
from notifiers.discord import _parse_answer
from core.i18n import t

SWAP = "Swap overuse: 80%"
MEM  = "Memory pressure: 90% used (available: 1GB)"
DISK = "Disk full: / 95% (free: 2GB)"
ZOMB = "Zombie processes: 2"


def reply(analysis, command="DONE"):
    return f"{t('react_analysis')} {analysis}\n{t('react_command')} {command}"


class ScriptedLLM:
    """Answers by looking at which issue the prompt is focused on."""

    def __init__(self, script):
        self.script = {k: list(v) for k, v in script.items()}
        self.prompts = []

    def generate(self, prompt):
        self.prompts.append(prompt)
        issues_block = prompt.split(t("react_issues"))[1].split("\n\n")[0]
        for issue, replies in self.script.items():
            if issue in issues_block and replies:
                return replies.pop(0)
        return "ERROR:no script"


def no_exec(monkeypatch):
    monkeypatch.setattr(CommandRunner, "run", staticmethod(lambda cmd, timeout=8: "ok"))


def test_single_issue_keeps_one_loop(snapshot, en, monkeypatch):
    no_exec(monkeypatch)
    llm = ScriptedLLM({SWAP: [reply("check", "free -m"), reply("ollama holds swap")]})
    result = ReActEngine(llm=llm).run(snapshot(), [SWAP])
    assert len(result.turns) == 2
    assert t("react_others") not in llm.prompts[0]


def test_each_issue_gets_its_own_turn_budget(snapshot, en, monkeypatch):
    no_exec(monkeypatch)
    full = [reply(f"step {n}", "free -m") for n in range(config.MAX_REACT_TURNS)]
    llm = ScriptedLLM({MEM: list(full), SWAP: list(full)})
    result = ReActEngine(llm=llm).run(snapshot(), [SWAP, MEM])

    per_issue = {}
    for turn in result.turns:
        per_issue[turn.issue] = per_issue.get(turn.issue, 0) + 1
    # before #4 both issues shared MAX_REACT_TURNS in total
    assert per_issue == {MEM: config.MAX_REACT_TURNS, SWAP: config.MAX_REACT_TURNS}


def test_focus_issue_and_others_are_separated_in_prompt(snapshot, en, monkeypatch):
    no_exec(monkeypatch)
    llm = ScriptedLLM({MEM: [reply("a")], SWAP: [reply("b")]})
    ReActEngine(llm=llm).run(snapshot(), [SWAP, MEM])
    first = llm.prompts[0]
    focus, others = first.split(t("react_others"))
    assert MEM in focus.split(t("react_issues"))[1]   # HIGH first
    assert SWAP in others


def test_highest_severity_first_and_issue_cap(snapshot, en, monkeypatch):
    no_exec(monkeypatch)
    monkeypatch.setattr(config, "MAX_ISSUES_PER_RUN", 2)
    llm = ScriptedLLM({ZOMB: [reply("z")], DISK: [reply("d")], MEM: [reply("m")], SWAP: [reply("s")]})
    result = ReActEngine(llm=llm).run(snapshot(), [ZOMB, DISK, SWAP, MEM])
    assert [turn.issue for turn in result.turns] == [MEM, ZOMB]
    assert t("ans_skipped") in result.answer


def test_time_budget_stops_new_loops(snapshot, en, monkeypatch):
    no_exec(monkeypatch)
    clock = {"now": 1000.0}
    monkeypatch.setattr(react.time, "time", lambda: clock["now"])
    monkeypatch.setattr(config, "REACT_TIME_BUDGET_SEC", 100)

    class SlowLLM(ScriptedLLM):
        def generate(self, prompt):
            clock["now"] += 50   # every LLM call takes 50s
            return super().generate(prompt)

    llm = SlowLLM({MEM: [reply("m1", "free -m"), reply("m2")], SWAP: [reply("s")]})
    result = ReActEngine(llm=llm).run(snapshot(), [SWAP, MEM])
    assert [turn.issue for turn in result.turns] == [MEM]
    assert f"{SWAP}: {t('ans_skipped')}" in result.answer


def test_combined_answer_takes_highest_severity(snapshot, lang, monkeypatch):
    no_exec(monkeypatch)
    llm = ScriptedLLM({MEM: [reply("oom risk, critical")], SWAP: [reply("minor, benign")]})
    result = ReActEngine(llm=llm).run(snapshot(), [SWAP, MEM])
    assert result.answer.startswith(f"{t('ans_severity')}: HIGH")


def test_combined_answer_parses_for_discord(snapshot, lang, monkeypatch):
    no_exec(monkeypatch)
    llm = ScriptedLLM({MEM: [reply("ollama leak")], SWAP: [reply("swap from ollama")]})
    result = ReActEngine(llm=llm).run(snapshot(), [SWAP, MEM])
    keys = [t(k) for k in ("ans_severity", "ans_problem", "ans_cause", "ans_steps", "ans_action", "ans_monitor")]
    parsed = _parse_answer(result.answer, keys)
    assert f"{MEM}: ollama leak" in parsed[t("ans_cause")]
    assert f"{SWAP}: swap from ollama" in parsed[t("ans_cause")]
    assert MEM in parsed[t("ans_steps")] and SWAP in parsed[t("ans_steps")]


def test_all_loops_fail_falls_back(snapshot, en):
    result = ReActEngine(llm=ScriptedLLM({})).run(snapshot(), [SWAP, MEM])
    assert result.latency_sec == -1
