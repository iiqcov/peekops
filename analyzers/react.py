"""
analyzers/react.py
ReAct (Reasoning + Acting) loop engine.
"""

from __future__ import annotations
import time
import config
from core.i18n import t
from core.models import ServerSnapshot, DiagnosticResult, ReActTurn
from analyzers.llm import LLMClient
from analyzers.command import CommandRejected, CommandRunner, parse as parse_command  # noqa: F401
from analyzers import kubectl

# ── Investigation hints by issue keyword ─────────────────────────────────────

# Short ps columns only (#21): full command lines (`ps aux`) fill the 600-char
# output budget with one long process and hide the rest, and may carry secrets
# passed as arguments. etime helps spot a young process that is already large.
ISSUE_HINTS: dict[str, str] = {
    "OOMKilled"       : "kubectl describe pod <name> -n <namespace>",
    "Pod abnormal"    : "kubectl describe pod <name> -n <namespace>",
    "Pod 이상"        : "kubectl describe pod <name> -n <namespace>",
    "Swap overuse"    : "ps -eo pid,rss,%mem,etime,comm --sort=-rss | head -10",
    "스왑"            : "ps -eo pid,rss,%mem,etime,comm --sort=-rss | head -10",
    "CPU overload"    : "ps -eo pid,%cpu,%mem,etime,comm --sort=-%cpu | head -10",
    "CPU 과부하"      : "ps -eo pid,%cpu,%mem,etime,comm --sort=-%cpu | head -10",
    "Disk full"       : "du -sh /var /home /tmp",
    "디스크"          : "du -sh /var /home /tmp",
    "Zombie"          : "ps -eo pid,ppid,stat,etime,comm | grep Z",
    "좀비"            : "ps -eo pid,ppid,stat,etime,comm | grep Z",
    "Memory pressure" : "ps -eo pid,rss,%mem,etime,comm --sort=-rss | head -10",
    "메모리"          : "ps -eo pid,rss,%mem,etime,comm --sort=-rss | head -10",
    "Internet"        : "ip route",
    "인터넷"          : "ip route",
}


class PromptBuilder:
    """Builds ReAct prompts from server state and history."""

    @staticmethod
    def build(snap: ServerSnapshot, issues: list[str], history: list[ReActTurn]) -> str:
        cpu = snap.cpu
        mem = snap.memory
        os_ = snap.os
        net = snap.network

        disk_lines = "\n".join(
            f"  {d['mount']}: {d['percent']}% (free {d['free_gb']}GB)"
            for d in snap.disk
            if isinstance(d, dict) and "percent" in d
        )

        history_text = ""
        for h in history:
            history_text += f"\n\n[Turn {h.turn}] {h.analysis}"
            if h.command:
                history_text += f"\n  ran: {h.command}"
            if h.result:
                history_text += f"\n  result:\n{h.result[:400]}"

        hints = [hint for kw, hint in ISSUE_HINTS.items()
                 if any(kw in i for i in issues) and hint not in []]
        seen, unique_hints = set(), []
        for h in hints:
            if h not in seen:
                seen.add(h)
                unique_hints.append(h)

        hint_text  = "\n".join(f"- {h}" for h in unique_hints) or "none"
        hist_text  = history_text or f" {t('react_none')}"
        turn_num   = len(history) + 1
        analysis_p = t("react_analysis").rstrip(":")
        command_p  = t("react_command").rstrip(":")

        return f"""{t('react_role')}

{t('react_state')}
hostname : {os_.get('hostname')}  |  OS: {os_.get('os')}  |  uptime: {os_.get('uptime_hours')}h
CPU      : {cpu.get('percent')}%  load {cpu.get('load_1m')}/{cpu.get('load_5m')}/{cpu.get('load_15m')} (ratio {cpu.get('load_ratio')})
memory   : {mem.get('percent')}%  {mem.get('used_gb')}GB/{mem.get('total_gb')}GB  avail {mem.get('available_gb')}GB
swap     : {mem.get('swap_percent')}%
disk     :
{disk_lines or '  no data'}
internet : {t('connected') if net.get('internet_ok') else t('disconnected')}
zombies  : {os_.get('zombie_count')}

{t('react_issues')}
{chr(10).join(f'- {i}' for i in issues)}

{t('react_hints')}
{hint_text}

{t('react_history')}{hist_text}

---
{t('react_turn', turn_num)}
{t('react_inst')}

{t('react_rules')}

{t('react_format').replace('analysis:', f'{analysis_p}:').replace('command:', f'{command_p}:')}
"""


