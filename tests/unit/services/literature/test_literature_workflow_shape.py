"""task-packets/N1-T05.yaml AT8: the literature-channel workflow cannot land
on ``main`` and cannot run on a clock.

Cheap assertions over a YAML file, and every one of them is a real incident
rather than a hypothetical:

* a workflow that claimed "never lands on main" pushed to ``HEAD:$REF``, which
  ON main IS main (gold-classification.yml, caught while preparing a merge);
* a guard using ``git diff --quiet`` threw away a completed sixty-case
  measurement and reported success, because that command sees only TRACKED
  files and the artefacts were new;
* a third nightly routine would break two standing rules at once.

A new corpus consists ENTIRELY of new files, so the second bug would not be
harmless here — it would discard every run this workflow ever makes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_WORKFLOW = Path(__file__).resolve().parents[4] / ".github" / "workflows" / "literature-channel.yml"


@pytest.fixture(scope="module")
def workflow_text() -> str:
    return _WORKFLOW.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def executable_text() -> str:
    """The workflow with its comment lines removed.

    The header of that file EXPLAINS the two bugs this suite guards against,
    and quotes them verbatim to do so. A test that searched the raw text would
    read the explanation as the defect — punishing the file for documenting
    the thing it gets right. What must be asserted is what the workflow DOES.
    """
    return "\n".join(
        line
        for line in _WORKFLOW.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )


def test_the_workflow_exists() -> None:
    assert _WORKFLOW.is_file(), f"{_WORKFLOW} is missing"


def test_there_is_no_schedule_trigger(workflow_text: str) -> None:
    """AT8. "Keine dritte naechtliche Routine" and "kein Nightly, das dasselbe
    neu rechnet" — both would be broken by a cron here, and the second more
    expensively: a nightly draw would fill corpora/ with questions nobody
    asked, each costing a synthesis run.
    """
    for line in workflow_text.splitlines():
        assert not line.strip().startswith("schedule:"), (
            "literature-channel.yml must have no schedule trigger"
        )
    assert "cron:" not in workflow_text


def test_it_commits_on_a_branch_it_creates_never_on_the_current_ref(
    workflow_text: str, executable_text: str
) -> None:
    """AT8. `git checkout -b` before the commit is a stronger bolt than a
    ref-name check: there is no code path on which the push target could be
    the branch the run started on, whatever that branch is.
    """
    assert 'git checkout -b "$branch"' in workflow_text
    assert 'branch="literature/$batch"' in workflow_text
    assert "HEAD:main" not in executable_text
    assert "HEAD:${GITHUB_REF_NAME}" not in executable_text


def test_it_stages_before_it_asks_whether_anything_changed(executable_text: str) -> None:
    """AT8. The order is the whole fix: `git add` first, then
    `git diff --cached`. Reversed, the guard sees no untracked files, reports
    "nothing changed", and discards a completed run.
    """
    add_index = executable_text.index("git add ")
    diff_index = executable_text.index("git diff --cached --quiet")
    assert add_index < diff_index, "the corpus must be staged before the guard asks"
    # The broken form must not appear anywhere in this file.
    assert "git diff --quiet" not in executable_text


def test_it_refuses_without_a_key_before_touching_the_network(executable_text: str) -> None:
    """A missing secret costs nobody an arXiv fetch: the key check sits above
    the draw and the anchor.
    """
    key_check = executable_text.index("GEMINI_API_KEY is not set")
    fetch = executable_text.index("fetch_source_content.py")
    assert key_check < fetch


def test_the_committer_is_a_machine_identity_at_an_invalid_address(
    workflow_text: str,
) -> None:
    """Standing rule of the repository: never credit a real person or a
    product for a machine's run.
    """
    assert 'git config user.email "literature@meridian-runtime.invalid"' in workflow_text


def test_the_error_rate_travels_with_the_pull_request(workflow_text: str) -> None:
    """The number a reader needs in order to weigh the corpus is in the
    commit message and in the pull-request body, not only in a design note.
    """
    for marker in ("0.5439", "0.4211", "0.5263"):
        assert marker in workflow_text, marker
