"""
Wildcards without a shell (#25). Backup experiment 2026-11-16: the model's
`du -sh /home/*` could never work because shell=False does not expand `*`.
"""

import pytest

from analyzers.command import CommandRejected, CommandRunner, expand_globs, parse


@pytest.fixture
def tree(tmp_path):
    for name, size in [("backups", 300_000), ("peekops", 20_000), ("notes", 10)]:
        d = tmp_path / name
        d.mkdir()
        (d / "f").write_bytes(b"x" * size)
    return tmp_path


def test_absolute_wildcard_is_expanded(tree):
    p = expand_globs(parse(f"du -s {tree}/*"))
    assert p.stages[0] == ["du", "-s", f"{tree}/backups", f"{tree}/notes", f"{tree}/peekops"]


def test_du_over_wildcard_runs(tree):
    out = CommandRunner.run(f"du -sh {tree}/* | sort -h | tail -3")
    assert out.splitlines()[-1].endswith("backups")


def test_unmatched_wildcard_stays_literal(tree):
    assert expand_globs(parse(f"ls {tree}/nothing*")).stages[0][1] == f"{tree}/nothing*"


def test_grep_patterns_are_not_expanded():
    assert expand_globs(parse("grep error.* /etc/os-release")).stages[0][1] == "error.*"


def test_wildcard_cannot_reach_sensitive_paths(tmp_path, monkeypatch):
    # /etc/ss* would expand to /etc/ssh and /etc/ssl on most systems
    from analyzers import command
    monkeypatch.setattr(command.glob, "glob", lambda pat: ["/etc/ssh/ssh_host_ed25519_key", "/etc/ssl"])
    with pytest.raises(CommandRejected):
        expand_globs(parse("cat /etc/ss*"))
    assert CommandRunner.run("cat /etc/ss*").startswith("blocked:")


def test_too_many_matches_rejected(tmp_path, monkeypatch):
    from analyzers import command
    monkeypatch.setattr(command.glob, "glob", lambda pat: [f"/tmp/{i}" for i in range(500)])
    with pytest.raises(CommandRejected):
        expand_globs(parse("ls /tmp/*"))
