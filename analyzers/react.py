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
from analyzers.command import CommandRunner  # noqa: F401  (re-exported)

# ── Investigation hints by issue keyword ─────────────────────────────────────

ISSUE_HINTS: dict[str, str] = {
    "OOMKilled"       : "kubectl describe pod <name> -n <namespace>",
    "Pod abnormal"    : "kubectl describe pod <name> -n <namespace>",
    "Pod 이상"        : "kubectl describe pod <name> -n <namespace>",
    "Swap overuse"    : "ps aux --sort=-%mem | head -10",
    "스왑"            : "ps aux --sort=-%mem | head -10",
    "CPU overload"    : "ps aux --sort=-%cpu | head -10",
    "CPU 과부하"      : "ps aux --sort=-%cpu | head -10",
    "Disk full"       : "du -sh /var /home /tmp",
    "디스크"          : "du -sh /var /home /tmp",
    "Zombie"          : "ps aux | grep Z",
    "좀비"            : "ps aux | grep Z",
    "Memory pressure" : "ps aux --sort=-%mem | head -10",
    "메모리"          : "ps aux --sort=-%mem | head -10",
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
            analysis, command, done = self._parse(raw)
            print(f"    analysis: {analysis}")

            turn = ReActTurn(turn=turn_num, analysis=analysis, command=command)

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
                    command = self._runner.sanitize(cmd)
        return analysis, command, done

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
            f"  [turn {h.turn}] {h.analysis}" + (f"\n    ran: {h.command}" if h.command else "")
            for h in history
        )
        action = "refer to investigation above"
        for h in reversed(history):
            if any(k in h.analysis.lower() for k in ["fix", "restart", "remove", "해결", "조치", "재시작"]):
                action = h.analysis
                break

        sk, pk, ck, stk, ak, mk = (
            t("ans_severity"), t("ans_problem"), t("ans_cause"),
            t("ans_steps"), t("ans_action"), t("ans_monitor"),
        )
        return (
            f"{sk}: {severity}\n{pk}: {issues[0] if issues else 'none'}\n"
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
                f"{sk}: {severity}\n{pk}: {issues[0] if issues else 'none'}\n"
                f"{ck}: {t('fallback_cause')}\n{stk}: {t('fallback_steps')}\n"
                f"{ak}: {t('fallback_action')}\n{mk}: {' / '.join(issues) or 'none'}"
            ),
            latency_sec=-1, tokens_out=0, tok_per_sec=0,
        )
