"""
A rejected command must not end the investigation (#23).
Disk experiment 2026-11-16: the LLM narrowed to /home, then proposed
`du -sh /home/* 2>/dev/null | sort -hr | head -n 10`; it was blocked and the
loop concluded after 2 turns instead of narrowing further.
"""

from analyzers.react import CommandRunner, ReActEngine
from core.i18n import t


def reply(analysis, command):
    return f"{t('react_analysis')} {analysis}\n{t('react_command')} {command}"


class FakeLLM:
    def __init__(self, replies):
        self.replies, self.prompts = list(replies), []

    def generate(self, prompt):
        self.prompts.append(prompt)
        return self.replies.pop(0) if self.replies else "ERROR:empty"


def test_blocked_command_is_fed_back_and_loop_continues(snapshot, lang, monkeypatch):
    ran = []
    monkeypatch.setattr(CommandRunner, "run", staticmethod(lambda cmd, timeout=8: ran.append(cmd) or "6.9G\t/home/qcov/backups"))
    llm = FakeLLM([
        reply("/home이 7.7G로 가장 큼", "du -sh /home/* 2>/dev/null | sort -hr | head -n 10"),
        reply("리다이렉션 없이 재시도", "du -sh /home/qcov/backups /home/qcov/peekops"),
        reply("오래된 백업 누적", "DONE"),
    ])
    result = ReActEngine(llm=llm).run(snapshot(), ["Disk full: / 95% (free: 2GB)"])

    assert len(result.turns) == 3                                    # not concluded at the block
    assert result.turns[0].command.startswith("[blocked] du -sh /home/*")
    assert "2>" not in ran[0] and ran == ["du -sh /home/qcov/backups /home/qcov/peekops"]
    # the model saw why it was blocked
    assert "operator not allowed" in llm.prompts[1]


def test_rule_tells_model_not_to_redirect(lang):
    assert "2>/dev/null" in t("react_rules")


def test_every_turn_blocked_still_ends(snapshot, en, monkeypatch):
    bad = reply("x", "ls ; reboot")
    result = ReActEngine(llm=FakeLLM([bad] * 10)).run(snapshot(), ["Disk full: / 95% (free: 2GB)"])
    assert 1 <= len(result.turns) <= 4                               # bounded by MAX_REACT_TURNS
