"""
Disk hint shows two levels, largest last, in one call (#25).
"""

from analyzers.command import OUTPUT_LIMIT, CommandRunner
from analyzers.react import ISSUE_HINTS


def test_disk_hint_is_deep_and_sorted():
    hints = {ISSUE_HINTS["Disk full"], ISSUE_HINTS["디스크"]}
    assert hints == {"du -h --max-depth=2 /var /home /tmp | sort -h | tail -15"}


def test_disk_hint_passes_policy_and_fits_budget():
    hint = ISSUE_HINTS["Disk full"]
    assert CommandRunner.sanitize(hint)
    out = CommandRunner.run(hint)
    assert out and len(out) <= OUTPUT_LIMIT and not out.startswith("blocked")
