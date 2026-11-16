"""
Read-only command policy (#9).
Every bypass that passed the old prefix/substring checker must now be rejected,
and every command the investigation hints rely on must still pass.
"""

import pytest

from analyzers.command import CommandRejected, CommandRunner, parse
from analyzers.react import ISSUE_HINTS

BYPASSES = [
    # reproduced against the old checker on main (all passed there)
    "ps aux | xargs rm",
    "ls $(id)",
    "sed -i s/a/b/ /etc/hosts",
    "journalctl --vacuum-time=1s",
    "dmesg -C",
    "cat /etc/shadow",
    'awk BEGIN{system("id")}',
    # chaining / redirection / substitution
    "ps aux; rm -rf /tmp/x",
    "ps aux && reboot",
    "ps aux || reboot",
    "ps aux & reboot",
    "df -h > /etc/passwd",
    "cat /etc/hosts >> /tmp/x",
    "grep root < /etc/shadow",
    "ls `id`",
    "ps aux\nreboot",
    # commands outside the allowlist, anywhere in the pipeline
    "ps aux | sh",
    "ps aux | bash",
    "ps aux | tee /tmp/x",
    "python3 -c print(1)",
    "/bin/rm -rf /",
    "kill 1",
    # read-only command, mutating option
    "kubectl delete pod x -n ai",
    "kubectl apply -f x.yaml",
    "kubectl logs x -n ai -f",
    "kubectl get pods -A -w",
    "systemctl stop ollama",
    "systemctl restart k3s",
    "ip route add 1.2.3.4 via 10.0.0.1",
    "ip link set eth0 down",
    "journalctl --rotate",
    "journalctl -f",
    "dmesg --clear",
    "dmesg -Cx",
    "tail -f /var/log/syslog",
    "sort -o /etc/passwd /etc/hosts",
    "ss -K dst 10.0.0.1",
    "top",
    # file reads outside the allowed area
    "cat /home/user/.env",
    "cat /etc/../root/.ssh/id_rsa",
    "cat /etc/ssh/ssh_host_ed25519_key",
    "cat /proc/self/environ",
    "grep -r token /etc/rancher/",
    # shape limits
    "ps aux | head | head | head | head",
    "ps aux |",
    "| ps aux",
    "x" * 400,
]

ALLOWED = [
    "ps aux --sort=-%mem | head -10",
    "ps aux | grep Z",
    "free -m",
    "df -h",
    "du -sh /var /home /tmp",
    "uptime",
    "top -b -n 1 | head -20",
    "ss -tulpn",
    "ip route",
    "ip addr show",
    "dmesg -T | tail -20",
    "journalctl -n 50 --no-pager",
    "journalctl -u k3s -n 50 --no-pager | grep -i error",
    "cat /proc/meminfo",
    "cat /etc/os-release",
    "systemctl status ollama",
    "kubectl get pods -A",
    "kubectl describe pod ollama-0 -n ai",
    "kubectl logs ollama-0 -n ai --tail=50",
    "grep 'Out of memory' /var/log/syslog",
]


@pytest.mark.parametrize("cmd", BYPASSES)
def test_bypass_is_rejected(cmd):
    with pytest.raises(CommandRejected):
        parse(cmd)
    assert CommandRunner.sanitize(cmd) == ""


@pytest.mark.parametrize("cmd", ALLOWED)
def test_read_only_command_passes(cmd):
    assert CommandRunner.sanitize(cmd) != ""


@pytest.mark.parametrize("hint", sorted(set(ISSUE_HINTS.values())))
def test_every_investigation_hint_passes(hint):
    concrete = hint.replace("<name>", "ollama-0").replace("<namespace>", "ai")
    assert CommandRunner.sanitize(concrete) != ""


def test_quotes_are_parsed_not_stripped():
    assert parse("grep 'Out of memory' /var/log/syslog").stages == [
        ["grep", "Out of memory", "/var/log/syslog"]]


def test_run_rechecks_instead_of_trusting_caller():
    assert CommandRunner.run("ps aux; reboot").startswith("blocked:")


def test_run_executes_pipeline_without_shell():
    out = CommandRunner.run("ps aux | head -2")
    assert "PID" in out


def test_shell_variables_are_not_expanded():
    # with shell=True this would list the home directory
    out = CommandRunner.run("ls $HOME")
    assert "$HOME" in out
