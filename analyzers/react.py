"""
analyzers/react.py
ReAct (Reasoning + Acting) loop engine.
"""

from __future__ import annotations
import subprocess
import time
import config
from core.i18n import t
from core.models import Anchor, ServerSnapshot, DiagnosticResult, ReActTurn
from analyzers.llm import LLMClient
from analyzers import anchors as anchor_ctx

# ── Security ──────────────────────────────────────────────────────────────────

BLOCKED_COMMANDS = [
    "rm ", "kill ", "pkill ", "reboot", "shutdown",
    "mkfs", "dd ", "> /", "| sh", "| bash",
    "chmod", "chown", "mv /", "cp /",
    "systemctl stop", "systemctl disable",
]

ALLOWED_PREFIXES = [
    "ps ", "top ", "free ", "df ", "du ",
    "ls ", "cat /proc", "cat /etc",
    "ss ", "netstat ", "ip ",
    "dmesg", "journalctl",
    "uptime", "who", "w ",
    "lsof ", "grep ", "awk ", "sed ",
    "systemctl status", "systemctl list",
    "kubectl get", "kubectl describe", "kubectl logs",
]

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


class CommandRunner:
    """Safely executes read-only shell commands."""

    @staticmethod
    def sanitize(cmd: str) -> str:
        if not cmd or cmd.strip().upper() == "DONE":
            return ""
        cmd = cmd.strip().strip("`").replace('"', "").replace("'", "")
        for blocked in BLOCKED_COMMANDS:
            if blocked in cmd:
                print(f"    [blocked] dangerous: {cmd}")
                return ""
        first = cmd.split("|")[0].strip()
        if not any(first.startswith(p) for p in ALLOWED_PREFIXES):
            print(f"    [blocked] not in allowlist: {cmd}")
            return ""
        return cmd

    @staticmethod
    def run(cmd: str, timeout: int = 8) -> str:
        if not cmd:
            return "no command"
        try:
            proc = subprocess.Popen(
                cmd, shell=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            try:
                out, err = proc.communicate(timeout=timeout)
                return (out + err).strip()[:600]
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                return "timeout"
        except Exception as e:
            return f"failed: {e}"


class PromptBuilder:
    """Builds ReAct prompts from server state and history."""

    @staticmethod
    def build(snap: ServerSnapshot, issues: list[str], history: list[ReActTurn],
              others: list[str] | tuple[str, ...] = (),
              anchor: Anchor | None = None, anchor_context: str = "") -> str:
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

        hints = anchor_ctx.hints(anchor) if anchor else []
        hints += [hint for kw, hint in ISSUE_HINTS.items()
                  if any(kw in i for i in issues) and not (anchor and "<name>" in hint)]
        seen, unique_hints = set(), []
        for h in hints:
            if h not in seen:
                seen.add(h)
                unique_hints.append(h)

        hint_text  = "\n".join(f"- {h}" for h in unique_hints) or "none"
        anchor_text = (
            f"\n{t('react_anchor')}\n{anchor.label} — {anchor.reason}\n{anchor_context}\n"
            if anchor else ""
        )
        others_text = (
            f"\n{t('react_others')}\n" + "\n".join(f"- {o}" for o in others) + "\n"
            if others else ""
        )
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
{anchor_text}{others_text}
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
        self._llm       = llm or LLMClient()
        self._runner    = CommandRunner()
        self._builder   = PromptBuilder()
        self._turn_secs : list[float] = []
        self._contexts  : dict[str, str] = {}

    def run(self, snap: ServerSnapshot, issues: list[str],
            anchors: dict[str, Anchor] | None = None) -> DiagnosticResult:
        if snap.cpu.get("percent", 0) >= config.LLM_SKIP_CPU_THRESHOLD:
            print(f"  [ReAct] CPU >= {config.LLM_SKIP_CPU_THRESHOLD}% → rule-based fallback")
            return self._fallback(issues)
        anchors = anchors or {}
        groups  = self._group_by_anchor(self._prioritize(issues), anchors)
        if len(groups) <= 1:
            return self._run_single(snap, issues, groups[0][1] if groups else None)
        return self._run_per_issue(snap, issues, groups)

    @staticmethod
    def _group_by_anchor(
        ordered: list[str], anchors: dict[str, Anchor],
    ) -> list[tuple[list[str], Anchor | None]]:
        """Issues pointing at the same PID/pod are investigated together (#6)."""
        groups: dict[str, tuple[list[str], Anchor | None]] = {}
        for issue in ordered:
            anchor = anchors.get(issue)
            key    = anchor.key if anchor else f"issue:{issue}"
            groups.setdefault(key, ([], anchor))[0].append(issue)
        return list(groups.values())

    def _context(self, anchor: Anchor | None) -> str:
        if anchor is None:
            return ""
        if anchor.key not in self._contexts:
            print(f"  [anchor] {anchor.label} — {anchor.reason}: collecting context")
            self._contexts[anchor.key] = anchor_ctx.collect(anchor, self._runner.run)
        return self._contexts[anchor.key]

    # ── loops ────────────────────────────────────────────────────────────────

    def _investigate(
        self,
        snap    : ServerSnapshot,
        issues  : list[str],
        deadline: float | None = None,
        others  : list[str] | tuple[str, ...] = (),
        anchor  : Anchor | None = None,
    ) -> list[ReActTurn]:
        """One ReAct loop over `issues`. Stops early when the deadline is near."""
        history : list[ReActTurn] = []
        label   = " / ".join(issues) if others else ""
        context = self._context(anchor)

        for turn_num in range(1, config.MAX_REACT_TURNS + 1):
            if deadline is not None and time.time() + self._turn_estimate() > deadline:
                print(f"    → time budget reached, stopping after {len(history)} turns")
                break
            print(f"\n  [ReAct {turn_num}/{config.MAX_REACT_TURNS}] LLM reasoning...")
            prompt  = self._builder.build(snap, issues, history, others, anchor, context)
            t0      = time.time()
            raw     = self._llm.generate(prompt)
            self._turn_secs.append(time.time() - t0)

            if not raw or raw.startswith("ERROR"):
                print(f"    → LLM error: {raw}")
                break

            print(f"    raw: {repr(raw[:200])}")
            analysis, command, done = self._parse(raw)
            print(f"    analysis: {analysis}")

            turn = ReActTurn(turn=turn_num, analysis=analysis, command=command, issue=label)

            if done or not command:
                history.append(turn)
                print(f"    → concluded ({turn_num} turns)")
                break

            print(f"    → running: {command}")
            turn.result = self._runner.run(command)
            print(f"    → result ({len(turn.result)} chars): {turn.result[:120]}")
            history.append(turn)

        return history

    def _turn_estimate(self) -> float:
        """Time to reserve for one more turn: 1.5x the slowest turn so far, at least 30s."""
        return max(30.0, 1.5 * max(self._turn_secs, default=0.0))

    def _run_single(self, snap: ServerSnapshot, issues: list[str],
                    anchor: Anchor | None = None) -> DiagnosticResult:
        start   = time.time()
        history = self._investigate(snap, issues, anchor=anchor)
        if not history:
            return self._fallback(issues)

        latency = round(time.time() - start, 2)
        print(f"\n  [ReAct done] {len(history)} turns / {latency}s")
        return self._result(self._build_answer(issues, history), history, latency)

    def _run_per_issue(
        self, snap: ServerSnapshot, issues: list[str],
        groups: list[tuple[list[str], Anchor | None]],
    ) -> DiagnosticResult:
        """
        Give each issue (or each group sharing an anchor) its own loop (#4),
        highest rule severity first, within a time budget.
        """
        start    = time.time()
        deadline = start + config.REACT_TIME_BUDGET_SEC
        ordered  = [i for group, _ in groups for i in group]
        targets  = groups[:config.MAX_ISSUES_PER_RUN]
        skipped  = [i for group, _ in groups[config.MAX_ISSUES_PER_RUN:] for i in group]

        findings: list[tuple[str, list[ReActTurn]]] = []
        for n, (group, anchor) in enumerate(targets, 1):
            if time.time() + self._turn_estimate() > deadline:
                skipped.extend(group)
                continue
            label = " / ".join(group)
            print(f"\n  [ReAct issue {n}/{len(targets)}] {label}")
            others = [o for o in issues if o not in group]
            findings.append((label, self._investigate(snap, group, deadline, others, anchor)))

        if not any(h for _, h in findings):
            return self._fallback(issues)

        turns   = [turn for _, h in findings for turn in h]
        latency = round(time.time() - start, 2)
        print(f"\n  [ReAct done] {len(findings)} issues / {len(turns)} turns / {latency}s")
        return self._result(self._build_multi_answer(ordered, findings, skipped), turns, latency)

    @staticmethod
    def _result(answer: str, turns: list[ReActTurn], latency: float) -> DiagnosticResult:
        return DiagnosticResult(
            answer      = answer,
            latency_sec = latency,
            tokens_out  = len(turns) * config.LLM_MAX_TOKENS,
            tok_per_sec = round((len(turns) * config.LLM_MAX_TOKENS) / max(latency, 1), 1),
            turns       = turns,
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

    # ── answers ──────────────────────────────────────────────────────────────

    SEVERITY_RANK = {"NORMAL": 0, "LOW": 1, "MED": 2, "HIGH": 3}

    @staticmethod
    def _ai_severity(history: list[ReActTurn]) -> str:
        full = " ".join(h.analysis for h in history).lower()
        if any(k in full for k in ["normal", "benign", "minor", "정상", "무해", "경미"]):
            return "LOW"
        if any(k in full for k in ["oom", "disk full", "unreachable", "critical", "심각", "즉시"]):
            return "HIGH"
        return "MED"

    @staticmethod
    def _rule_severity(issues: list[str]) -> str:
        severity = "LOW"
        for iss in issues:
            if any(k in iss for k in ["CPU overload", "Memory pressure", "unreachable", "OOMKilled",
                                       "CPU 과부하", "메모리 부족", "인터넷"]):
                return "HIGH"
            if any(k in iss for k in ["Swap", "Disk", "Zombie", "Pod", "스왑", "디스크", "좀비"]):
                severity = "MED"
        return severity

    def _prioritize(self, issues: list[str]) -> list[str]:
        rank = self.SEVERITY_RANK
        return sorted(issues, key=lambda i: -rank[self._rule_severity([i])])  # stable

    @staticmethod
    def _action(history: list[ReActTurn]) -> str | None:
        for h in reversed(history):
            if any(k in h.analysis.lower() for k in ["fix", "restart", "remove", "해결", "조치", "재시작"]):
                return h.analysis
        return None

    @staticmethod
    def _steps(history: list[ReActTurn], indent: str = "  ") -> str:
        return "\n".join(
            f"{indent}[{t('ans_turn', h.turn)}] {h.analysis}"
            + (f"\n{indent}  {t('ans_ran')}: {h.command}" if h.command else "")
            for h in history
        )

    @staticmethod
    def _keys() -> tuple[str, ...]:
        return (t("ans_severity"), t("ans_problem"), t("ans_cause"),
                t("ans_steps"), t("ans_action"), t("ans_monitor"))

    def _build_answer(self, issues: list[str], history: list[ReActTurn]) -> str:
        sk, pk, ck, stk, ak, mk = self._keys()
        return (
            f"{sk}: {self._ai_severity(history)}\n{pk}: {issues[0] if issues else t('ans_none')}\n"
            f"{ck}: {history[-1].analysis}\n{stk}:\n{self._steps(history)}\n"
            f"{ak}: {self._action(history) or t('ans_action_default')}\n"
            f"{mk}: ps aux | grep -w Z; journalctl -n 20"
        )

    def _build_multi_answer(
        self,
        ordered : list[str],
        findings: list[tuple[str, list[ReActTurn]]],
        skipped : list[str],
    ) -> str:
        """One answer for several per-issue loops. Severity = the highest found."""
        sk, pk, ck, stk, ak, mk = self._keys()
        rank = self.SEVERITY_RANK

        severities, causes, steps, actions = [], [], [], []
        for issue, history in findings:
            if history:
                severities.append(self._ai_severity(history))
                causes.append(f"- {issue}: {history[-1].analysis}")
                steps.append(f"  ▸ {issue}\n{self._steps(history, indent='    ')}")
                action = self._action(history)
                if action:
                    actions.append(f"- {issue}: {action}")
            else:
                severities.append(self._rule_severity([issue]))
                causes.append(f"- {issue}: {t('fallback_cause')}")
        for issue in skipped:
            severities.append(self._rule_severity([issue]))
            causes.append(f"- {issue}: {t('ans_skipped')}")

        severity = max(severities, key=lambda s: rank[s])
        return (
            f"{sk}: {severity}\n{pk}: {ordered[0]}\n"
            f"{ck}:\n" + "\n".join(causes) + "\n"
            f"{stk}:\n" + "\n".join(steps) + "\n"
            f"{ak}: " + ("\n" + "\n".join(actions) if actions else t("ans_action_default")) + "\n"
            f"{mk}: ps aux | grep -w Z; journalctl -n 20"
        )

    def _fallback(self, issues: list[str]) -> DiagnosticResult:
        sk, pk, ck, stk, ak, mk = self._keys()
        return DiagnosticResult(
            answer=(
                f"{sk}: {self._rule_severity(issues)}\n{pk}: {issues[0] if issues else t('ans_none')}\n"
                f"{ck}: {t('fallback_cause')}\n{stk}: {t('fallback_steps')}\n"
                f"{ak}: {t('fallback_action')}\n{mk}: {' / '.join(issues) or t('ans_none')}"
            ),
            latency_sec=-1, tokens_out=0, tok_per_sec=0,
        )
