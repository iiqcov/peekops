"""
notifiers/discord.py
Discord Webhook notifiers — alert and daily report.
"""

from __future__ import annotations
from datetime import datetime
import requests
import config
from core.i18n import t
from core.models import DiagnosticResult


def _truncate(text: str, n: int = 900) -> str:
    return text[:n] + "..." if len(text) > n else text


def _parse_answer(answer: str, keys: list[str]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    current_key: str | None = None
    current_val: list[str]  = []
    for line in answer.splitlines():
        matched = False
        for key in keys:
            if line.startswith(f"{key}:"):
                if current_key:
                    parsed[current_key] = "\n".join(current_val).strip()
                current_key, current_val = key, [line.split(":", 1)[-1].strip()]
                matched = True
                break
        if not matched and current_key:
            current_val.append(line)
    if current_key:
        parsed[current_key] = "\n".join(current_val).strip()
    return parsed


class AlertNotifier:
    """Sends incident alerts to Discord."""

    SEVERITY_COLOR = {"HIGH": 0xFF0000, "MED": 0xFF8C00, "LOW": 0xFFD700}
    SEVERITY_EMOJI = {"HIGH": "🚨", "MED": "⚠️", "LOW": "🔔"}

    def __init__(self, webhook: str = config.DISCORD_ALERT_WEBHOOK):
        self._webhook = webhook

    def notify(self, issues: list[str], result: DiagnosticResult) -> None:
        if not self._webhook:
            return

        severity = self._parse_severity(result.answer)
        if severity not in ("HIGH", "MED", "LOW"):
            return

        keys   = [t(k) for k in ("ans_severity","ans_problem","ans_cause","ans_steps","ans_action","ans_monitor")]
        parsed = _parse_answer(result.answer, keys)
        emoji  = self.SEVERITY_EMOJI.get(severity, "ℹ️")
        color  = self.SEVERITY_COLOR.get(severity, 0x888888)
        issue_text = "\n".join(f"• {i}" for i in issues) or t("ans_none")

        payload = {
            "username": t("alert_user"),
            "embeds": [{
                "title" : f"{emoji} {t('alert_title', severity)}",
                "color" : color,
                "fields": [
                    {"name": t("alert_field_issue"), "value": f"```{issue_text}```",                                           "inline": False},
                    {"name": t("alert_field_prob"),  "value": _truncate(parsed.get(t("ans_problem"), t("ans_unknown")), 200),         "inline": True},
                    {"name": t("alert_field_cause"), "value": _truncate(parsed.get(t("ans_cause"),   t("ans_unknown")), 200),         "inline": True},
                    {"name": t("alert_field_steps"), "value": f"```{_truncate(parsed.get(t('ans_steps'), t('ans_none')), 700)}```",   "inline": False},
                    {"name": t("alert_field_act"),   "value": f"```{_truncate(parsed.get(t('ans_action'), t('ans_none')), 400)}```",  "inline": False},
                    {"name": t("alert_field_mon"),   "value": _truncate(parsed.get(t("ans_monitor"), t("ans_none")), 300),            "inline": False},
                ],
                "footer"   : {"text": t("alert_footer", result.latency_sec, result.tok_per_sec, result.tokens_out // max(config.LLM_MAX_TOKENS, 1))},
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }],
        }
        self._send(payload, severity)

    def _parse_severity(self, answer: str) -> str:
        sev_key = t("ans_severity")
        for line in answer.splitlines():
            if line.startswith(f"{sev_key}:"):
                return line.split(":", 1)[-1].strip()
        return "NORMAL"

    def _send(self, payload: dict, label: str) -> None:
        try:
            requests.post(self._webhook, json=payload, timeout=5)
            print(f"  [Discord] alert sent ({label})")
        except Exception as e:
            print(f"  [Discord] alert failed: {e}")


class ReportNotifier:
    """Sends daily summary reports to Discord."""

    def __init__(self, webhook: str = config.DISCORD_REPORT_WEBHOOK):
        self._webhook = webhook

    def notify(self, stats: dict) -> None:
        if not self._webhook:
            print("[report] DISCORD_REPORT_WEBHOOK not set")
            return

        sev = stats["severity"]
        if sev.get("HIGH", 0) > 0:
            color, status = 0xFF4B4B, t("report_status_hi")
        elif sev.get("MED", 0) > 0:
            color, status = 0xFF8C00, t("report_status_med")
        else:
            color, status = 0x00CC66, t("report_status_ok")

        def field(name: str, value: str, inline: bool = True) -> dict:
            return {"name": name, "value": value, "inline": inline}

        sev_text = t("report_sev_fmt", sev.get("HIGH",0), sev.get("MED",0), sev.get("LOW",0))

        payload = {
            "username": t("report_user"),
            "embeds": [{
                "title" : t("report_title", stats["date"]),
                "color" : color,
                "fields": [
                    field(t("report_status_ok").split()[1] if status.startswith("✅") else t("report_status_hi").split()[1], status, inline=False),
                    field(t("report_runs"),      t("report_unit_runs", stats['total_rows'])),
                    field(t("report_issues"),    f"{stats['issue_count']} ({sev_text})"),
                    field("─"*16, "", inline=False),
                    field(t("report_cpu_avg"),   f"{stats['cpu_avg']}%"),
                    field(t("report_cpu_max"),   f"{stats['cpu_max']}%"),
                    field(t("report_cpu_over"),  t("report_unit_times", stats['cpu_over_count'])),
                    field("─"*16, "", inline=False),
                    field(t("report_mem_avg"),   f"{stats['mem_avg']}%"),
                    field(t("report_mem_max"),   f"{stats['mem_max']}%"),
                    field("─"*16, "", inline=False),
                    field(t("report_swap_avg"),  f"{stats['swap_avg']}%"),
                    field(t("report_swap_max"),  f"{stats['swap_max']}%"),
                    field(t("report_swap_over"), t("report_unit_times", stats['swap_over_count'])),
                    field("─"*16, "", inline=False),
                    field(t("report_ai_avg"),    f"{stats['ai_latency_avg']}s"),
                    field(t("report_ai_max"),    f"{stats['ai_latency_max']}s"),
                    field(t("report_react"),     t("report_unit_runs", stats['react_runs'])),
                ],
                "footer"   : {"text": t("report_footer", datetime.now().strftime("%Y-%m-%d %H:%M"))},
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }],
        }
        try:
            resp = requests.post(self._webhook, json=payload, timeout=5)
            if resp.status_code in (200, 204):
                print("[report] Discord report sent")
            else:
                print(f"[report] Discord error: {resp.status_code}")
        except Exception as e:
            print(f"[report] Discord failed: {e}")
