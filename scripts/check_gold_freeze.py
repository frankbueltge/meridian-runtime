#!/usr/bin/env python3
"""Fail when a frozen gold standard's bytes have moved
(task-packets/N1-T02.yaml R3).

A gold standard is the fixed set of correct answers everything else is judged
against. "Frozen" is only worth something if it is checkable, so this script is
the check: for every entry in ``benchmarks/meridianbench/fixtures/FROZEN.json``
it recomputes the file's sha256 and compares it to the pinned value.

Run by CI on every push. Offline, dependency-free, read-only — it opens no
database, contacts no network, and writes nothing.

Exit codes:
  0  every registered version still hashes to its pinned value
  1  at least one registered version moved, or its file is missing/unreadable
  2  the registry itself is missing, unreadable, or malformed

Why a moved gold set is an error and not a warning: a changed standard makes
every measurement recorded against that ``set_id`` mean something other than
what it says. The correct way to change a standard is a NEW ``set_id`` with its
own entry, leaving the old one intact so past results stay interpretable. There
is deliberately no ``--update`` flag: a script that can re-pin a hash on demand
is not a freeze, it is a formality.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = REPO_ROOT / "benchmarks" / "meridianbench" / "fixtures" / "FROZEN.json"

_EXIT_OK = 0
_EXIT_MOVED = 1
_EXIT_REGISTRY_UNUSABLE = 2


def _sha256(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def main() -> int:
    if not REGISTRY_PATH.is_file():
        print(f"check_gold_freeze: registry not found: {REGISTRY_PATH}", file=sys.stderr)
        return _EXIT_REGISTRY_UNUSABLE

    try:
        registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"check_gold_freeze: registry unusable: {exc}", file=sys.stderr)
        return _EXIT_REGISTRY_UNUSABLE

    frozen = registry.get("frozen")
    if not isinstance(frozen, dict):
        print("check_gold_freeze: registry has no 'frozen' object", file=sys.stderr)
        return _EXIT_REGISTRY_UNUSABLE

    if not frozen:
        # An empty registry is honest (nothing is frozen yet) and must not be
        # reported as success-by-vacuity — say so, then pass.
        print("check_gold_freeze: registry is empty — no gold standard is frozen yet.")
        return _EXIT_OK

    failures: list[str] = []
    for set_id in sorted(frozen):
        entry = frozen[set_id]
        if not isinstance(entry, dict):
            failures.append(f"{set_id}: registry entry is not an object")
            continue

        relative = entry.get("path")
        expected = entry.get("sha256")
        if not isinstance(relative, str) or not isinstance(expected, str):
            failures.append(f"{set_id}: registry entry needs string 'path' and 'sha256'")
            continue

        path = REPO_ROOT / relative
        try:
            actual = _sha256(path.read_bytes())
        except OSError as exc:
            failures.append(f"{set_id}: cannot read {relative} ({exc})")
            continue

        if actual != expected:
            failures.append(
                f"{set_id}: {relative} MOVED\n"
                f"    pinned: {expected}\n"
                f"    actual: {actual}\n"
                "    A frozen standard is never edited. Give the changed standard a new "
                "set_id with its own registry entry and leave this one intact."
            )
        else:
            print(f"check_gold_freeze: {set_id} frozen at {expected} — unchanged.")

    if failures:
        print("", file=sys.stderr)
        for failure in failures:
            print(f"check_gold_freeze: {failure}", file=sys.stderr)
        return _EXIT_MOVED

    print(f"check_gold_freeze: {len(frozen)} frozen version(s) verified.")
    return _EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