class ReActEngine:
    """
    Runs a ReAct reasoning loop:
      LLM analyzes → suggests command → executes → feeds result back → repeat.
    """

    def __init__(self, llm: LLMClient | None = None):
        self._llm     = llm or LLMClient()
        self._runner  = CommandRunner()
        self._builder = PromptBuilder()
        self._last_blocked: tuple[str, str] | None = None

    def run(self, snap: ServerSnapshot, issues: list[str]) -> DiagnosticResult:
        if snap.cpu.get("percent", 0) >= config.LLM_SKIP_CPU_THRESHOLD:
            print(f"  [ReAct] CPU >= {config.LLM_SKIP_CPU_THRESHOLD}% → rule-based fallback")
            return self._fallback(issues)

        start   = time.time()
        history : list[ReActTurn] = []

        for turn_num in range(1, config.MAX_REACT_TURNS + 1):
            print(f"\n  [ReAct {turn_num}/{config.MAX_REACT_TURNS}] LLM reasoning...")
            prompt = self._builder.build(snap, issues, history)
            raw    = self._llm.generate(prompt)

            if not raw or raw.startswith("ERROR"):
                print(f"    → LLM error: {raw}")
                break

            print(f"    raw: {repr(raw[:200])}")
            self._last_blocked = None
            analysis, command, done = self._parse(raw)
            print(f"    analysis: {analysis}")

            turn = ReActTurn(turn=turn_num, analysis=analysis, command=command)

            if not done and not command and self._last_blocked:
                # A rejected command used to end the investigation (#23).
                # Tell the model why and let it try again with an allowed one.
                blocked_cmd, reason = self._last_blocked
                turn.command = f"[blocked] {blocked_cmd}"
                turn.result  = t("react_blocked", reason)
                print(f"    → blocked, asking for another command: {reason}")
                history.append(turn)
                continue

            if done or not command:
                history.append(turn)
                print(f"    → concluded ({turn_num} turns)")
                break

            print(f"    → running: {command}")
            turn.result = self._runner.run(command)
            print(f"    → result ({len(turn.result)} chars): {turn.result[:120]}")
            history.append(turn)

        if not history:
            return self._fallback(issues)

        latency = round(time.time() - start, 2)
        print(f"\n  [ReAct done] {len(history)} turns / {latency}s")

        return DiagnosticResult(
            answer      = self._build_answer(issues, history),
            latency_sec = latency,
            tokens_out  = len(history) * config.LLM_MAX_TOKENS,
            tok_per_sec = round((len(history) * config.LLM_MAX_TOKENS) / max(latency, 1), 1),
            turns       = history,
        )

    def _parse(self, raw: str) -> tuple[str, str, bool]:
        analysis = ""
        command  = ""
        done     = False
        ap = t("react_analysis")
        cp = t("react_command")
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith(ap):
                analysis = line.split(":", 1)[-1].strip()
            elif line.startswith(cp):
                cmd = line.split(":", 1)[-1].strip()
                if not cmd or cmd.upper() == "DONE":
                    done = True
                else:
                    fixed   = kubectl.normalize(cmd)
                    command = self._runner.sanitize(fixed)
                    if not command:
                        self._last_blocked = (fixed, self._block_reason(fixed))
        return analysis, command, done

    @staticmethod
    def _block_reason(cmd: str) -> str:
        try:
            parse_command(cmd)
        except CommandRejected as e:
            return str(e)
        return "rejected"

    def _build_answer(self, issues: list[str], history: list[ReActTurn]) -> str:
        last    = history[-1].analysis
        full    = " ".join(h.analysis for h in history).lower()

        if any(k in full for k in ["normal", "benign", "minor", "정상", "무해", "경미"]):
            severity = "LOW"
        elif any(k in full for k in ["oom", "disk full", "unreachable", "critical", "심각", "즉시"]):
            severity = "HIGH"
        else:
            severity = "MED"

        steps = "\n".join(
            f"  [{t('ans_turn', h.turn)}] {h.analysis}" + (f"\n    {t('ans_ran')}: {h.command}" if h.command else "")
            for h in history
        )
        action = t("ans_action_default")
        for h in reversed(history):
            if any(k in h.analysis.lower() for k in ["fix", "restart", "remove", "해결", "조치", "재시작"]):
                action = h.analysis
                break

        sk, pk, ck, stk, ak, mk = (
            t("ans_severity"), t("ans_problem"), t("ans_cause"),
            t("ans_steps"), t("ans_action"), t("ans_monitor"),
        )
        return (
            f"{sk}: {severity}\n{pk}: {issues[0] if issues else t('ans_none')}\n"
            f"{ck}: {last}\n{stk}:\n{steps}\n{ak}: {action}\n"
            f"{mk}: ps aux | grep -w Z; journalctl -n 20"
        )

    def _fallback(self, issues: list[str]) -> DiagnosticResult:
        severity = "LOW"
        for iss in issues:
            if any(k in iss for k in ["CPU overload", "Memory pressure", "unreachable", "OOMKilled",
                                       "CPU 과부하", "메모리 부족", "인터넷"]):
                severity = "HIGH"; break
            if any(k in iss for k in ["Swap", "Disk", "Zombie", "Pod", "스왑", "디스크", "좀비"]):
                severity = "MED"

        sk, pk, ck, stk, ak, mk = (
            t("ans_severity"), t("ans_problem"), t("ans_cause"),
            t("ans_steps"), t("ans_action"), t("ans_monitor"),
        )
        return DiagnosticResult(
            answer=(
                f"{sk}: {severity}\n{pk}: {issues[0] if issues else t('ans_none')}\n"
                f"{ck}: {t('fallback_cause')}\n{stk}: {t('fallback_steps')}\n"
                f"{ak}: {t('fallback_action')}\n{mk}: {' / '.join(issues) or t('ans_none')}"
            ),
            latency_sec=-1, tokens_out=0, tok_per_sec=0,
        )
