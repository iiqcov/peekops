"""
Command output budget (#19: stderr noise hid du results in a disk experiment).
"""

import os

from analyzers.command import OUTPUT_LIMIT, CommandRunner, format_output

DENIED = "\n".join(f"du: cannot read directory '/var/spool/x{i}': Permission denied" for i in range(40))
SIZES = "9.8G\t/var\n2.1G\t/home\n12K\t/tmp"


def test_stdout_survives_permission_noise():
    # the 2026-11-16 experiment: 600 chars of "Permission denied" hid these sizes
    text = format_output(SIZES, DENIED)
    assert text.startswith(SIZES)
    assert "[stderr: 40 line(s)" in text
    assert len(text) <= OUTPUT_LIMIT


def test_stderr_shown_when_there_is_no_stdout():
    text = format_output("", "Error from server (NotFound): pods \"x\" not found")
    assert text.startswith("Error from server (NotFound)")


def test_long_stdout_still_leaves_room_for_summary():
    text = format_output("x" * 5000, DENIED)
    assert len(text) <= OUTPUT_LIMIT and text.endswith("]")


def test_plain_output_unchanged():
    assert format_output("ok\n", None) == "ok"


def test_real_command_keeps_stdout_first(tmp_path):
    (tmp_path / "a.log").write_text("x" * 1000)
    out = CommandRunner.run(f"ls {tmp_path} /nonexistent-peekops-dir")
    assert out.splitlines()[0].startswith(("a.log", str(tmp_path)))
    assert "[stderr: 1 line(s)" in out


def test_real_command_error_only():
    out = CommandRunner.run("ls /nonexistent-peekops-dir")
    assert "No such file" in out or "cannot access" in out
