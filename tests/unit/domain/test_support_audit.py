"""Unit tests for ``mrr.domain.support_audit`` (task-packets/N2-T03b.yaml,
unit tier). DB-free, no-network, no-model — every input here is a small,
hand-built excerpt string, never a fixture read from disk (the REAL
committed claim manifest and content snapshot are exercised separately, at
the contract tier, in tests/contract/test_support_audit_report.py).
"""

from __future__ import annotations

import pytest
from mrr.domain.support_audit import (
    DEFAULT_QUOTATION_SIMILARITY_THRESHOLD,
    IntegrityGateError,
    build_exclusion_verdict,
    check_anchor,
    check_and_gate,
    evaluate_figure_claim,
    evaluate_quotation_claim,
    find_numerals,
    find_term_occurrences,
)

_OK_HASH = "sha256:" + "a" * 64
_OTHER_HASH = "sha256:" + "b" * 64


# ---------------------------------------------------------------------------
# The fail-closed hash gate (mirrors, does not reuse, R2-T01's OR N2-T02b's).
# ---------------------------------------------------------------------------


def test_check_anchor_ok_when_hashes_are_exactly_equal() -> None:
    result = check_anchor("claims_manifest", "claims.manifest.json", _OK_HASH, _OK_HASH)
    assert result.status == "anchor_ok"


def test_check_anchor_mismatch_when_hashes_differ() -> None:
    result = check_anchor("claims_manifest", "claims.manifest.json", _OK_HASH, _OTHER_HASH)
    assert result.status == "anchor_mismatch"


def test_check_and_gate_does_not_raise_when_both_inputs_match() -> None:
    results = [
        check_anchor("claims_manifest", "a.json", _OK_HASH, _OK_HASH),
        check_anchor("content_snapshot", "b.json", _OK_HASH, _OK_HASH),
    ]
    check_and_gate(results)  # must not raise


def test_check_and_gate_raises_integrity_gate_error_on_a_mismatch() -> None:
    results = [check_anchor("content_snapshot", "b.json", _OK_HASH, _OTHER_HASH)]
    with pytest.raises(IntegrityGateError) as excinfo:
        check_and_gate(results)
    assert excinfo.value.role == "content_snapshot"
    assert excinfo.value.path == "b.json"
    assert excinfo.value.declared_sha256 == _OK_HASH
    assert excinfo.value.actual_sha256 == _OTHER_HASH


def test_check_and_gate_names_the_first_mismatch_in_role_sorted_order() -> None:
    """ "claims_manifest" sorts before "content_snapshot" — the gate reports
    the claims_manifest mismatch first regardless of the caller's own
    argument order.
    """
    results = [
        check_anchor("content_snapshot", "b.json", _OK_HASH, _OTHER_HASH),
        check_anchor("claims_manifest", "a.json", _OK_HASH, _OTHER_HASH),
    ]
    with pytest.raises(IntegrityGateError) as excinfo:
        check_and_gate(results)
    assert excinfo.value.role == "claims_manifest"


def test_check_and_gate_supports_zero_results() -> None:
    check_and_gate([])  # must not raise


# ---------------------------------------------------------------------------
# find_numerals — the exact merge/split rule this packet's own false-support
# story depends on (see the module docstring).
# ---------------------------------------------------------------------------


