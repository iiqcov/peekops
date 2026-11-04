"""
analyzers/command.py
Read-only command policy and executor for the ReAct loop.

The LLM proposes shell-like commands. They run on a production server, so:
  - commands are tokenized with shlex; the only operator allowed is "|"
  - every pipeline stage is checked against a per-command policy
  - execution uses shell=False with the exact tokens that were checked,
    so nothing the checker did not see (;, &&, $(...), >, ...) can run
"""

from __future__ import annotations
import shlex
import subprocess
from dataclasses import dataclass
from typing import Callable

MAX_COMMAND_LEN = 300
MAX_STAGES      = 4
OUTPUT_LIMIT    = 600

# Shell operators shlex can split out. Only "|" is allowed.
_OPERATOR_CHARS = set("|;&<>()")

# Files that must never be read, even under allowed prefixes.
SENSITIVE_PATHS = (
    "/etc/shadow", "/etc/gshadow", "/etc/sudoers", "/etc/security/opasswd",
    "/etc/ssh/", "/etc/ssl/private", "/etc/rancher/",
    "/proc/kcore", "/proc/self/environ",
)


class CommandRejected(ValueError):
    """Raised when a command violates the read-only policy."""


# ── per-command rules ────────────────────────────────────────────────────────
# Each rule receives the stage's argv (argv[0] is the command) and raises
# CommandRejected when the arguments are not read-only.

def _deny_flags(*flags: str) -> Callable[[list[str]], None]:
    def check(argv: list[str]) -> None:
        for arg in argv[1:]:
            name = arg.split("=", 1)[0]
            if name in flags:
                raise CommandRejected(f"{argv[0]}: option not allowed: {name}")
            # combined short flags, e.g. dmesg -Cx
            if arg.startswith("-") and not arg.startswith("--"):
                short = {f for f in flags if len(f) == 2 and f[0] == "-" and f[1] != "-"}
                if any(f[1] in arg[1:] for f in short):
                    raise CommandRejected(f"{argv[0]}: option not allowed in {arg}")
    return check


def _deny_flag_prefix(*prefixes: str) -> Callable[[list[str]], None]:
    def check(argv: list[str]) -> None:
        for arg in argv[1:]:
            if any(arg.startswith(p) for p in prefixes):
                raise CommandRejected(f"{argv[0]}: option not allowed: {arg}")
    return check


def _all(*checks: Callable[[list[str]], None]) -> Callable[[list[str]], None]:
    def check(argv: list[str]) -> None:
        for c in checks:
            c(argv)
    return check


def _subcommand(allowed: set[str], position: int = 1) -> Callable[[list[str]], None]:
    def check(argv: list[str]) -> None:
        words = [a for a in argv[1:] if not a.startswith("-")]
        if len(words) < position or words[position - 1] not in allowed:
            raise CommandRejected(f"{argv[0]}: subcommand must be one of {sorted(allowed)}")
    return check


def _paths_readable(argv: list[str]) -> None:
    for arg in argv[1:]:
        if arg.startswith("-"):
            continue
        if ".." in arg.split("/"):
            raise CommandRejected(f"{argv[0]}: path traversal not allowed: {arg}")
        if any(arg == p.rstrip("/") or arg.startswith(p) for p in SENSITIVE_PATHS):
            raise CommandRejected(f"{argv[0]}: sensitive path: {arg}")


def _cat_paths(argv: list[str]) -> None:
    files = [a for a in argv[1:] if not a.startswith("-")]
    if not files:
        raise CommandRejected("cat: a file under /proc, /sys or /etc is required")
    for f in files:
        if not f.startswith(("/proc/", "/sys/", "/etc/")):
            raise CommandRejected(f"cat: only /proc, /sys, /etc allowed: {f}")
    _paths_readable(argv)


def _top_batch(argv: list[str]) -> None:
    if not any(a == "-b" or (a.startswith("-") and not a.startswith("--") and "b" in a) for a in argv[1:]):
        raise CommandRejected("top: batch mode (-b) is required")


def _ip_show_only(argv: list[str]) -> None:
    mutating = {"add", "del", "delete", "change", "replace", "set", "flush",
                "append", "prepend", "save", "restore", "exec"}
    hit = mutating.intersection(argv[1:])
    if hit:
        raise CommandRejected(f"ip: modifying action not allowed: {sorted(hit)}")


_NO_FOLLOW = _deny_flags("-f", "--follow", "-F")

