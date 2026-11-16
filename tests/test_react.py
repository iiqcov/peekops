import config
from analyzers.react import CommandRunner, ReActEngine
from core.i18n import t


class FakeLLM:
    """Returns scripted responses in order."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    def generate(self, prompt):
        self.prompts.append(prompt)
        return self.responses.pop(0) if self.responses else "ERROR:empty"


# ── CommandRunner ────────────────────────────────────────────────────────────

def test_allowed_read_only_command_passes():
    assert CommandRunner.sanitize("ps aux --sort=-%mem | head -10") == "ps aux --sort=-%mem | head -10"


def test_destructive_command_is_blocked():
    assert CommandRunner.sanitize("rm -rf /tmp/x") == ""


def test_command_outside_allowlist_is_blocked():
    assert CommandRunner.sanitize("python3 -c print(1)") == ""


def test_done_means_no_command():
    assert CommandRunner.sanitize("DONE") == ""


# ── ReActEngine ──────────────────────────────────────────────────────────────

def _reply(analysis, command):
    return f"{t('react_analysis')} {analysis}\n{t('react_command')} {command}"


def test_parse_reads_localized_keys(lang):
    engine = ReActEngine(llm=FakeLLM([]))
    analysis, command, done = engine._parse(_reply("memory leak suspected", "free -m"))
    assert (analysis, command, done) == ("memory leak suspected", "free -m", False)


def test_parse_done(lang):
    engine = ReActEngine(llm=FakeLLM([]))
    _, command, done = engine._parse(_reply("cause found", "DONE"))
    assert command == "" and done is True


def test_loop_runs_command_then_concludes(snapshot, en, monkeypatch):
    ran = []
    monkeypatch.setattr(CommandRunner, "run", staticmethod(lambda cmd, timeout=8: ran.append(cmd) or "ok"))
    llm = FakeLLM([_reply("check memory", "free -m"), _reply("cause found", "DONE")])
    result = ReActEngine(llm=llm).run(snapshot(), ["Memory pressure: 90% used (available: 1GB)"])
    assert ran == ["free -m"]
    assert len(result.turns) == 2
    assert "severity:" in result.answer


def test_high_cpu_skips_llm(snapshot, en):
    llm = FakeLLM([])
    snap = snapshot(cpu={"percent": config.LLM_SKIP_CPU_THRESHOLD + 1})
    result = ReActEngine(llm=llm).run(snap, ["CPU overload: 99%"])
    assert llm.prompts == []
    assert result.latency_sec == -1


def test_llm_error_on_first_turn_falls_back(snapshot, en):
    result = ReActEngine(llm=FakeLLM(["ERROR:timeout"])).run(snapshot(), ["Swap overuse: 80%"])
    assert result.latency_sec == -1
    assert "severity: MED" in result.answer
