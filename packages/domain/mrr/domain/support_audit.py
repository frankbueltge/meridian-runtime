"""Pure, hand-rolled, no-new-dependency, MODEL-FREE support-audit core
(task-packets/N2-T03b.yaml). No file I/O, no network, no database — every
function here takes already-parsed/already-read values (a claim's already-
transcribed tokens/quotation text, an already-read excerpt string, an
already-computed hash) and returns a plain, typed result. Mirrors
``mrr.domain.anchoring_integrity``'s own "takes already-loaded values"
precedent: reading the committed batch descriptor and hashing its declared
inputs is the SERVICE's job (``mrr.services.support_audit.service
.SupportAuditService``), never this module's.

--- What this module answers, and what it explicitly refuses to answer -----

For every hand-transcribed claim in ``corpora/research-records/
claims.manifest.json`` (a FIGURE or a verbatim QUOTATION), this module
decides whether the CHECKED EXCERPT — the ABSTRACT captured by N2-T03a, and
NOTHING ELSE — carries it. It never asks whether the source SUPPORTS the
claim in substance (that would require reading the full paper and judging
meaning, which is N2-T03c, deferred and blocked — see below) and it never
asks whether the excerpt CONTRADICTS the claim (not decidable against an
abstract; also deferred to N2-T03c). This is the N2-T03 analogue of N1's
"reliability, not validity", N2-T01's "existence, not support", N2-T02b's
"anchoring, not support", and R2-T01's "observation, not optimization".

--- NO MODEL (task-packets/N2-T03b.yaml reviewer_resolution (1)) -----------

Everything decidable here — a number occurs in the excerpt or it does not; a
quotation occurs verbatim, in altered words, or not at all — is decidable by
substring/token comparison alone. No ``ModelAdapter`` is imported, invoked,
or even importable from this module (there is no concrete implementation to
call anyway — see docs/design/2026-07-25-n2-t03-derivation.md's Korrektur 1).
The paraphrase-level question ("does the surrounding prose, in different
words, still support the claim?") is NOT answered here — it is named,
deferred, and explicitly declined as N2-T03c, on the evidence of the very
records this packet audits: LLM judges stay under 85% accuracy even with
checklists and a full execution trace (Record I, arXiv 2605.10246 Sec. 6),
and the roadmap's own accepted recommendation is "never as sole judge"
(kappa 0.19-0.51). Applying a model here would repeat the failure mode this
audit exists to catch.

--- Three closed sets, kept apart by TYPE and never unified (AGENTS.md
    prohibited shortcut: "collapsing distinct statuses into one generic
    outcome") --------------------------------------------------------------

:data:`FigureStatus` (``"figure_supported_in_excerpt"`` / ``"figure_absent_
from_checked_excerpt"``), :data:`QuotationStatus`
(``"quotation_verbatim"`` / ``"quotation_altered"`` /
``"quotation_absent_from_checked_excerpt"``), and :data:`ExclusionStatus`
(``"claim_excluded"``, exactly one value) never share a type or a
comparison. ``figure_absent_from_checked_excerpt`` and ``quotation_absent_
from_checked_excerpt`` are OBSERVATIONS, never violations — absence from an
abstract is the NORMAL case (measured at the N2-T03 derivation at roughly 28
percent numeric-token coverage across this corpus), not a defect. The only
VIOLATION status this module can ever emit is ``quotation_altered``: the
excerpt carries the passage in DIFFERENT WORDS, which is a genuine
discrepancy between what the record quotes and what the source actually
says. Collapsing an OBSERVATION into a violation bucket would report 18
false violations on the very first run over the real committed inputs — the
exact number this module's own report (``mrr.domain.support_audit_report``)
keeps strictly apart. ``claim_excluded`` is neither a hit nor a violation —
the manifest itself declares three claims unavailable to this check with a
named reason (a REFUTED claim, an attribution the manifest declines to
invent), and an exclusion must never read as a pass.

``figure_contradicted`` DOES NOT EXIST as a status anywhere in this module.
It is not decidable against an abstract (an abstract that says nothing about
a figure is not "the source disagrees" — it is silence), and inventing it
would be exactly the fabricated-precision failure this audit exists to
prevent (AGENTS.md rule 3: no domain behavior absent from the spec).

--- Anchor terms are mandatory: token presence alone is never a status ------

A bare numeric substring comparison over this exact corpus produced 3 FALSE
SUPPORTS out of 14 at the N2-T03 derivation: the digit ``6`` matched the
JATS reference marker ``6,7`` surviving in the Nature abstract after N2-T03a's
declared tag-stripping; ``1`` and ``3`` matched ``1,2`` and the enumeration
item ``(3) Human involvement``. Certifying support that does not exist is
the exact failure this audit exists to prevent, so :func:`evaluate_figure_
claim` NEVER emits ``figure_supported_in_excerpt`` from a bare digit match —
a token counts as HIT only when EVERY one of the claim's declared
``anchor_terms`` (case-insensitive substrings, authored from what the RECORD
claims the quantity IS — never from the source text itself, which would tune
the check towards confirming) has some occurrence within ``anchor_window_
chars`` character-distance of that SAME digit occurrence (see :func:`_char_
gap`: the number of characters strictly between the two spans' nearer edges,
0 if they overlap). ``anchor_window_chars`` is a caller-supplied parameter,
never a constant baked in here — the committed claim manifest declares 60,
chosen at the derivation from a sweep over 200/120/80/60/40/25 (identical
from 200 down to 60, first changing at 40).

--- Numbers are matched as DIGITS only, with thousands-grouping (never
    tolerance) recognised, decimal points always kept ------------------------

:func:`find_numerals` extracts every maximal numeral literal from an excerpt:
a run of decimal digits, optionally extended by EXACTLY ONE trailing decimal-
point group (``"79.4"``) and by any number of chained THREE-DIGIT comma
groups (``"42,000"`` -> the single value ``"42000"``, ``"1,234,567"`` ->
``"1234567"``) — English-abstract thousands separators, stripped per
AGENTS.md's "German decimal comma -> dot; strip thousands separators" rule
applied to whatever separator convention the CHECKED EXCERPT itself uses
(these excerpts are English-language abstracts, so the separator this
function actually strips is the comma). A comma NOT followed by exactly
three digits is deliberately NOT merged — it splits into two SEPARATE
numerals. This is not a simplification; it is required by the real data: the
false-support risk above is proof the reference behavior treats ``"6,7"`` as
TWO standalone numbers ``"6"`` and ``"7"`` (if it merged into one number
``"6.7"``/``"67"``, a bare search for token ``"6"`` could never have matched
there in the first place, and the false-support risk this module's own
anchor-term requirement defends against would not exist). No tolerance band
of any kind is applied to the resulting comparison — exact string equality
between a claim's declared token and a found numeral's normalised value,
never "close enough" (AGENTS.md rule 3; task-packets/N2-T03b.yaml
explicitly_not).

--- Quotations: verbatim first, then a bounded best-window similarity -------

:func:`evaluate_quotation_claim` normalises both the claim's quoted text and
the excerpt (lower-case, whitespace-collapsed) and checks for an exact
substring match FIRST (``quotation_verbatim`` — a hit). Only if that fails
does it slide a WORD-length window (matching the quote's own word count)
across the excerpt and take the single best ``difflib.SequenceMatcher``
ratio (stdlib only, no new dependency) — at or above
:data:`DEFAULT_QUOTATION_SIMILARITY_THRESHOLD` is ``quotation_altered`` (the
excerpt carries the passage in different words — a VIOLATION), below is
``quotation_absent_from_checked_excerpt`` (an OBSERVATION). UNLIKE the 60-
character anchor window, this specific threshold (0.75) has NO sweep behind
it in the derivation record — none of the four real quotation claims in the
committed manifest ever resolves to ``quotation_altered`` (the acceptance
oracle's own total for it is 0), so there is no real case to calibrate
against. It is a deliberately conservative, documented choice, verified at
build time against the real committed excerpts to sit with wide margin below
every genuinely-unrelated pairing in this corpus (the closest, aar-
inference-chain-transient against the AAR abstract's own unrelated "transient
constraints"/"unverifiable inference" phrasing, scores ~0.45 — well under
0.75) and to still catch an exact match's own trivial neighbours (an exact
match is caught earlier anyway, via the substring check, so this threshold
only ever governs genuine near-paraphrases). This is a builder judgement
call within the packet's remit, not a value handed down by the packet
itself, and is reported as such rather than presented as pre-registered.

--- The fail-closed hash gate (mirrors, does NOT reuse, R2-T01's OR
    N2-T02b's) -----------------------------------------------------------------

``mrr.domain.field_observation.check_anchor``/``BatchRole`` compare an
already-computed hash against a pinned anchor for exactly this shape — but
that module's ``BatchRole`` is a CLOSED two-value set (``"manifest"``,
``"snapshot"``) task-packets/R2-T01.yaml itself declares closed ("this batch
shape has no third input, and no caller of this module may invent one").
Widening another packet's closed ``Literal`` for convenience is exactly the
forbidden move N2-T02b already declined (task-packets/N2-T02b.yaml
derived_decisions (d)) — so this module mirrors the PATTERN with its own,
separately declared closed set, :data:`SupportBatchRole`
(``"claims_manifest"``, ``"content_snapshot"``), exactly as
``mrr.domain.anchoring_integrity`` mirrored the SAME pattern for its own
archive-dump shape rather than reusing ``field_observation``'s. :func:`check_
and_gate` raises :class:`IntegrityGateError` the moment ANY
:class:`AnchorCheckResult` is ``"anchor_mismatch"`` — naming the FIRST
mismatch in ``role``-sorted order — and NO claim may be evaluated when it
fires; whether :func:`evaluate_figure_claim`/:func:`evaluate_quotation_claim`
are ever called at all is entirely the SERVICE layer's decision, made only
after this gate has already passed clean.

--- Determinism (task-packets/N2-T03b.yaml invariant) -------------------------

No wall clock anywhere in this module. Every function that returns more than
one result sorts its own output explicitly (never a ``dict``/``set``
iteration order, never the caller's own argument order), so calling any of
them twice over equal inputs yields an identical sequence.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

#: Every maximal run of decimal digits — the raw building block
#: :func:`find_numerals` chains into full numeral literals below.
_DIGIT_RUN_RE = re.compile(r"\d+")

# ---------------------------------------------------------------------------
# Section 1: the fail-closed hash gate — mirrors
# mrr.domain.field_observation's/mrr.domain.anchoring_integrity's identical
# pattern, over THIS packet's own, separately declared closed set (see the
# module docstring's "fail-closed hash gate" section for why this is not a
# reuse of either).
# ---------------------------------------------------------------------------


class SupportAuditError(Exception):
    """Base class for every typed error this module raises."""


#: The closed set of exactly the two named inputs a committed support-batch
#: descriptor carries — this batch shape has no third input, and no caller
#: of this module may invent one (mirrors ``mrr.domain.field_observation
#: .BatchRole``'s identical closed-two-set discipline, declared fresh here
#: rather than imported/widened — see the module docstring).
SupportBatchRole = Literal["claims_manifest", "content_snapshot"]

#: Every declared role, in the fixed, stable order :func:`check_and_gate`
#: and :meth:`SupportBatch.inputs` sort by.
SUPPORT_BATCH_ROLES: tuple[SupportBatchRole, ...] = ("claims_manifest", "content_snapshot")


@dataclass(frozen=True, slots=True)
class BatchInputDeclaration:
    """One declared input from a committed support-batch descriptor: its
    role, its declared path (exactly as written in the descriptor —
    resolving it relative to the descriptor's own directory is the
    SERVICE's job, never this dataclass's), and its pinned sha256 anchor
    (already in ``"sha256:<hex>"`` form).
    """

    role: SupportBatchRole
    path: str
    declared_sha256: str


@dataclass(frozen=True, slots=True)
class SupportBatch:
    """The parsed shape of a committed support-batch descriptor (e.g.
    ``corpora/research-records/support-batch.v1.json``). Carries exactly the
    two named inputs the descriptor declares — ``claims_manifest`` and
    ``content_snapshot`` — as their own explicit fields, never a generic
    list a caller could reorder or extend silently.
    """

    schema_version: str
    batch_id: str
    audit_target: str
    claims_manifest: BatchInputDeclaration
    content_snapshot: BatchInputDeclaration

    def inputs(self) -> tuple[BatchInputDeclaration, ...]:
        """Both declared inputs, sorted by ``role`` — the ONE place this
        ordering is decided, so a caller iterating this always sees
        ``claims_manifest`` before ``content_snapshot`` regardless of how
        the two fields happen to be laid out on this dataclass.
        """
        return tuple(
            sorted((self.claims_manifest, self.content_snapshot), key=lambda item: item.role)
        )


#: The closed set of exactly two hash-comparison outcomes — never collapsed
#: into a bare ``bool`` a future caller could silently reinterpret.
AnchorStatus = Literal["anchor_ok", "anchor_mismatch"]


@dataclass(frozen=True, slots=True)
class AnchorCheckResult:
    """The named result of one integrity-anchor comparison. ``status`` is
    the definitive verdict; both hash strings are carried so a caller (or a
    rendered report) can show the disagreement without recomputing
    anything.
    """

    role: SupportBatchRole
    path: str
    declared_sha256: str
    actual_sha256: str
    status: AnchorStatus


def check_anchor(
    role: SupportBatchRole, path: str, declared_sha256: str, actual_sha256: str
) -> AnchorCheckResult:
    """Pure comparison of two already-computed hash strings — no file I/O,
    no normalisation guessed. ``"anchor_ok"`` iff the two strings are
    exactly equal, else ``"anchor_mismatch"``.
    """
    status: AnchorStatus = "anchor_ok" if declared_sha256 == actual_sha256 else "anchor_mismatch"
    return AnchorCheckResult(
        role=role,
        path=path,
        declared_sha256=declared_sha256,
        actual_sha256=actual_sha256,
        status=status,
    )


class IntegrityGateError(SupportAuditError):
    """Raised by :func:`check_and_gate` the moment ANY declared input's
    actual sha256 does not match its pinned anchor — a fail-closed refusal,
    raised BEFORE any claim is ever evaluated. Carries ``role``, ``path``,
    ``declared_sha256``, and ``actual_sha256`` so a caller can report exactly
    which input failed and why, without parsing the message string.
    """

    def __init__(
        self, role: SupportBatchRole, path: str, declared_sha256: str, actual_sha256: str
    ) -> None:
        self.role = role
        self.path = path
        self.declared_sha256 = declared_sha256
        self.actual_sha256 = actual_sha256
        super().__init__(
            f"integrity gate failed for input {role!r} at {path!r}: declared sha256 "
            f"{declared_sha256!r} does not match actual sha256 {actual_sha256!r}"
        )


def check_and_gate(results: Sequence[AnchorCheckResult]) -> None:
    """Raise :class:`IntegrityGateError` the moment ANY ``results`` entry is
    ``"anchor_mismatch"`` — naming the FIRST mismatch in ``role``-sorted
    order, never the caller's own argument order. Returns ``None`` (does
    nothing) when every result is ``"anchor_ok"``, however many results are
    given, including zero.
    """
    ordered = sorted(results, key=lambda result: result.role)
    for result in ordered:
        if result.status == "anchor_mismatch":
            raise IntegrityGateError(
                result.role, result.path, result.declared_sha256, result.actual_sha256
            )


# ---------------------------------------------------------------------------
# Section 2: the numeral tokenizer — pure, no I/O. See the module
# docstring's "Numbers are matched as DIGITS only" section for the exact,
# derivation-verified merge/split rule.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NumeralOccurrence:
    """One numeral literal found in an excerpt: its normalised value (a
    plain digit string, optionally carrying exactly one ``.`` decimal
    fraction — never a thousands-separator comma, which is always stripped),
    and its ``[start, end)`` character span in the ORIGINAL (unmodified)
    excerpt text.
    """

    value: str
    start: int
    end: int


def find_numerals(text: str) -> tuple[NumeralOccurrence, ...]:
    """Extract every maximal numeral literal from ``text``, left to right.
    Chained comma groups of EXACTLY three digits merge as thousands
    separators (``"42,000"`` -> ``"42000"``, span covering both groups); at
    most one trailing dot-group merges as a decimal fraction (``"79.4"`` ->
    ``"79.4"``, kept as written); any OTHER adjacency (a comma not followed
    by exactly three digits, a second dot, any non-digit/non-``,``/``.``
    separator, or whitespace) ends the current numeral and starts a new one.
    See the module docstring for why this exact rule — not a looser or
    stricter one — is required by the real committed corpus.
    """
    digit_runs = [
        (match.start(), match.end(), match.group()) for match in _DIGIT_RUN_RE.finditer(text)
    ]
    occurrences: list[NumeralOccurrence] = []
    i = 0
    while i < len(digit_runs):
        start, _end, first_group = digit_runs[i]
        parts = [first_group]
        j = i
        decimal_used = False
        while j + 1 < len(digit_runs):
            _this_start, this_end, _this_group = digit_runs[j]
            next_start, _next_end, next_group = digit_runs[j + 1]
            between = text[this_end:next_start]
            if between == "," and len(next_group) == 3:
                parts.append(next_group)
                j += 1
                continue
            if between == "." and not decimal_used:
                parts.append("." + next_group)
                decimal_used = True
                j += 1
                continue
            break
        end = digit_runs[j][1]
        occurrences.append(NumeralOccurrence(value="".join(parts), start=start, end=end))
        i = j + 1
    return tuple(occurrences)


@dataclass(frozen=True, slots=True)
class TermOccurrence:
    """One case-insensitive substring match of an anchor term (or a
    quotation) in an excerpt: its ``[start, end)`` character span in the
    ORIGINAL (unmodified) excerpt text.
    """

    start: int
    end: int


def find_term_occurrences(term: str, text: str) -> tuple[TermOccurrence, ...]:
    """Every case-insensitive occurrence of ``term`` in ``text`` — a plain,
    overlap-tolerant left-to-right substring scan (never a word-boundary
    regex: an anchor term such as ``"accurat"`` is deliberately authored as
    a STEM to match both "accurate" and "accuracy", per the claim
    manifest's own ``anchor_note``).
    """
    if not term:
        return ()
    lowered_text = text.lower()
    lowered_term = term.lower()
    occurrences: list[TermOccurrence] = []
    search_from = 0
    while True:
        index = lowered_text.find(lowered_term, search_from)
        if index == -1:
            break
        occurrences.append(TermOccurrence(start=index, end=index + len(lowered_term)))
        search_from = index + 1
    return tuple(occurrences)


def _char_gap(a_start: int, a_end: int, b_start: int, b_end: int) -> int:
    """The character distance between two ``[start, end)`` spans: 0 if they
    overlap or touch, else the count of characters strictly between the
    nearer pair of edges. Symmetric in its two spans.
    """
    if a_end <= b_start:
        return b_start - a_end
    if b_end <= a_start:
        return a_start - b_end
    return 0


def _render_window(text: str, start: int, end: int, radius: int) -> str:
    """The excerpt text within ``radius`` characters on either side of
    ``[start, end)`` — an ellipsis marks a side that was actually truncated,
    never a side that genuinely reached the text boundary. Pure string
    slicing; no normalisation beyond what ``text`` already carries.
    """
    window_start = max(0, start - radius)
    window_end = min(len(text), end + radius)
    prefix = "…" if window_start > 0 else ""
    suffix = "…" if window_end < len(text) else ""
    return f"{prefix}{text[window_start:window_end]}{suffix}"


# ---------------------------------------------------------------------------
# Section 3: figure claims.
# ---------------------------------------------------------------------------

#: A hit (`"figure_supported_in_excerpt"`) or an OBSERVATION
#: (`"figure_absent_from_checked_excerpt"`) — never a violation. There is no
#: `"figure_contradicted"` value: it is not decidable against an abstract
#: (see the module docstring).
FigureStatus = Literal["figure_supported_in_excerpt", "figure_absent_from_checked_excerpt"]

_FIGURE_SUPPORTED: FigureStatus = "figure_supported_in_excerpt"
_FIGURE_ABSENT: FigureStatus = "figure_absent_from_checked_excerpt"


@dataclass(frozen=True, slots=True)
class MatchedWindow:
    """One token's rendered matched-excerpt window (task-packets/
    N2-T03b.yaml acceptance_criteria: "Every resolved figure renders its
    matched excerpt window ... no window size can distinguish a describing
    neighbour from an accidental one, so a human reader must be able to see
    the evidence"). ``token`` is the declared figure token this window
    supports; ``window_text`` is the excerpt text around the qualifying
    occurrence, radius = the claim's own ``anchor_window_chars``.
    """

    token: str
    window_text: str


@dataclass(frozen=True, slots=True)
class FigureVerdict:
    """One figure claim's evaluated verdict. ``matched_windows`` is
    POPULATED (one entry per declared token) iff ``status`` is
    ``"figure_supported_in_excerpt"`` — an absent claim renders no window,
    since there is no qualifying occurrence to show (task-packets/
    N2-T03b.yaml: the window is rendered for every RESOLVED figure).
    """

    claim_id: str
    citation_id: str
    status: FigureStatus
    matched_windows: tuple[MatchedWindow, ...]


def evaluate_figure_claim(
    *,
    claim_id: str,
    citation_id: str,
    tokens: Sequence[str],
    anchor_terms: Sequence[str],
    anchor_window_chars: int,
    excerpt_text: str | None,
) -> FigureVerdict:
    """Decide one figure claim's status against an already-read excerpt
    (``None`` when N2-T03a recorded no excerpt at all for this citation —
    treated identically to "no qualifying occurrence found", i.e. absent).

    ``"figure_supported_in_excerpt"`` iff EVERY token in ``tokens`` has at
    least one :func:`find_numerals` occurrence whose normalised value
    equals that token AND for which EVERY term in ``anchor_terms`` has some
    :func:`find_term_occurrences` occurrence within ``anchor_window_chars``
    (:func:`_char_gap`) of that SAME numeral occurrence. A single token
    failing this makes the WHOLE claim ``"figure_absent_from_checked_
    excerpt"`` (an observation, never a violation) — this module never
    reports a "partially supported" status (AGENTS.md prohibited shortcut:
    no collapsing of distinct outcomes, and no invented middle ground
    either).
    """
    if excerpt_text is None:
        return FigureVerdict(
            claim_id=claim_id, citation_id=citation_id, status=_FIGURE_ABSENT, matched_windows=()
        )

    numerals = find_numerals(excerpt_text)
    anchor_occurrences = {term: find_term_occurrences(term, excerpt_text) for term in anchor_terms}

    matched_windows: list[MatchedWindow] = []
    for token in tokens:
        candidate_occurrences = [numeral for numeral in numerals if numeral.value == token]
        qualifying_occurrence: NumeralOccurrence | None = None
        for occurrence in candidate_occurrences:
            if _all_anchors_within_window(occurrence, anchor_occurrences, anchor_window_chars):
                qualifying_occurrence = occurrence
                break
        if qualifying_occurrence is None:
            return FigureVerdict(
                claim_id=claim_id,
                citation_id=citation_id,
                status=_FIGURE_ABSENT,
                matched_windows=(),
            )
        matched_windows.append(
            MatchedWindow(
                token=token,
                window_text=_render_window(
                    excerpt_text,
                    qualifying_occurrence.start,
                    qualifying_occurrence.end,
                    anchor_window_chars,
                ),
            )
        )

    return FigureVerdict(
        claim_id=claim_id,
        citation_id=citation_id,
        status=_FIGURE_SUPPORTED,
        matched_windows=tuple(matched_windows),
    )


def _all_anchors_within_window(
    occurrence: NumeralOccurrence,
    anchor_occurrences: dict[str, tuple[TermOccurrence, ...]],
    anchor_window_chars: int,
) -> bool:
    """Does EVERY anchor term in ``anchor_occurrences`` have at least one
    occurrence within ``anchor_window_chars`` of ``occurrence``? A term with
    NO occurrence anywhere in the excerpt fails this immediately (an empty
    ``anchor_occurrences`` value, i.e. the term never appears at all).
    """
    for term_occurrences in anchor_occurrences.values():
        if not term_occurrences:
            return False
        closest_gap = min(
            _char_gap(occurrence.start, occurrence.end, term.start, term.end)
            for term in term_occurrences
        )
        if closest_gap > anchor_window_chars:
            return False
    return True


# ---------------------------------------------------------------------------
# Section 4: quotation claims.
# ---------------------------------------------------------------------------

#: A hit (`"quotation_verbatim"`), a VIOLATION (`"quotation_altered"` — the
#: excerpt carries the passage in different words), or an OBSERVATION
#: (`"quotation_absent_from_checked_excerpt"`).
QuotationStatus = Literal[
    "quotation_verbatim", "quotation_altered", "quotation_absent_from_checked_excerpt"
]

_QUOTATION_VERBATIM: QuotationStatus = "quotation_verbatim"
_QUOTATION_ALTERED: QuotationStatus = "quotation_altered"
_QUOTATION_ABSENT: QuotationStatus = "quotation_absent_from_checked_excerpt"

#: See the module docstring's "Quotations" section for the full rationale
#: and the empirical margin check against the real committed corpus. A
#: builder judgement call, not a pre-registered value — flagged as such.
DEFAULT_QUOTATION_SIMILARITY_THRESHOLD: float = 0.75


@dataclass(frozen=True, slots=True)
class QuotationVerdict:
    """One quotation claim's evaluated verdict. ``matched_text`` is the
    literal matched substring for ``"quotation_verbatim"``, the best-window
    text for ``"quotation_altered"``, and ``None`` for ``"quotation_absent_
    from_checked_excerpt"`` (there is nothing to show).
    """

    claim_id: str
    citation_id: str
    status: QuotationStatus
    matched_text: str | None


def _normalize_quotation_text(text: str) -> str:
    """Lower-case, whitespace-collapsed comparison form — used for BOTH the
    claim's own quoted text and the excerpt it is checked against, so
    comparison never depends on case or incidental whitespace.
    """
    return " ".join(text.lower().split())


def evaluate_quotation_claim(
    *,
    claim_id: str,
    citation_id: str,
    quote_text: str,
    excerpt_text: str | None,
    similarity_threshold: float = DEFAULT_QUOTATION_SIMILARITY_THRESHOLD,
) -> QuotationVerdict:
    """Decide one quotation claim's status against an already-read excerpt.

    Checked in order: (1) an exact, normalised substring match ->
    ``"quotation_verbatim"``. (2) Else, the single best
    ``difflib.SequenceMatcher`` ratio between the normalised quote and every
    word-length window of the normalised excerpt (a window the same word
    count as the quote, slid one word at a time) — at or above
    ``similarity_threshold`` -> ``"quotation_altered"`` (a VIOLATION: the
    excerpt carries the passage in different words). (3) Otherwise ->
    ``"quotation_absent_from_checked_excerpt"`` (an OBSERVATION).
    """
    if excerpt_text is None:
        return QuotationVerdict(
            claim_id=claim_id, citation_id=citation_id, status=_QUOTATION_ABSENT, matched_text=None
        )

    normalized_quote = _normalize_quotation_text(quote_text)
    normalized_excerpt = _normalize_quotation_text(excerpt_text)

    if normalized_quote and normalized_quote in normalized_excerpt:
        return QuotationVerdict(
            claim_id=claim_id,
            citation_id=citation_id,
            status=_QUOTATION_VERBATIM,
            matched_text=normalized_quote,
        )

    quote_words = normalized_quote.split()
    excerpt_words = normalized_excerpt.split()
    window_length = len(quote_words)

    best_ratio = 0.0
    best_window_text = ""
    if window_length > 0 and excerpt_words:
        last_start = max(0, len(excerpt_words) - window_length)
        for start_index in range(last_start + 1):
            window_words = excerpt_words[start_index : start_index + window_length]
            window_text = " ".join(window_words)
            ratio = difflib.SequenceMatcher(None, normalized_quote, window_text).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_window_text = window_text

    if best_ratio >= similarity_threshold:
        return QuotationVerdict(
            claim_id=claim_id,
            citation_id=citation_id,
            status=_QUOTATION_ALTERED,
            matched_text=best_window_text,
        )

    return QuotationVerdict(
        claim_id=claim_id, citation_id=citation_id, status=_QUOTATION_ABSENT, matched_text=None
    )


# ---------------------------------------------------------------------------
# Section 5: excluded claims — neither a hit nor a violation.
# ---------------------------------------------------------------------------

#: Exactly one value: the manifest itself declares a claim unavailable to
#: this check, with a named reason (a REFUTED claim, or an attribution the
#: manifest declines to invent) — counted and listed separately so an
#: exclusion can never read as a pass (task-packets/N2-T03b.yaml
#: status_vocabulary.excluded).
ExclusionStatus = Literal["claim_excluded"]

_CLAIM_EXCLUDED: ExclusionStatus = "claim_excluded"


@dataclass(frozen=True, slots=True)
class ExclusionVerdict:
    """One excluded claim, carrying the manifest's own named reason
    verbatim — never re-derived or summarised.
    """

    claim_id: str
    citation_id: str
    status: ExclusionStatus
    exclusion_reason: str


def build_exclusion_verdict(
    *, claim_id: str, citation_id: str, exclusion_reason: str
) -> ExclusionVerdict:
    """Trivial by design: an exclusion is a fact the manifest already
    states, not something this module decides — this function exists only
    so exclusions are constructed through the same typed shape as every
    other verdict, never a bare dict.
    """
    return ExclusionVerdict(
        claim_id=claim_id,
        citation_id=citation_id,
        status=_CLAIM_EXCLUDED,
        exclusion_reason=exclusion_reason,
    )
