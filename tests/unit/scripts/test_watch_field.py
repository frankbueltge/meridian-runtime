"""The field watch, tested offline: the egress gate, the fail-closed rule, and
the promise that silence means something.

No network in any test here. The one property that matters most — a failed
sweep must never render as "nothing new" — is asserted directly, because it is
the difference between a routine whose silence is information and one whose
silence is noise.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.watch_field import (
    ALLOWED_HOST,
    EgressRefusedError,
    Entry,
    SearchFailedError,
    _check_allowlisted,
    main,
    parse_atom,
    passes_inclusion,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
WATCH_DIR = REPO_ROOT / "corpora" / "field-watch"

_ATOM_ONE = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2699.12345v1</id>
    <title>An  agentic   pipeline   with   independent verification</title>
    <published>2699-01-02T00:00:00Z</published>
    <summary>A generated  result is checked by a separate component.</summary>
  </entry>
</feed>
"""


# --- The egress gate ---------------------------------------------------------


def test_only_the_one_allowlisted_host_over_https_is_permitted() -> None:
    _check_allowlisted(f"https://{ALLOWED_HOST}/api/query?search_query=x")

    for refused in (
        f"http://{ALLOWED_HOST}/api/query",  # not https
        "https://arxiv.org/api/query",  # a different host
        "https://export.arxiv.org.evil.example/api/query",  # a lookalike SUFFIX
        "https://evil.example/export.arxiv.org",  # the name only in the path
    ):
        with pytest.raises(EgressRefusedError):
            _check_allowlisted(refused)


def test_the_host_check_is_on_the_parsed_hostname_not_a_substring() -> None:
    # The lookalike above is the whole reason: a substring check on the raw URL
    # would wave "export.arxiv.org.evil.example" straight through.
    with pytest.raises(EgressRefusedError, match="is not"):
        _check_allowlisted("https://not-export.arxiv.org.example/api/query")


# --- Parsing, and the XML guard ---------------------------------------------


def test_entries_parse_with_whitespace_normalised() -> None:
    (entry,) = parse_atom(_ATOM_ONE.encode())
    assert entry.arxiv == "2699.12345"
    assert entry.title == "An agentic pipeline with independent verification"
    assert entry.published == "2699-01-02"
    assert entry.abstract == "A generated result is checked by a separate component."


def test_a_doctype_is_refused_before_the_parser_sees_it() -> None:
    payload = b'<?xml version="1.0"?><!DOCTYPE feed [<!ENTITY x "y">]><feed/>'
    with pytest.raises(SearchFailedError, match="DOCTYPE or ENTITY"):
        parse_atom(payload)


