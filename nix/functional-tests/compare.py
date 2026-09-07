#!/usr/bin/env python3
"""Compare two runs of Nix's functional test suite.

A test that fails in both runs is not a defect of the daemon under test. Only
a test that passes the control and fails through the other daemon is one. This
script states that difference, and nothing else.

Usage:

    compare.py CONTROL.json CANDIDATE.json

Each argument is a `meson-logs/testlog.json`, which meson writes as one JSON
object for each test, one to a line. meson writes the file while the run goes
on, so the last line can be half written. This script drops a line it cannot
read, and says how many it dropped.

It needs the standard library alone, so it runs under the Python of any
machine that has Nix. Issue #172 holds the work this belongs to.
"""

from __future__ import annotations

import json
import sys
from collections import Counter

# meson gives each test a name such as `main - nix-functional-tests:gc`. The
# part in front of the space is the suite, and the part after the colon is the
# script.
_SEPARATOR = " - "


def read_log(path: str) -> tuple[dict[str, str], int]:
    """The result of each test in *path*, and the number of unreadable lines."""
    results: dict[str, str] = {}
    broken = 0
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                broken += 1
                continue
            name = entry.get("name")
            result = entry.get("result")
            if isinstance(name, str) and isinstance(result, str):
                results[short_name(name)] = result
    return results, broken


def short_name(name: str) -> str:
    """`main - nix-functional-tests:gc` becomes `main:gc`."""
    suite, _, rest = name.partition(_SEPARATOR)
    _, _, script = rest.partition(":")
    return f"{suite}:{script}" if script else name


def totals(results: dict[str, str]) -> str:
    counts = Counter(results.values())
    parts = [f"{counts[key]} {key}" for key in ("OK", "SKIP", "FAIL", "TIMEOUT") if counts[key]]
    return f"{', '.join(parts)}  ({len(results)} tests)"


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2

    control, control_broken = read_log(argv[1])
    candidate, candidate_broken = read_log(argv[2])

    print(f"control    {totals(control)}")
    print(f"candidate  {totals(candidate)}")
    for label, broken in (("control", control_broken), ("candidate", candidate_broken)):
        if broken:
            print(f"note: dropped {broken} unreadable line(s) from the {label} log")

    only_control = sorted(set(control) - set(candidate))
    only_candidate = sorted(set(candidate) - set(control))
    if only_control:
        print(f"\nnot in the candidate run ({len(only_control)}):")
        for name in only_control:
            print(f"  {name}")
    if only_candidate:
        print(f"\nnot in the control run ({len(only_candidate)}):")
        for name in only_candidate:
            print(f"  {name}")

    # A regression is the answer this script exists to give.
    regressions = sorted(
        name for name, result in candidate.items() if result in ("FAIL", "TIMEOUT") and control.get(name) == "OK"
    )
    repairs = sorted(
        name for name, result in candidate.items() if result == "OK" and control.get(name) in ("FAIL", "TIMEOUT")
    )
    other = sorted(
        f"{name}: {control[name]} -> {candidate[name]}"
        for name in set(control) & set(candidate)
        if control[name] != candidate[name] and name not in regressions and name not in repairs
    )

    print(f"\n=== REGRESSIONS ({len(regressions)}) ===")
    for name in regressions:
        print(f"  {name}")
    if repairs:
        print(f"\n=== ONLY THE CANDIDATE PASSES ({len(repairs)}) ===")
        for name in repairs:
            print(f"  {name}")
    if other:
        print(f"\n=== OTHER CHANGES ({len(other)}) ===")
        for line in other:
            print(f"  {line}")

    shared_failures = sorted(
        name
        for name in set(control) & set(candidate)
        if control[name] in ("FAIL", "TIMEOUT") and candidate[name] in ("FAIL", "TIMEOUT")
    )
    print(f"\n=== FAILS IN BOTH ({len(shared_failures)}) — a defect of Nix or of this harness ===")
    for name in shared_failures:
        print(f"  {name}")

    return 1 if regressions else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