ALLOWED_COMMANDS: dict[str, Callable[[list[str]], None] | None] = {
    # process / memory / cpu
    "ps"        : None,
    "top"       : _top_batch,
    "free"      : None,
    "uptime"    : None,
    "who"       : None,
    "w"         : None,
    # disk
    "df"        : None,
    "du"        : _paths_readable,
    "ls"        : _paths_readable,
    # network
    "ss"        : _deny_flags("-K", "--kill"),
    "netstat"   : None,
    "ip"        : _ip_show_only,
    "lsof"      : None,
    # logs
    "dmesg"     : _deny_flags("-c", "-C", "--clear", "--read-clear", "-w", "--follow", "-n", "--console-level", "-D", "-E"),
    "journalctl": _all(_NO_FOLLOW, _deny_flag_prefix("--vacuum", "--rotate", "--flush", "--sync",
                                                     "--relinquish-var", "--smart-relinquish-var",
                                                     "--setup-keys", "--update-catalog")),
    # files
    "cat"       : _cat_paths,
    # text filters (read stdin only)
    "grep"      : _paths_readable,
    "head"      : None,
    "tail"      : _NO_FOLLOW,
    "sort"      : _deny_flags("-o", "--output"),
    "uniq"      : None,
    "wc"        : None,
    "cut"       : None,
    # services
    "systemctl" : _subcommand({"status", "list-units", "list-timers", "list-unit-files",
                               "is-active", "is-failed", "is-enabled", "show"}),
    # kubernetes
    "kubectl"   : _all(_subcommand({"get", "describe", "logs", "top"}),
                       _deny_flags("-f", "--follow", "-w", "--watch", "--watch-only")),
}


@dataclass(frozen=True)
class Pipeline:
    stages: list[list[str]]

    def __str__(self) -> str:
        return " | ".join(shlex.join(s) for s in self.stages)


def parse(cmd: str) -> Pipeline:
    """Tokenize and validate. Raises CommandRejected on any violation."""
    cmd = (cmd or "").strip().strip("`").strip()
    if not cmd or cmd.upper() == "DONE":
        raise CommandRejected("empty command")
    if len(cmd) > MAX_COMMAND_LEN:
        raise CommandRejected(f"command longer than {MAX_COMMAND_LEN} chars")
    if "\n" in cmd or "\r" in cmd:
        raise CommandRejected("multi-line command not allowed")
    if "`" in cmd or "$(" in cmd:
        raise CommandRejected("command substitution not allowed")

    lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    try:
        tokens = list(lex)
    except ValueError as e:  # unbalanced quotes
        raise CommandRejected(f"cannot parse: {e}") from e

    stages: list[list[str]] = [[]]
    for tok in tokens:
        if tok and set(tok) <= _OPERATOR_CHARS:
            if tok != "|":
                raise CommandRejected(f"operator not allowed: {tok}")
            stages.append([])
        else:
            stages[-1].append(tok)

    if any(not s for s in stages):
        raise CommandRejected("empty pipeline stage")
    if len(stages) > MAX_STAGES:
        raise CommandRejected(f"more than {MAX_STAGES} pipeline stages")

    for argv in stages:
        name = argv[0]
        if "/" in name:
            raise CommandRejected(f"command must be a bare name: {name}")
        if name not in ALLOWED_COMMANDS:
            raise CommandRejected(f"command not in allowlist: {name}")
        rule = ALLOWED_COMMANDS[name]
        if rule:
            rule(argv)

    return Pipeline(stages)


class CommandRunner:
    """Validates and executes read-only commands without a shell."""

    @staticmethod
    def sanitize(cmd: str) -> str:
        """Return the normalized command if allowed, else "" (and log why)."""
        if not cmd or cmd.strip().upper() == "DONE":
            return ""
        try:
            return str(parse(cmd))
        except CommandRejected as e:
            print(f"    [blocked] {e}: {cmd}")
            return ""

    @staticmethod
    def run(cmd: str, timeout: int = 8) -> str:
        if not cmd:
            return "no command"
        try:
            pipeline = parse(cmd)
        except CommandRejected as e:
            return f"blocked: {e}"

        procs: list[subprocess.Popen] = []
        try:
            prev_out = subprocess.DEVNULL
            for i, argv in enumerate(pipeline.stages):
                last = i == len(pipeline.stages) - 1
                p = subprocess.Popen(
                    argv, shell=False, stdin=prev_out,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT if last else subprocess.DEVNULL,
                    text=True,
                )
                if procs and procs[-1].stdout:
                    procs[-1].stdout.close()  # let upstream get SIGPIPE
                procs.append(p)
                prev_out = p.stdout
            out, _ = procs[-1].communicate(timeout=timeout)
            for p in procs[:-1]:
                p.wait(timeout=1)
            return (out or "").strip()[:OUTPUT_LIMIT]
        except subprocess.TimeoutExpired:
            return "timeout"
        except FileNotFoundError as e:
            return f"failed: command not found: {e.filename}"
        except Exception as e:
            return f"failed: {e}"
        finally:
            for p in procs:
                if p.poll() is None:
                    p.kill()
                    p.wait()
