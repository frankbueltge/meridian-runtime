"""The MB-CLS commission stays intact and stays unanswered.

The commission is what Meridian sends out and what another practice sends
back filled in. Three things have to hold, and none of them is obvious enough
to leave to care:

1. Its ``content_hash`` verifies under the repository's own hashing policy —
   ``mrr federation envelope sign`` refuses a payload without one, so a
   commission whose hash has drifted cannot be sent at all.
2. It carries no answers. Meridian is the practice being MEASURED; a case
   here that already had a relation on it would make the whole exercise a
   confirmation.
3. Every excerpt still hashes to the value the fetch recorded, and every case
   is one of the sixty actually drawn.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from mrr.crypto.canonical import canonicalize
from mrr.crypto.hashing import content_hash
from mrr.domain.hashing_policy import prepare_for_hash

REPO_ROOT = Path(__file__).resolve().parents[2]
BASE = REPO_ROOT / "corpora" / "gold-classification"
COMMISSION = BASE / "commission.v2.json"
CRITERIA = REPO_ROOT / "benchmarks" / "meridianbench" / "fixtures" / "mb-cls-criteria.v2.json"


def _commission() -> dict[str, Any]:
    document: dict[str, Any] = json.loads(COMMISSION.read_text(encoding="utf-8"))
    return document


def test_the_commission_content_hash_verifies() -> None:
    document = _commission()
    assert content_hash(canonicalize(prepare_for_hash(document))) == document["content_hash"]


def test_the_commission_pins_the_criteria_it_was_issued_under() -> None:
    document = _commission()
    expected = "sha256:" + hashlib.sha256(CRITERIA.read_bytes()).hexdigest()
    assert document["criteria_lock_content_hash"] == expected
    assert document["criteria_version"] == json.loads(CRITERIA.read_text())["criteria_version"]


def test_the_commission_carries_no_answers() -> None:
    # The whole point. Meridian draws the cases and states the criteria; it
    # does not get to say what the right answer is, because it is the thing
    # being measured.
    for case in _commission()["cases"]:
        assert "expected_relation" not in case
        assert "expected_rationale" not in case
        assert "gold" not in json.dumps(case).lower()


def test_every_case_carries_a_verifiable_excerpt() -> None:
    for case in _commission()["cases"]:
        digest = "sha256:" + hashlib.sha256(case["excerpt"].encode("utf-8")).hexdigest()
        assert digest == case["excerpt_sha256"], case["case_id"]
        assert len(case["excerpt"]) >= 400


def test_the_cases_are_exactly_the_sixty_that_were_drawn() -> None:
    pool = json.loads((BASE / "candidate-pool.v1.json").read_text(encoding="utf-8"))
    drawn = {c["arxiv"] for c in pool["candidates"] if c["drawn"]}
    cases = {case["source_identifiers"]["repository_id"] for case in _commission()["cases"]}
    assert len(drawn) == 60
    assert cases == drawn


def test_no_case_comes_from_a_corpus_that_was_already_run() -> None:
    # The locked criteria's inclusion rule: measuring on cases the practice
    # already curated would be measuring on its own training set.
    already_run: set[str] = set()
    for name in ("model-collapse", "e2e-claims"):
        for path in (REPO_ROOT / "corpora" / name).rglob("*.json"):
            already_run.update(
                part
                for part in path.read_text(encoding="utf-8").split('"')
                if part.count(".") == 1 and part.replace(".", "").isdigit()
            )
    for case in _commission()["cases"]:
        assert case["source_identifiers"]["repository_id"] not in already_run


# --- The re-stamped set: only the header moved -------------------------------

RESTAMPED = BASE / "mb-cls-ulysses-v1-restamped.json"
RETURNED = BASE / "mb-cls-ulysses-v1.returned.json"


def test_not_one_label_was_touched_when_meridian_corrected_its_own_clock() -> None:
    """The assertion the whole correction rests on.

    Meridian's criteria v2 carried a lock instant two hours in the future of
    the act it licensed, so the order gate refused sixty honestly-made labels.
    The labelling practice declined to re-stamp — correctly: "the order gate
    that refuses the set, and the labels themselves" belong to the issuing
    side. So Meridian corrected its own header, and this test is what keeps
    that from becoming a licence to touch the labels too.
    """
    returned = json.loads(RETURNED.read_text(encoding="utf-8"))
    restamped = json.loads(RESTAMPED.read_text(encoding="utf-8"))

    assert restamped["cases"] == returned["cases"]
    assert restamped["labelled_at"] == returned["labelled_at"] == "2026-08-01T20:52:00Z"
    assert restamped["notes"] == returned["notes"]

    # Exactly the criteria pointer moved, and nothing else.
    moved = {
        key for key in set(returned) | set(restamped) if returned.get(key) != restamped.get(key)
    }
    assert moved == {
        "set_id",
        "criteria_version",
        "criteria_lock_content_hash",
        "criteria_locked_at",
        "label_provenance",
        "header_corrected_by_meridian",
    }


def test_the_correction_is_attributed_to_meridian_not_to_the_labeller() -> None:
    restamped = json.loads(RESTAMPED.read_text(encoding="utf-8"))
    correction = restamped["header_corrected_by_meridian"]

    assert correction["corrected_by"] == "meridian (the issuing side)"
    assert correction["returned_set_sha256"].startswith("sha256:")
    # The labels stay the labelling practice's own, in the provenance a reader
    # actually reads.
    assert restamped["label_provenance"]["producing_practice"] == "ulysses"
    assert "the labels are ulysses' own and unmodified" in (
        restamped["label_provenance"]["account"].replace("’", "'").lower()
    )


def test_the_returned_set_is_committed_verbatim_beside_the_correction() -> None:
    # A correction is only auditable while the thing corrected is readable.
    returned = json.loads(RETURNED.read_text(encoding="utf-8"))
    restamped = json.loads(RESTAMPED.read_text(encoding="utf-8"))
    digest = "sha256:" + hashlib.sha256(RETURNED.read_bytes()).hexdigest()

    assert returned["set_id"] == "mb-cls-ulysses-v1"
    assert digest == restamped["header_corrected_by_meridian"]["returned_set_sha256"]
