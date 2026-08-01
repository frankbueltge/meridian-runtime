#!/usr/bin/env python3
"""Place a field-watch observation where the practice can actually read it.

The watch writes its observations into THIS repository. The practice that
would act on them — field-research — cannot read this repository: its sessions
are scoped to their own (`journal/2026-07-26.md`: "This session's repository
access was scoped to frankbueltge/field-research"). An observation committed
here has no reader.

That is an ADDRESSING problem, and it is the second time the same one has come
up in a day: the labelling commission sat unreachable in this repository until
it was landed inside the receiving practice's own tree. This script does the
same thing for observations, and does the file placement in Python rather than
in YAML so it can be tested without a network, a token or a second checkout.

--- What it does NOT do -----------------------------------------------------

It does not clone, commit, push, or hold a credential. It writes files into a
directory a caller already has, and it refuses to overwrite an observation that
is already there. Everything that needs a token stays in the workflow, where a
reader expects to find it.

--- What the receiving practice is promised ---------------------------------

Nothing. An observation obliges no one: it says what appeared, it does not say
what any of it means, and the practice is free to ignore every one of them.
That promise is written into the README this script maintains, in the delivery
itself rather than in a commit message nobody opens — the same reason the
observation carries its own disclaimer.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

_EXIT_OK = 0
_EXIT_INPUT_UNUSABLE = 2
_EXIT_REFUSED = 3

README_NAME = "README.md"

README = """# The field watch — what Meridian's own instrument saw

Dated observations from `meridian-runtime`, the runtime this practice owns and
uses. Each file lists sources that appeared in this practice's subject —
end-to-end automation of AI research, and the checking of its outputs — and
were not already in the watch's register.

**These are observations, not readings.** Nothing here has been classified,
nothing has been judged, and nothing here implies that anything should change.
Deciding what a finding means is this practice's work, not its instrument's.

**Nothing here obliges anyone.** An observation that is never opened is a
legitimate outcome. The watch exists so that the field can be looked at without
a person having to remember to look; whether looking leads anywhere is a
separate question and this file does not answer it.

They are delivered here because they were previously written somewhere this
practice cannot read — its sessions are scoped to this repository. That was an
addressing mistake, made twice in one day, and this directory is the repair.

Provenance: the watch runs nightly at 01:10 UTC against a frozen set of
searches with a frozen inclusion filter, is fail-closed (a failed sweep writes
nothing and reports non-zero, so silence means the field was quiet and never
that the fetch broke), and calls no model at any point.
"""


def deliver(observation_path: Path, target_dir: Path, *, force: bool = False) -> Path:
    """Copy ``observation_path`` into ``target_dir`` and ensure the README.

    Returns the written path. Raises ``FileExistsError`` when an observation
    for that date is already delivered and ``force`` is not set — a delivered
    observation is a record, and silently replacing one would make the record
    unreliable in exactly the way the whole apparatus exists to prevent.
    """
    document = json.loads(observation_path.read_text(encoding="utf-8"))
    date = str(document["date"])

    target_dir.mkdir(parents=True, exist_ok=True)
    readme = target_dir / README_NAME
    # Rewrite the README only when it is absent or has drifted, so a delivery
    # that changes nothing leaves no diff and the receiving repository's history
    # stays readable.
    if not readme.exists() or readme.read_text(encoding="utf-8") != README:
        readme.write_text(README, encoding="utf-8")

    destination = target_dir / f"{date}.json"
    if destination.exists() and not force:
        raise FileExistsError(destination)
    shutil.copyfile(observation_path, destination)
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="deliver_observation.py",
        description=(
            "Place a field-watch observation into the receiving practice's own tree, so it "
            "has a reader. No network, no credential, no commit."
        ),
    )
    parser.add_argument("--observation", required=True, type=Path)
    parser.add_argument(
        "--into",
        required=True,
        type=Path,
        help="Directory inside the receiving practice's checkout, e.g. <repo>/watch.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace an already-delivered observation for the same date. Off by default.",
    )
    args = parser.parse_args(argv)

    try:
        written = deliver(args.observation, args.into, force=args.force)
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        if isinstance(exc, FileExistsError):
            print(
                f"deliver_observation: {exc.args[0]} is already delivered — refusing to "
                "replace a record. Pass --force only if you mean to.",
                file=sys.stderr,
            )
            return _EXIT_REFUSED
        print(f"deliver_observation: cannot use {args.observation} ({exc})", file=sys.stderr)
        return _EXIT_INPUT_UNUSABLE

    print(json.dumps({"delivered": str(written)}, sort_keys=True))
    return _EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
