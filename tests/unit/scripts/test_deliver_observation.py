"""Delivery across the repository boundary, tested without a network or a
checkout.

The property under test is the one that was missing twice in a day: an
artefact must end up somewhere its reader can open, and a delivery must never
quietly replace a record that is already there.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.deliver_observation import README, README_NAME, deliver, main


def _observation(tmp_path: Path, date: str = "2026-08-02", new_count: int = 2) -> Path:
    path = tmp_path / "observation.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "field-watch-observation.v1",
                "date": date,
                "_note": "An OBSERVATION, not a reading.",
                "new_count": new_count,
                "new": [{"arxiv": "2699.00001", "title": "t", "published": date}],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def test_the_observation_lands_under_its_own_date(tmp_path: Path) -> None:
    target = tmp_path / "field-research" / "watch"
    written = deliver(_observation(tmp_path), target)

    assert written == target / "2026-08-02.json"
    assert json.loads(written.read_text(encoding="utf-8"))["new_count"] == 2


def test_the_readme_travels_with_the_first_delivery(tmp_path: Path) -> None:
    # The promise that an observation obliges nobody has to arrive WITH the
    # data, not in a commit message the receiving practice will not open.
    target = tmp_path / "watch"
    deliver(_observation(tmp_path), target)

    readme = (target / README_NAME).read_text(encoding="utf-8")
    assert readme == README
    assert "observations, not readings" in readme
    assert "obliges anyone" in readme


def test_a_second_delivery_leaves_the_readme_untouched(tmp_path: Path) -> None:
    target = tmp_path / "watch"
    deliver(_observation(tmp_path, date="2026-08-02"), target)
    before = (target / README_NAME).stat().st_mtime_ns

    deliver(_observation(tmp_path, date="2026-08-03"), target)

    # A delivery that changes nothing should leave no diff, so the receiving
    # repository's history stays readable.
    assert (target / README_NAME).stat().st_mtime_ns == before


def test_an_already_delivered_date_is_refused_not_replaced(tmp_path: Path) -> None:
    target = tmp_path / "watch"
    deliver(_observation(tmp_path, new_count=2), target)

    with pytest.raises(FileExistsError):
        deliver(_observation(tmp_path, new_count=99), target)

    # The first record survives intact. Silently replacing it would make the
    # record unreliable in exactly the way this apparatus exists to prevent.
    assert json.loads((target / "2026-08-02.json").read_text(encoding="utf-8"))["new_count"] == 2


def test_force_replaces_only_when_asked(tmp_path: Path) -> None:
    target = tmp_path / "watch"
    deliver(_observation(tmp_path, new_count=2), target)
    deliver(_observation(tmp_path, new_count=99), target, force=True)

    assert json.loads((target / "2026-08-02.json").read_text(encoding="utf-8"))["new_count"] == 99


def test_the_cli_refuses_a_duplicate_with_exit_three(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    observation = _observation(tmp_path)
    target = tmp_path / "watch"

    assert main(["--observation", str(observation), "--into", str(target)]) == 0
    assert main(["--observation", str(observation), "--into", str(target)]) == 3
    assert "already delivered" in capsys.readouterr().err


def test_an_unusable_observation_is_a_dependency_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")

    assert main(["--observation", str(broken), "--into", str(tmp_path / "watch")]) == 2
    assert "cannot use" in capsys.readouterr().err


def test_delivery_holds_no_credential_and_opens_no_socket() -> None:
    # The whole reason the file placement lives in Python: everything that
    # needs a token stays in the workflow, where a reader expects it.
    source = Path(__file__).resolve().parents[3] / "scripts" / "deliver_observation.py"
    text = source.read_text(encoding="utf-8")
    for forbidden in ("urllib", "requests", "subprocess", "TOKEN", "secret"):
        assert forbidden not in text, forbidden