class TestFindNumerals:
    def test_plain_integer(self) -> None:
        occurrences = find_numerals("Kosmos runs for up to 12 hours.")
        values = [occurrence.value for occurrence in occurrences]
        assert "12" in values

    def test_decimal_point_is_kept(self) -> None:
        occurrences = find_numerals("79.4% of statements were accurate.")
        values = [occurrence.value for occurrence in occurrences]
        assert values == ["79.4"]

    def test_thousands_comma_group_of_three_digits_merges(self) -> None:
        occurrences = find_numerals("an average of 42,000 lines of code")
        values = [occurrence.value for occurrence in occurrences]
        assert values == ["42000"]

    def test_chained_thousands_groups_merge(self) -> None:
        occurrences = find_numerals("a population of 1,234,567 people")
        values = [occurrence.value for occurrence in occurrences]
        assert values == ["1234567"]

    def test_comma_not_followed_by_three_digits_splits_into_two_numbers(self) -> None:
        """The exact JATS reference-marker shape this packet's honesty
        story depends on: "6,7" is TWO standalone numbers, "6" and "7" —
        never merged into one value. Confirmed at the N2-T03 derivation: a
        bare digit search for "6" DID match inside "6,7", which is only
        possible if "6" survives as its own standalone numeral here.
        """
        occurrences = find_numerals("wider scientific exploration 6,7 . Both settings")
        values = [occurrence.value for occurrence in occurrences]
        assert values == ["6", "7"]

    def test_en_dash_range_marker_splits_into_two_numbers(self) -> None:
        occurrences = find_numerals("modern foundation models 3–5 within a complex")
        values = [occurrence.value for occurrence in occurrences]
        assert values == ["3", "5"]

    def test_sentence_boundary_period_does_not_merge_across_a_space(self) -> None:
        occurrences = find_numerals("... in 2020. 42% of experiments failed")
        values = [occurrence.value for occurrence in occurrences]
        assert values == ["2020", "42"]

    def test_no_numerals_in_text_without_digits(self) -> None:
        assert find_numerals("no digits here at all") == ()

    def test_occurrence_spans_are_correct(self) -> None:
        text = "prefix 42,000 suffix"
        occurrences = find_numerals(text)
        assert len(occurrences) == 1
        occurrence = occurrences[0]
        assert text[occurrence.start : occurrence.end] == "42,000"

    def test_is_pure_and_deterministic(self) -> None:
        text = "12 hours, 200 rollouts, 42,000 lines, 1,500 papers, 79.4% accurate"
        assert find_numerals(text) == find_numerals(text)


# ---------------------------------------------------------------------------
# find_term_occurrences — case-insensitive stem matching.
# ---------------------------------------------------------------------------


class TestFindTermOccurrences:
    def test_case_insensitive_match(self) -> None:
        occurrences = find_term_occurrences("accurat", "79.4% of statements were ACCURATE.")
        assert len(occurrences) == 1

    def test_stem_matches_the_word_it_is_a_prefix_of(self) -> None:
        assert len(find_term_occurrences("accurat", "accurate")) == 1
        assert len(find_term_occurrences("integrity", "integrity problem rate")) == 1

    def test_multiple_occurrences_are_all_found(self) -> None:
        occurrences = find_term_occurrences("run", "a run, another run, and a third run")
        assert len(occurrences) == 3

    def test_no_occurrence_returns_empty_tuple(self) -> None:
        assert find_term_occurrences("workshop", "no mention of that word") == ()

    def test_empty_term_returns_empty_tuple(self) -> None:
        assert find_term_occurrences("", "any text") == ()


# ---------------------------------------------------------------------------
# evaluate_figure_claim.
# ---------------------------------------------------------------------------