def test_billion_laughs_is_refused_without_reaching_the_parser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The proof the two `nosec` suppressions in watch_field.py name.

    Refusing a bomb is not the same claim as never handing it to the parser,
    and only the second one justifies keeping stdlib ElementTree. So: make
    ``ET.fromstring`` fatal, then feed it a billion-laughs payload. If the
    guard is ever removed this test fails, and the suppressions lose the
    reason they cite.
    """

    def must_not_be_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("ET.fromstring was reached with a DOCTYPE payload")

    monkeypatch.setattr("scripts.watch_field.ET.fromstring", must_not_be_called)

    bomb = (
        b'<?xml version="1.0"?>\n<!DOCTYPE lolz [\n'
        b'  <!ENTITY lol "lol">\n'
        b'  <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">\n'
        b"]>\n<lolz>&lol2;</lolz>"
    )
    with pytest.raises(SearchFailedError, match="DOCTYPE or ENTITY"):
        parse_atom(bomb)


def test_unparseable_xml_is_a_search_failure_not_a_crash() -> None:
    with pytest.raises(SearchFailedError, match="not parseable XML"):
        parse_atom(b"<feed><entry>")


# --- The frozen inclusion filter --------------------------------------------


def _inclusion() -> dict[str, Any]:
    searches: dict[str, Any] = json.loads(
        (WATCH_DIR / "searches.v1.json").read_text(encoding="utf-8")
    )
    inclusion: dict[str, Any] = searches["inclusion"]
    return inclusion


def test_inclusion_needs_both_checking_and_automation() -> None:
    inclusion = _inclusion()
    long = " padding." * 60

    assert passes_inclusion("t", "an autonomous agent whose output is verified" + long, inclusion)
    # Checking without automation, and automation without checking, both out.
    assert not passes_inclusion("t", "a verified proof of a theorem" + long, inclusion)
    assert not passes_inclusion("t", "an autonomous vehicle controller" + long, inclusion)


def test_a_short_abstract_is_excluded_regardless_of_wording() -> None:
    assert not passes_inclusion("t", "an autonomous agent, verified.", _inclusion())


# --- Fail-closed: the property the whole routine rests on -------------------


def test_a_failed_search_exits_nonzero_and_never_reports_nothing_new(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(query: str, max_results: int) -> tuple[Entry, ...]:
        raise SearchFailedError(f"{query}: simulated outage")

    monkeypatch.setattr("scripts.watch_field._search", boom)
    monkeypatch.setattr("scripts.watch_field.SECONDS_BETWEEN_REQUESTS", 0)

    exit_code = main(["--today", "2699-01-01", "--dry-run"])
    captured = capsys.readouterr()

    assert exit_code == 2
    # The whole point: a broken sweep must not be indistinguishable from a
    # quiet field.
    assert "nothing new" not in captured.out
    assert captured.out.strip() == ""
    assert "Refusing to report a partial sweep" in captured.err


def test_an_empty_sweep_says_so_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setattr("scripts.watch_field._search", lambda q, n: ())
    monkeypatch.setattr("scripts.watch_field.SECONDS_BETWEEN_REQUESTS", 0)
    monkeypatch.setattr("scripts.watch_field.WATCH_DIR", WATCH_DIR)

    exit_code = main(["--today", "2699-01-01"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["new"] == 0
    assert payload["searches_completed"] == 14
    assert "sweep completed" in payload["note"]
    assert not (WATCH_DIR / "observations" / "2699-01-01.json").exists()


def test_an_already_seen_id_is_not_reported_twice(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    register = json.loads((WATCH_DIR / "seen.json").read_text(encoding="utf-8"))
    known = register["ids"][0]
    entry = Entry(arxiv=known, title="t", published="2026-01-01", abstract="x" * 500)

    monkeypatch.setattr("scripts.watch_field._search", lambda q, n: (entry,))
    monkeypatch.setattr("scripts.watch_field.SECONDS_BETWEEN_REQUESTS", 0)

    assert main(["--today", "2699-01-01", "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["new"] == 0


def test_a_new_id_is_reported_with_its_own_disclaimer(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    entry = Entry(
        arxiv="2699.99999",
        title="An autonomous research agent with an independent verifier",
        published="2699-01-02",
        abstract="An autonomous agent whose outputs are verified by a separate component." * 8,
    )
    monkeypatch.setattr("scripts.watch_field._search", lambda q, n: (entry,))
    monkeypatch.setattr("scripts.watch_field.SECONDS_BETWEEN_REQUESTS", 0)

    assert main(["--today", "2699-01-01", "--dry-run"]) == 0
    observation = json.loads(capsys.readouterr().out)

    assert observation["new_count"] == 1
    assert observation["new"][0]["arxiv"] == "2699.99999"
    # An observation must not read as a reading. The disclaimer travels with
    # the data, not in a commit message somebody will not open.
    assert "not a reading" in observation["_note"]
    assert "nothing here implies a change" in observation["_note"]


# --- The frozen searches ------------------------------------------------------


def test_the_register_is_seeded_so_the_first_night_is_not_all_of_history() -> None:
    register = json.loads((WATCH_DIR / "seen.json").read_text(encoding="utf-8"))
    assert len(register["ids"]) == 353
    assert register["seeded_from"].endswith("candidate-pool.v1.json")


def test_the_searches_are_the_ones_the_gold_standard_was_drawn_with() -> None:
    # Kept identical on purpose: a watch and a benchmark that look at different
    # literatures cannot be compared to each other.
    searches = json.loads((WATCH_DIR / "searches.v1.json").read_text(encoding="utf-8"))
    assert len(searches["queries"]) == 14
    assert searches["host"] == ALLOWED_HOST


def test_a_paper_older_than_the_watch_start_is_backlog_not_news(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The bug this floor fixes was real and was caught by running the thing:
    # the register was seeded from a relevance-ranked pool while the sweep
    # sorts by submission date, so the first live run reported 260 older
    # papers as news. Absence from the register is not enough — a watch
    # reports what APPEARED since it began watching.
    register = json.loads((WATCH_DIR / "seen.json").read_text(encoding="utf-8"))
    watch_from = register["watch_from"]

    old = Entry(
        arxiv="2401.00001",
        title="An autonomous research agent that verifies its own output",
        published="2025-11-14",
        abstract="An autonomous agent whose outputs are verified separately." * 12,
    )
    monkeypatch.setattr("scripts.watch_field._search", lambda q, n: (old,))
    monkeypatch.setattr("scripts.watch_field.SECONDS_BETWEEN_REQUESTS", 0)

    assert old.arxiv not in set(register["ids"])
    assert old.published < watch_from
    assert main(["--today", "2699-01-01", "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["new"] == 0


def test_a_paper_from_the_watch_start_date_itself_counts_as_news(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The boundary is inclusive: the day the watch begins is watched.
    watch_from = json.loads((WATCH_DIR / "seen.json").read_text(encoding="utf-8"))["watch_from"]
    fresh = Entry(
        arxiv="2699.00002",
        title="An autonomous research agent with an independent verifier",
        published=watch_from,
        abstract="An autonomous agent whose outputs are verified separately." * 12,
    )
    monkeypatch.setattr("scripts.watch_field._search", lambda q, n: (fresh,))
    monkeypatch.setattr("scripts.watch_field.SECONDS_BETWEEN_REQUESTS", 0)

    assert main(["--today", "2699-01-01", "--dry-run"]) == 0
    observation = json.loads(capsys.readouterr().out)
    assert observation["new_count"] == 1
    assert observation["watch_from"] == watch_from