class TestEvaluateFigureClaim:
    def test_excerpt_none_is_absent(self) -> None:
        verdict = evaluate_figure_claim(
            claim_id="c1",
            citation_id="cit1",
            tokens=("12",),
            anchor_terms=("hour",),
            anchor_window_chars=60,
            excerpt_text=None,
        )
        assert verdict.status == "figure_absent_from_checked_excerpt"
        assert verdict.matched_windows == ()

    def test_single_token_supported_when_anchor_is_close(self) -> None:
        verdict = evaluate_figure_claim(
            claim_id="c1",
            citation_id="cit1",
            tokens=("12",),
            anchor_terms=("hour",),
            anchor_window_chars=60,
            excerpt_text="Kosmos runs for up to 12 hours performing cycles.",
        )
        assert verdict.status == "figure_supported_in_excerpt"
        assert len(verdict.matched_windows) == 1
        assert verdict.matched_windows[0].token == "12"
        assert "12 hours" in verdict.matched_windows[0].window_text

    def test_absent_when_token_never_occurs(self) -> None:
        verdict = evaluate_figure_claim(
            claim_id="c1",
            citation_id="cit1",
            tokens=("999",),
            anchor_terms=("hour",),
            anchor_window_chars=60,
            excerpt_text="Kosmos runs for up to 12 hours.",
        )
        assert verdict.status == "figure_absent_from_checked_excerpt"
        assert verdict.matched_windows == ()

    def test_absent_when_token_occurs_but_anchor_too_far(self) -> None:
        far_text = "12 " + ("filler word " * 20) + "hours mentioned way later"
        verdict = evaluate_figure_claim(
            claim_id="c1",
            citation_id="cit1",
            tokens=("12",),
            anchor_terms=("hour",),
            anchor_window_chars=10,
            excerpt_text=far_text,
        )
        assert verdict.status == "figure_absent_from_checked_excerpt"

    def test_absent_when_anchor_term_never_occurs_anywhere(self) -> None:
        verdict = evaluate_figure_claim(
            claim_id="c1",
            citation_id="cit1",
            tokens=("12",),
            anchor_terms=("nonexistentterm",),
            anchor_window_chars=60,
            excerpt_text="Kosmos runs for up to 12 hours.",
        )
        assert verdict.status == "figure_absent_from_checked_excerpt"

    def test_multi_token_claim_requires_every_token_to_qualify(self) -> None:
        """ "40" and "80" must BOTH have a qualifying occurrence near
        "citation"/"accuracy" — mirrors the real deeptrace-citation-accuracy
        claim.
        """
        excerpt = "with citation accuracy ranging from 40--80% across systems."
        verdict = evaluate_figure_claim(
            claim_id="deeptrace-citation-accuracy",
            citation_id="deeptrace",
            tokens=("40", "80"),
            anchor_terms=("citation", "accuracy"),
            anchor_window_chars=60,
            excerpt_text=excerpt,
        )
        assert verdict.status == "figure_supported_in_excerpt"
        assert {window.token for window in verdict.matched_windows} == {"40", "80"}

    def test_multi_token_claim_is_absent_if_only_one_token_qualifies(self) -> None:
        excerpt = "28 research papers were manually reviewed for weaknesses."
        verdict = evaluate_figure_claim(
            claim_id="zhu-experimental-weakness-share",
            citation_id="zhu",
            tokens=("100", "28"),
            anchor_terms=("paper",),
            anchor_window_chars=60,
            excerpt_text=excerpt,
        )
        # "28" qualifies (near "paper"), but "100" never occurs at all.
        assert verdict.status == "figure_absent_from_checked_excerpt"
        assert verdict.matched_windows == ()

    def test_reference_marker_false_support_is_prevented_by_anchor_terms(self) -> None:
        """The exact false-support risk task-packets/N2-T03b.yaml's
        reviewer_resolution names: a bare digit "6" matches inside a JATS
        reference marker "6,7" that has NOTHING to do with "stage" — the
        anchor-term requirement must refuse to certify support here.
        """
        excerpt = (
            "leverages agentic search for wider scientific exploration 6,7 . Both "
            "settings produce diverse ideas."
        )
        verdict = evaluate_figure_claim(
            claim_id="sakana-six-stages",
            citation_id="sakana-nature",
            tokens=("6",),
            anchor_terms=("stage",),
            anchor_window_chars=60,
            excerpt_text=excerpt,
        )
        assert verdict.status == "figure_absent_from_checked_excerpt"

    def test_known_coincidental_neighbourhood_still_resolves_supported(self) -> None:
        """The documented, accepted limitation from claims.manifest.json's
        own anchor_window_note: at window=60, "agent-laboratory-stages"
        resolves through a coincidental enumeration marker "(3) Human
        involvement ... at each stage", not the abstract's real "three
        stages" prose. The verdict is right (supported); the rendered
        window makes the coincidence visible to a human reader — which is
        exactly why the window is rendered, not just the status.
        """
        excerpt = (
            "achieve state-of-the-art performance compared to existing methods; "
            "(3) Human involvement, providing feedback at each stage, significantly "
            "improves the overall quality of research"
        )
        verdict = evaluate_figure_claim(
            claim_id="agent-laboratory-stages",
            citation_id="agent-laboratory",
            tokens=("3",),
            anchor_terms=("stage",),
            anchor_window_chars=60,
            excerpt_text=excerpt,
        )
        assert verdict.status == "figure_supported_in_excerpt"
        assert "stage" in verdict.matched_windows[0].window_text

    def test_no_status_other_than_the_two_closed_values_is_ever_emitted(self) -> None:
        for excerpt_text in (None, "no numbers here", "12 hours, all near hour"):
            verdict = evaluate_figure_claim(
                claim_id="c1",
                citation_id="cit1",
                tokens=("12",),
                anchor_terms=("hour",),
                anchor_window_chars=60,
                excerpt_text=excerpt_text,
            )
            assert verdict.status in (
                "figure_supported_in_excerpt",
                "figure_absent_from_checked_excerpt",
            )

    def test_is_deterministic(self) -> None:
        kwargs: dict[str, object] = {
            "claim_id": "c1",
            "citation_id": "cit1",
            "tokens": ("12", "200"),
            "anchor_terms": ("hour",),
            "anchor_window_chars": 60,
            "excerpt_text": "Kosmos runs for up to 12 hours over 200 rollouts.",
        }
        assert evaluate_figure_claim(**kwargs) == evaluate_figure_claim(**kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# evaluate_quotation_claim.
# ---------------------------------------------------------------------------


class TestEvaluateQuotationClaim:
    def test_excerpt_none_is_absent(self) -> None:
        verdict = evaluate_quotation_claim(
            claim_id="q1", citation_id="cit1", quote_text="anything", excerpt_text=None
        )
        assert verdict.status == "quotation_absent_from_checked_excerpt"
        assert verdict.matched_text is None

    def test_exact_substring_is_verbatim(self) -> None:
        verdict = evaluate_quotation_claim(
            claim_id="q1",
            citation_id="cit1",
            quote_text="Contradiction Transparency",
            excerpt_text=(
                "... testable via provenance coverage, contradiction transparency, "
                "and audit effort."
            ),
        )
        assert verdict.status == "quotation_verbatim"
        assert verdict.matched_text == "contradiction transparency"

    def test_case_and_whitespace_insensitive_verbatim_match(self) -> None:
        verdict = evaluate_quotation_claim(
            claim_id="q1",
            citation_id="cit1",
            quote_text="we   MANUALLY filtered\nthe outputs",
            excerpt_text="the team we manually filtered the outputs before publishing",
        )
        assert verdict.status == "quotation_verbatim"

    def test_unrelated_text_is_absent_not_altered(self) -> None:
        verdict = evaluate_quotation_claim(
            claim_id="q1",
            citation_id="cit1",
            quote_text="we manually filtered the most promising outputs",
            excerpt_text=(
                "The automation of science is a long-standing ambition in artificial "
                "intelligence research. The workshop had an acceptance rate of 70%."
            ),
        )
        assert verdict.status == "quotation_absent_from_checked_excerpt"
        assert verdict.matched_text is None

    def test_near_paraphrase_at_or_above_threshold_is_altered(self) -> None:
        verdict = evaluate_quotation_claim(
            claim_id="q1",
            citation_id="cit1",
            quote_text="the inference chain exists only as transient activation patterns",
            excerpt_text=(
                "our finding is that the inference chain exists only as transient "
                "activation processes in the model"
            ),
            similarity_threshold=0.75,
        )
        assert verdict.status == "quotation_altered"
        assert verdict.matched_text is not None

    def test_default_threshold_constant_is_the_one_actually_used_by_default(self) -> None:
        assert DEFAULT_QUOTATION_SIMILARITY_THRESHOLD == 0.75

    def test_higher_threshold_can_turn_an_altered_case_into_absent(self) -> None:
        quote = "the inference chain exists only as transient activation patterns"
        excerpt = "the inference chain exists only as transient activation processes"
        altered = evaluate_quotation_claim(
            claim_id="q1",
            citation_id="cit1",
            quote_text=quote,
            excerpt_text=excerpt,
            similarity_threshold=0.5,
        )
        absent = evaluate_quotation_claim(
            claim_id="q1",
            citation_id="cit1",
            quote_text=quote,
            excerpt_text=excerpt,
            similarity_threshold=0.999,
        )
        assert altered.status == "quotation_altered"
        assert absent.status == "quotation_absent_from_checked_excerpt"

    def test_is_deterministic(self) -> None:
        kwargs: dict[str, object] = {
            "claim_id": "q1",
            "citation_id": "cit1",
            "quote_text": "Contradiction Transparency",
            "excerpt_text": "testable via contradiction transparency and audit effort",
        }
        assert evaluate_quotation_claim(**kwargs) == evaluate_quotation_claim(**kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# build_exclusion_verdict.
# ---------------------------------------------------------------------------


def test_build_exclusion_verdict_carries_the_reason_verbatim() -> None:
    verdict = build_exclusion_verdict(
        claim_id="agent-laboratory-neurips-scores",
        citation_id="agent-laboratory",
        exclusion_reason="The record itself marks this claim REFUTED.",
    )
    assert verdict.status == "claim_excluded"
    assert verdict.exclusion_reason == "The record itself marks this claim REFUTED."


def test_exclusion_status_is_the_single_closed_value() -> None:
    verdict = build_exclusion_verdict(
        claim_id="c1", citation_id="cit1", exclusion_reason="any reason"
    )
    assert verdict.status == "claim_excluded"
