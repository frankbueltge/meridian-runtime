"""Framework-independent state-machine library for the MRR lifecycles defined
in docs/spec/01_SYSTEM_SPEC.md section 6 (task-packets/E1-T04.yaml):
``RESEARCH_SCORE_LIFECYCLE``, ``TASK_BUNDLE_LIFECYCLE``, ``CLAIM_LIFECYCLE``,
``CORRECTION_LIFECYCLE``, plus ``TRANSFER_LIFECYCLE`` (task-packets/
E6-T01.yaml), ``OBLIGATION_LIFECYCLE`` (task-packets/E6-T02.yaml),
``METHOD_PROFILE_LIFECYCLE`` (task-packets/K0-T01.yaml, the Research Method
Kernel's first task — grounded in docs/spec/08_RESEARCH_METHOD_KERNEL.md
section 3's table), and ``QUESTION_MODEL_LIFECYCLE``/
``CONCEPT_CHARTER_LIFECYCLE``/``METHOD_PROTOCOL_LIFECYCLE``/
``EVIDENCE_MATRIX_LIFECYCLE``/``METHOD_RULING_LIFECYCLE``/
``RESEARCH_DECISION_LIFECYCLE`` (task-packets/K1-T01.yaml, the kernel
governance contracts task — also grounded in spec 08 section 3's table, AS
AMENDED by commit 1d453bf for ``MethodProtocol``), plus
``RELEASE_RECORD_LIFECYCLE`` (task-packets/E8-T04.yaml, docs/spec/adr/
ADR-0011-RELEASE-RECORD-AND-A4-APPROVAL-EVENT.md decision 1: "Lifecycle:
released -> superseded is the ONLY transition (E8-T05 drives it); release
records are never edited, deleted, or re-released"). None of these has a
section-6 diagram to anchor it — see each machine's own comment block below
and the "Open specification questions" list at the end of this docstring.

State names match the owning schema's ``status`` enum exactly, including
casing, where one exists (task-packets/E1-T04.yaml invariant): ResearchScore
and CorrectionEvent are UPPERCASE (schemas/research-score.schema.json,
schemas/correction-event.schema.json), Claim is lowercase
(schemas/claim.schema.json). TaskBundle has no schema-level status enum, so
its states are the section-6 diagram names verbatim (already UPPERCASE) —
see the open question below.

Edges are taken only from what is actually drawn in section 6, plus Claim's
two explicit universal rules written under its diagram; nothing else is
inferred (task-packets/E1-T04.yaml invariant "only transitions drawn in
section 6 ... are legal"). The ASCII diagrams there use pipe/plus column
position, not indentation depth, to say which state a branch hangs off —
each machine's edge list below documents the column reading that produced
it, in case the rendered spec is ever reflowed and the columns drift.

Self-transitions (state -> same state) are not drawn anywhere in section 6,
so none is declared legal here, including as a side effect of a universal
rule (Claim's "any status -> WITHDRAWN" / "-> SUPERSEDED" is applied with
that state excluded as a source) — task-packets/E1-T04.yaml invariant
"undrawn but plausible transitions ... are reported as open specification
questions, never implemented" plus the packet's plan-time guidance that
self-transitions are not legal.

Open specification questions (none implemented, all left for a future ADR
or spec amendment):

- ResearchScore has no drawn path back out of SUSPENDED (e.g. a resume to
  ACTIVE or APPROVED). As specified, a suspended score is permanently
  suspended within this machine.
- TaskBundle has no schema ``status`` enum to anchor its state names against
  (unlike the other three machines) — MRR-FR-004/062/063/092 do not resolve
  this, and no ``schemas/task-bundle.schema.json`` "status" property exists
  to check drift against. The states below are the section-6 diagram names
  verbatim; a future schema addition could change casing or membership.
- Claim's ``review_required`` has no drawn outgoing edge besides the two
  universal rules (-> WITHDRAWN, -> SUPERSEDED); there is no drawn way back
  to UNDER_REVIEW or any terminal verdict.
- Claim's ``legacy_unverified`` appears in schemas/claim.schema.json's status
  enum but nowhere in the section-6 diagram or its universal-rule prose. Per
  the approved implementation guidance it is included as a nonterminal state
  that participates only in the two universal rules; an explicit upgrade
  path (e.g. -> UNDER_REVIEW, referenced in planning as "GOV-028 context")
  is a plausible but undrawn transition and is intentionally not declared.
- Claim's diagram literally reads "Any status -> WITHDRAWN" and "Any status
  -> SUPERSEDED" with no "nonterminal" qualifier (unlike the review_required
  rule, which is explicitly qualified "Any nonterminal status -> ..."). Taken
  completely literally, that would also license WITHDRAWN -> SUPERSEDED and
  SUPERSEDED -> WITHDRAWN. This module does not declare either edge, because
  the same diagram block separately calls WITHDRAWN and SUPERSEDED terminal
  ("A withdrawn or superseded claim remains addressable" plus the packet's
  own framing that these two states "have no drawn outgoing edges") — read
  together, "any status" is treated as "any status other than the two
  terminal sinks themselves". This is a defensible but not the only possible
  reading; flagged for reviewer scrutiny rather than resolved unilaterally.
- CorrectionEvent's ``DELIVERY_PENDING`` has no drawn outgoing edge at all
  (not even a universal rule, since Correction has none). As specified, a
  correction that reaches DELIVERY_PENDING has no drawn way to reach
  RESOLVED, PARTIALLY_RESOLVED, or REJECTED_BY_RECIPIENT.
- TransferContract has no section-6 diagram at all (unlike the other four
  machines, each anchored to an ASCII diagram there) — task-packets/
  E6-T01.yaml ``specification_gaps`` item 1. ``TRANSFER_LIFECYCLE``'s edges
  are grounded only in MRR-FR-080/081's prose and the section 3.8 API /
  section 5.2 event enumeration. Whether ``respond`` may be called more than
  once for the same contract (e.g. a later terminal decision following an
  earlier ``deferred``/``unresolved``) is left undrawn and unimplemented —
  task-packets/E6-T01.yaml ``specification_gaps`` item 3 — pending a future
  specification amendment, exactly like this module's other open questions.
- Obligation has no section-6 diagram either (task-packets/E6-T02.yaml
  ``specification_gaps`` item 1). ``OBLIGATION_LIFECYCLE``'s two edges
  (``open -> resolved``, ``open -> deferred``) are grounded only in section
  3.8's two response endpoints (``POST .../resolve``, ``POST .../defer``)
  and section 5.2's ``obligation.resolved`` event name (``obligation.
  deferred`` is this task's own additive event — task-packets/E6-T02.yaml
  derived_decisions (h)). Whether a ``deferred`` Obligation may later be
  ``resolve``d (a second respond-style call) is left undrawn and
  unimplemented, mirroring ``TRANSFER_LIFECYCLE``'s own identical open
  question about a second ``respond`` after ``deferred``/``unresolved``.
- Whether a superseded ``MethodProfile`` may ever transition further (e.g.
  back toward ``draft`` for a correction) is undrawn — mirrors
  ``ResearchScore``'s own open question above for its ``SUSPENDED`` state.
  ``METHOD_PROFILE_LIFECYCLE`` declares exactly the two edges docs/spec/
  08_RESEARCH_METHOD_KERNEL.md section 3's table shows (``draft ->
  accepted``, ``accepted -> superseded``) and nothing else (task-packets/
  K0-T01.yaml ``specification_gaps``).
- ``QUESTION_MODEL_LIFECYCLE`` and ``CONCEPT_CHARTER_LIFECYCLE`` (task-packets/
  K1-T01.yaml) each declare exactly the same two-edge shape as
  ``METHOD_PROFILE_LIFECYCLE`` (``draft -> accepted``, ``accepted ->
  superseded``), grounded in the identical spec 08 section 3 table
  spelling for each entity. Whether ``superseded`` may ever transition
  further is undrawn for both, mirroring ``METHOD_PROFILE_LIFECYCLE``'s own
  identical open question.
- ``METHOD_PROTOCOL_LIFECYCLE`` (task-packets/K1-T01.yaml) is grounded in
  spec 08 section 3's table row, AS AMENDED by commit 1d453bf
  ("Spec-08-Amendment MethodProtocol-Re-Review-Zyklus"): "draft -> reviewed
  -> locked -> amended | executed; amended -> reviewed". Declares exactly
  five edges — ``(draft, reviewed)``, ``(reviewed, locked)``, ``(locked,
  amended)``, ``(locked, executed)``, ``(amended, reviewed)`` — with
  ``executed`` the ONLY terminal state. Before this amendment, the literal
  four-edge reading left ``amended`` a dead end (no drawn way back to
  ``reviewed``/``locked``/``executed``, and no legal second amendment,
  since ``StateMachine.__post_init__`` forbids declaring ``(amended,
  amended)`` as a self-transition) — flagged as the single most
  operationally significant open question by task-packets/K1-T01.yaml's own
  derivation agent, and resolved by the amendment: an amended protocol
  re-enters review, and from there may be re-locked (a fresh lock event,
  its own ``locked_at``/``locked_by``/``content_hash``) toward ``executed``
  or amended again — never via a direct ``(amended, executed)`` or
  ``(amended, amended)`` edge, both of which remain UNDRAWN and illegal.
- ``EVIDENCE_MATRIX_LIFECYCLE`` (task-packets/K1-T01.yaml) declares three
  edges verbatim from spec 08 section 3's table: ``draft -> active -> frozen
  -> superseded``. Whether ``superseded`` may transition further is
  undrawn, mirroring ``METHOD_PROFILE_LIFECYCLE``'s own identical open
  question.
- ``METHOD_RULING_LIFECYCLE`` (task-packets/K1-T01.yaml) declares two edges
  verbatim from spec 08 section 3's table: ``pending -> issued ->
  superseded``. Whether ``superseded`` may transition further is undrawn,
  mirroring the same open question above.
- ``RESEARCH_DECISION_LIFECYCLE`` (task-packets/K1-T01.yaml) is
  deliberately declared with exactly ONE state (``issued``) and an EMPTY
  transition set — spec 08 section 3's table names it "issued
  (append-only)" with no further state at all. This is a considered choice,
  not an oversight: it keeps ``ResearchDecision`` uniform with the other
  machines for any generic code iterating this module's full machine list
  (this module's own ``StateMachine.__post_init__`` accepts a machine with
  zero transitions without complaint), rather than requiring a
  "this machine doesn't support transitions" carve-out anywhere in calling
  code. Every ``(from, to)`` pair, including ``("issued", "issued")``, is
  therefore illegal — proving append-only-ness structurally rather than by
  convention.
- ``RELEASE_RECORD_LIFECYCLE`` (task-packets/E8-T04.yaml) declares exactly
  ONE edge, ``released -> superseded``, verbatim from ADR-0011 decision 1's
  own text — no section-6 diagram or spec-08-style table exists for this
  entity (it is not in docs/spec/02_DOMAIN_MODEL.md section 2 at all; the
  ADR is the record of that extension). Whether ``superseded`` may
  transition further is undrawn, mirroring ``METHOD_PROFILE_LIFECYCLE``'s
  own identical open question. This packet (E8-T04) never drives this
  machine at all — ``mrr.services.release.service.ReleaseService.create``
  always writes a brand-new ``ReleaseRecord`` at revision 1, status
  ``"released"``, and calls ``StateMachine.assert_transition`` for neither
  state; the edge exists for a future E8-T05 (the correction banner) to
  drive.
"""

from __future__ import annotations

from dataclasses import dataclass

from mrr.domain.exceptions import InvalidTransitionError

#: A single legal ``(from_state, to_state)`` edge.
Transition = tuple[str, str]


@dataclass(frozen=True, slots=True)
class StateMachine:
    """A pure, declarative set of legal transitions for one MRR lifecycle.

    Holds no mutable state of its own — the "current state" of an actual
    ResearchScore/TaskBundle/Claim/CorrectionEvent lives on that object, not
    here. This class only answers "is X -> Y legal for this machine", so
    callers elsewhere (persistence, services) enforce it without inventing
    their own transition tables.
    """

    name: str
    states: frozenset[str]
    transitions: frozenset[Transition]
    initial_state: str

    def __post_init__(self) -> None:
        if self.initial_state not in self.states:
            raise ValueError(
                f"{self.name}: initial_state {self.initial_state!r} is not one of "
                "the declared states"
            )
        for from_state, to_state in self.transitions:
            if from_state == to_state:
                raise ValueError(
                    f"{self.name}: self-transition {from_state!r} -> {to_state!r} is "
                    "not drawn in docs/spec/01_SYSTEM_SPEC.md section 6 and must not "
                    "be declared as a legal edge"
                )
            if from_state not in self.states:
                raise ValueError(
                    f"{self.name}: transition source {from_state!r} is not a declared state"
                )
            if to_state not in self.states:
                raise ValueError(
                    f"{self.name}: transition target {to_state!r} is not a declared state"
                )

    def can_transition(self, from_state: str, to_state: str) -> bool:
        """Return ``True`` exactly when ``(from_state, to_state)`` is a
        declared legal edge for this machine.

        An unrecognized state name simply cannot appear in any declared
        edge, so it returns ``False`` here rather than raising — this method
        never raises. Callers that need fail-closed behavior on an unknown
        state name (rather than a silent ``False``) use
        :meth:`assert_transition`.
        """
        return (from_state, to_state) in self.transitions

    def assert_transition(self, from_state: str, to_state: str) -> None:
        """Raise :class:`InvalidTransitionError` unless ``(from_state,
        to_state)`` is a declared legal edge for this machine.

        This includes unknown state names on either side (fail closed): a
        name that is not one of ``states`` cannot be part of any declared
        edge, so it is rejected exactly like a known-but-illegal pair,
        with the same typed error carrying machine/from/to. Never mutates
        anything, so a caller that lets the error propagate is left with no
        partial state.
        """
        if not self.can_transition(from_state, to_state):
            raise InvalidTransitionError(self.name, from_state, to_state)


# ---------------------------------------------------------------------------
# ResearchScore — docs/spec/01_SYSTEM_SPEC.md section 6.1
# ---------------------------------------------------------------------------
#
#   DRAFT -> IN_REVIEW -> APPROVED -> ACTIVE -> SUPERSEDED -> ARCHIVED
#                |            |
#                v            v
#             REJECTED      SUSPENDED
#
# Column reading: the first "|"/"v" column (13) falls inside IN_REVIEW's
# span in the header line (columns 9-17), so that branch is
# IN_REVIEW -> REJECTED. The second column (26) falls inside APPROVED's span
# (columns 22-29), so that branch is APPROVED -> SUSPENDED. No other branches
# are drawn; in particular SUSPENDED has no drawn way back to APPROVED or
# ACTIVE (open question above).

_RESEARCH_SCORE_STATES = frozenset(
    {
        "DRAFT",
        "IN_REVIEW",
        "APPROVED",
        "ACTIVE",
        "REJECTED",
        "SUSPENDED",
        "SUPERSEDED",
        "ARCHIVED",
    }
)

_RESEARCH_SCORE_TRANSITIONS = frozenset(
    {
        ("DRAFT", "IN_REVIEW"),
        ("IN_REVIEW", "APPROVED"),
        ("IN_REVIEW", "REJECTED"),
        ("APPROVED", "ACTIVE"),
        ("APPROVED", "SUSPENDED"),
        ("ACTIVE", "SUPERSEDED"),
        ("SUPERSEDED", "ARCHIVED"),
    }
)

RESEARCH_SCORE_LIFECYCLE = StateMachine(
    name="ResearchScore",
    states=_RESEARCH_SCORE_STATES,
    transitions=_RESEARCH_SCORE_TRANSITIONS,
    initial_state="DRAFT",
)


# ---------------------------------------------------------------------------
# TaskBundle — docs/spec/01_SYSTEM_SPEC.md section 6.2
# ---------------------------------------------------------------------------
#
#   CREATED -> OFFERED -> ACCEPTED -> QUEUED -> RUNNING -> COMPLETED -> SEALED
#                    |       |          |          |           |
#                    |       |          |          +-> FAILED  +-> INVALID_RESULT
#                    |       |          +-> CANCELLED
#                    |       +-> EXPIRED
#                    +-> MODIFICATION_PROPOSED -> OFFERED
#                    +-> DEFERRED
#                    +-> REJECTED
#
# Column reading against the header line's spans (OFFERED 11-17, ACCEPTED
# 22-29, QUEUED 34-39, RUNNING 44-50, COMPLETED 55-63): column 17 (OFFERED's
# last character) carries three stacked branches on the following rows —
# MODIFICATION_PROPOSED (which itself arcs back to OFFERED), DEFERRED, and
# REJECTED — so all three are OFFERED -> X. Column 25 (inside ACCEPTED) is
# EXPIRED, so ACCEPTED -> EXPIRED, not OFFERED -> EXPIRED. Column 36 (inside
# QUEUED) is CANCELLED, so QUEUED -> CANCELLED. Column 47 (inside RUNNING) is
# FAILED, so RUNNING -> FAILED. Column 59 (inside COMPLETED) is
# INVALID_RESULT, so COMPLETED -> INVALID_RESULT.
#
# No schema status enum exists for TaskBundle to check these names against
# (open question above); the fourteen names below are the diagram's verbatim
# spelling.

_TASK_BUNDLE_STATES = frozenset(
    {
        "CREATED",
        "OFFERED",
        "ACCEPTED",
        "MODIFICATION_PROPOSED",
        "DEFERRED",
        "REJECTED",
        "QUEUED",
        "EXPIRED",
        "CANCELLED",
        "RUNNING",
        "FAILED",
        "COMPLETED",
        "SEALED",
        "INVALID_RESULT",
    }
)

_TASK_BUNDLE_TRANSITIONS = frozenset(
    {
        ("CREATED", "OFFERED"),
        ("OFFERED", "ACCEPTED"),
        ("OFFERED", "MODIFICATION_PROPOSED"),
        ("MODIFICATION_PROPOSED", "OFFERED"),
        ("OFFERED", "DEFERRED"),
        ("OFFERED", "REJECTED"),
        ("ACCEPTED", "QUEUED"),
        ("ACCEPTED", "EXPIRED"),
        ("QUEUED", "RUNNING"),
        ("QUEUED", "CANCELLED"),
        ("RUNNING", "COMPLETED"),
        ("RUNNING", "FAILED"),
        ("COMPLETED", "SEALED"),
        ("COMPLETED", "INVALID_RESULT"),
    }
)

TASK_BUNDLE_LIFECYCLE = StateMachine(
    name="TaskBundle",
    states=_TASK_BUNDLE_STATES,
    transitions=_TASK_BUNDLE_TRANSITIONS,
    initial_state="CREATED",
)


# ---------------------------------------------------------------------------
# Claim — docs/spec/01_SYSTEM_SPEC.md section 6.3
# ---------------------------------------------------------------------------
#
#   DRAFT -> UNDER_REVIEW -> SUPPORTED
#                       |-> CONTESTED
#                       |-> CONTRADICTED
#                       |-> UNRESOLVED
#                       |-> UNSUPPORTED
#   Any nonterminal status -> REVIEW_REQUIRED
#   Any status -> WITHDRAWN
#   Any status -> SUPERSEDED
#
# Column reading: all four stacked "|->" branches sit at the column of
# UNDER_REVIEW's last character, so they are all UNDER_REVIEW -> X, alongside
# the header line's own UNDER_REVIEW -> SUPPORTED.
#
# Terminal states (no drawn outgoing edge, including no outgoing edge
# produced by the universal rules — see the module-level open question on
# the literal "any status" wording): WITHDRAWN, SUPERSEDED. Every other
# state (DRAFT, UNDER_REVIEW, SUPPORTED, CONTESTED, CONTRADICTED, UNRESOLVED,
# UNSUPPORTED, REVIEW_REQUIRED, LEGACY_UNVERIFIED — schema-lowercase below)
# is nonterminal and therefore gains -> review_required, -> withdrawn, and
# -> superseded, minus any self-loop.
#
# State names are lowercase to match schemas/claim.schema.json's status enum
# exactly, per the packet invariant (Claim is the one lowercase machine).
# `legacy_unverified` is the schema's 11th value with no diagram presence;
# see the module-level open question for its scope here.

_CLAIM_STATES = frozenset(
    {
        "draft",
        "under_review",
        "supported",
        "contested",
        "contradicted",
        "unresolved",
        "unsupported",
        "review_required",
        "withdrawn",
        "superseded",
        "legacy_unverified",
    }
)

#: States with a drawn outgoing edge into WITHDRAWN/SUPERSEDED, i.e. every
#: state except the two terminal sinks themselves (see the module-level open
#: question on the literal "any status" wording).
_CLAIM_TERMINAL_STATES = frozenset({"withdrawn", "superseded"})
_CLAIM_NONTERMINAL_STATES = _CLAIM_STATES - _CLAIM_TERMINAL_STATES

_CLAIM_TRANSITIONS = frozenset(
    {
        ("draft", "under_review"),
        ("under_review", "supported"),
        ("under_review", "contested"),
        ("under_review", "contradicted"),
        ("under_review", "unresolved"),
        ("under_review", "unsupported"),
    }
    # "Any nonterminal status -> REVIEW_REQUIRED", minus the review_required
    # self-loop.
    | {
        (state, "review_required")
        for state in _CLAIM_NONTERMINAL_STATES
        if state != "review_required"
    }
    # "Any status -> WITHDRAWN", read as any state other than the two
    # terminal sinks (excludes the withdrawn self-loop and, per the
    # module-level open question, superseded -> withdrawn).
    | {(state, "withdrawn") for state in _CLAIM_NONTERMINAL_STATES if state != "withdrawn"}
    # "Any status -> SUPERSEDED", same reading (excludes the superseded
    # self-loop and withdrawn -> superseded).
    | {(state, "superseded") for state in _CLAIM_NONTERMINAL_STATES if state != "superseded"}
)

CLAIM_LIFECYCLE = StateMachine(
    name="Claim",
    states=_CLAIM_STATES,
    transitions=_CLAIM_TRANSITIONS,
    initial_state="draft",
)


# ---------------------------------------------------------------------------
# CorrectionEvent — docs/spec/01_SYSTEM_SPEC.md section 6.4
# ---------------------------------------------------------------------------
#
#   OPEN -> IMPACT_ANALYSIS -> NOTIFYING -> AWAITING_RESPONSES -> RESOLVED
#                                            |                  |-> PARTIALLY_RESOLVED
#                                            |                  |-> REJECTED_BY_RECIPIENT
#                                            +-> DELIVERY_PENDING
#
# Column reading against the header line's spans (NOTIFYING 27-35,
# AWAITING_RESPONSES 40-57): the first branch column (41) falls inside
# AWAITING_RESPONSES's span, not NOTIFYING's (which ends at 35) — so
# DELIVERY_PENDING hangs off AWAITING_RESPONSES, not NOTIFYING. The second
# branch column (60) sits directly under the "->" that leads into RESOLVED
# on the header line, i.e. the same source as that arrow, AWAITING_RESPONSES;
# it carries PARTIALLY_RESOLVED and REJECTED_BY_RECIPIENT on the two rows
# below it. All four destinations reachable from this diagram — RESOLVED,
# PARTIALLY_RESOLVED, REJECTED_BY_RECIPIENT, DELIVERY_PENDING — are therefore
# all AWAITING_RESPONSES -> X.
#
# DELIVERY_PENDING has no drawn outgoing edge at all (open question above);
# Correction has no universal rules (unlike Claim), so nothing rescues it.

_CORRECTION_STATES = frozenset(
    {
        "OPEN",
        "IMPACT_ANALYSIS",
        "NOTIFYING",
        "AWAITING_RESPONSES",
        "DELIVERY_PENDING",
        "RESOLVED",
        "PARTIALLY_RESOLVED",
        "REJECTED_BY_RECIPIENT",
    }
)

_CORRECTION_TRANSITIONS = frozenset(
    {
        ("OPEN", "IMPACT_ANALYSIS"),
        ("IMPACT_ANALYSIS", "NOTIFYING"),
        ("NOTIFYING", "AWAITING_RESPONSES"),
        ("AWAITING_RESPONSES", "RESOLVED"),
        ("AWAITING_RESPONSES", "PARTIALLY_RESOLVED"),
        ("AWAITING_RESPONSES", "REJECTED_BY_RECIPIENT"),
        ("AWAITING_RESPONSES", "DELIVERY_PENDING"),
    }
)

CORRECTION_LIFECYCLE = StateMachine(
    name="CorrectionEvent",
    states=_CORRECTION_STATES,
    transitions=_CORRECTION_TRANSITIONS,
    initial_state="OPEN",
)


# ---------------------------------------------------------------------------
# TransferContract — task-packets/E6-T01.yaml. NO section-6 diagram exists
# for this entity (unlike the four machines above, each anchored to an
# ASCII diagram there) — see the module docstring's "Open specification
# questions" list.
# ---------------------------------------------------------------------------
#
#   created -> offered -> {accepted, adapted, rejected, deferred, unresolved}
#
# Grounded only in docs/spec/01_SYSTEM_SPEC.md MRR-FR-080 ("A transfer
# between practices MUST use a versioned TransferContract ...") and
# MRR-FR-081 ("The receiving practice MUST respond with accepted, adapted,
# rejected, deferred, or unresolved"), plus the section 3.8 API surface
# (POST /v1/transfers, .../offer, .../respond) and the section 5.2 required
# event list (transfer.offered, transfer.responded) — never a verified
# section-6 diagram. Every one of the five terminal outcomes is reachable
# ONLY from OFFERED, and each is drawn as its own edge rather than a single
# "OFFERED -> RESPONDED" collapse, so an illegal decision value can never be
# recorded even in principle (``StateMachine.assert_transition`` rejects it
# structurally, not just by convention).
#
# State names are lowercase, taken verbatim from MRR-FR-081's own prose
# (task-packets/E6-T01.yaml derived_decisions (b)) — the same discipline
# this module already applies to Claim ("State names match the owning
# schema's status enum exactly, including casing, where one exists"; here,
# schemas/transfer-contract.schema.json's own status enum IS lowercase,
# taken directly from the FR text since no diagram exists to anchor casing
# against instead).

_TRANSFER_STATES = frozenset(
    {
        "created",
        "offered",
        "accepted",
        "adapted",
        "rejected",
        "deferred",
        "unresolved",
    }
)

_TRANSFER_TRANSITIONS = frozenset(
    {
        ("created", "offered"),
        ("offered", "accepted"),
        ("offered", "adapted"),
        ("offered", "rejected"),
        ("offered", "deferred"),
        ("offered", "unresolved"),
    }
)

TRANSFER_LIFECYCLE = StateMachine(
    name="TransferContract",
    states=_TRANSFER_STATES,
    transitions=_TRANSFER_TRANSITIONS,
    initial_state="created",
)


# ---------------------------------------------------------------------------
# Obligation — task-packets/E6-T02.yaml. NO section-6 diagram exists for this
# entity either (like TransferContract) — see the module docstring's "Open
# specification questions" list.
# ---------------------------------------------------------------------------
#
#   open -> {resolved, deferred}
#
# Grounded only in docs/spec/03_API_AND_EVENTS.md section 3.8's two response
# endpoints (POST /v1/obligations/{id}/resolve, .../defer) and section 5.2's
# obligation.resolved event name (obligation.deferred is this task's own
# additive event, task-packets/E6-T02.yaml derived_decisions (h) — there is
# no drawn/enumerated "obligation.deferred" in section 5.2's literal list,
# but the API surface's own /defer endpoint needs SOME recorded outcome).
# Both terminal states (resolved, deferred) have no drawn outgoing edge —
# whether a deferred Obligation may later be resolved via a second
# respond-style call is undrawn and out of scope, mirroring
# TRANSFER_LIFECYCLE's own identical open question for TransferContract's
# own respond.
#
# State names are lowercase, taken verbatim from the section 3.8 endpoint
# names (resolve/defer) — the same discipline this module already applies
# to TransferContract in the absence of any section-6 diagram or schema
# precedent to anchor casing against instead (task-packets/E6-T02.yaml
# derived_decisions (a)).

_OBLIGATION_STATES = frozenset({"open", "resolved", "deferred"})

_OBLIGATION_TRANSITIONS = frozenset(
    {
        ("open", "resolved"),
        ("open", "deferred"),
    }
)

OBLIGATION_LIFECYCLE = StateMachine(
    name="Obligation",
    states=_OBLIGATION_STATES,
    transitions=_OBLIGATION_TRANSITIONS,
    initial_state="open",
)


# ---------------------------------------------------------------------------
# MethodProfile — task-packets/K0-T01.yaml (docs/spec/08_RESEARCH_METHOD_KERNEL.md
# section 3's table). No section-6 diagram exists for this entity either
# (like TransferContract above) — it is grounded only in spec 08 section 3's
# table row: "MethodProfile | section 2 declaration | draft -> accepted ->
# superseded".
# ---------------------------------------------------------------------------
#
#   draft -> accepted -> superseded
#
# Every transition mints a NEW REVISION with `status` changed in the body
# (content hash recomputed) — MethodProfile is UNSIGNED and revision-based,
# like ResearchScore/Claim, not event-only like TaskBundle (task-packets/
# K0-T01.yaml derived_decisions (a)). State names are lowercase, taken
# verbatim from spec 08 section 3's own table spelling — the same discipline
# this module already applies to Claim and TransferContract when no
# section-6 diagram exists to anchor casing against instead.
#
# `superseded` has no drawn outgoing edge (open question above, mirroring
# ResearchScore's own undrawn path out of SUSPENDED): a superseded profile
# revision cannot transition further within this machine. A later, semver-
# bumped profile supersedes the OLD id via a freshly minted new id's own
# `supersedes` field (inherited from baseObject) — a cross-id relationship,
# not a transition on this state machine (task-packets/K0-T01.yaml
# derived_decisions (b)).

_METHOD_PROFILE_STATES = frozenset({"draft", "accepted", "superseded"})

_METHOD_PROFILE_TRANSITIONS = frozenset(
    {
        ("draft", "accepted"),
        ("accepted", "superseded"),
    }
)

METHOD_PROFILE_LIFECYCLE = StateMachine(
    name="MethodProfile",
    states=_METHOD_PROFILE_STATES,
    transitions=_METHOD_PROFILE_TRANSITIONS,
    initial_state="draft",
)


# ---------------------------------------------------------------------------
# QuestionModel — task-packets/K1-T01.yaml (docs/spec/08_RESEARCH_METHOD_KERNEL.md
# section 3's table). No section-6 diagram exists for this entity either —
# grounded only in spec 08 section 3's table row: "QuestionModel |
# decomposition of a question ... | draft -> accepted -> superseded".
# ---------------------------------------------------------------------------
#
#   draft -> accepted -> superseded
#
# Identical shape to METHOD_PROFILE_LIFECYCLE. `superseded` has no drawn
# outgoing edge (open question above, mirroring MethodProfile's own
# identical treatment).

_QUESTION_MODEL_STATES = frozenset({"draft", "accepted", "superseded"})

_QUESTION_MODEL_TRANSITIONS = frozenset(
    {
        ("draft", "accepted"),
        ("accepted", "superseded"),
    }
)

QUESTION_MODEL_LIFECYCLE = StateMachine(
    name="QuestionModel",
    states=_QUESTION_MODEL_STATES,
    transitions=_QUESTION_MODEL_TRANSITIONS,
    initial_state="draft",
)


# ---------------------------------------------------------------------------
# ConceptCharter — task-packets/K1-T01.yaml (docs/spec/08_RESEARCH_METHOD_KERNEL.md
# section 3's table). No section-6 diagram exists for this entity either —
# grounded only in spec 08 section 3's table row: "ConceptCharter |
# versioned local operationalizations ... | draft -> accepted -> superseded".
# ---------------------------------------------------------------------------
#
#   draft -> accepted -> superseded
#
# Identical shape to METHOD_PROFILE_LIFECYCLE/QUESTION_MODEL_LIFECYCLE.
# `superseded` has no drawn outgoing edge (open question above).

_CONCEPT_CHARTER_STATES = frozenset({"draft", "accepted", "superseded"})

_CONCEPT_CHARTER_TRANSITIONS = frozenset(
    {
        ("draft", "accepted"),
        ("accepted", "superseded"),
    }
)

CONCEPT_CHARTER_LIFECYCLE = StateMachine(
    name="ConceptCharter",
    states=_CONCEPT_CHARTER_STATES,
    transitions=_CONCEPT_CHARTER_TRANSITIONS,
    initial_state="draft",
)


# ---------------------------------------------------------------------------
# MethodProtocol — task-packets/K1-T01.yaml (docs/spec/08_RESEARCH_METHOD_KERNEL.md
# section 3's table, AS AMENDED by commit 1d453bf "Spec-08-Amendment
# MethodProtocol-Re-Review-Zyklus"). No section-6 diagram exists for this
# entity either — grounded only in spec 08 section 3's table row, as
# amended: "draft -> reviewed -> locked -> amended | executed; amended ->
# reviewed (re-review cycle: an amendment is a new revision that must be
# re-reviewed and re-locked before further confirmatory work; each lock
# binds that revision's own content hash, so work recorded under an
# earlier lock stays auditable against its own hash)".
# ---------------------------------------------------------------------------
#
#   draft -> reviewed -> locked -> amended
#                            |        |
#                            |        +-> reviewed  (re-review cycle)
#                            +-> executed
#
# Five edges: (draft, reviewed), (reviewed, locked), (locked, amended),
# (locked, executed), (amended, reviewed). `executed` is the ONLY terminal
# state — see the module docstring's "Open specification questions" section
# for the full history of why this edge was added (task-packets/K1-T01.yaml
# flagged the literal four-edge reading's `amended` dead end as its single
# most operationally significant open question; commit 1d453bf resolves it
# by amending spec 08 section 3's table text itself, not by this module
# inventing an undrawn edge unilaterally).
#
# Still UNDRAWN and illegal, even after the amendment: `(amended, executed)`
# (an amended protocol must pass back through a fresh reviewed/locked pair
# before further confirmatory work — never a direct shortcut to executed)
# and `(amended, amended)` (a second amendment must also pass back through
# reviewed/locked first; `StateMachine.__post_init__`'s self-transition ban
# would reject a direct declaration of this edge regardless). `(draft,
# locked)` (skips reviewed) and `(reviewed, executed)`/`(reviewed,
# amended)` (skip locked) are likewise undrawn.

_METHOD_PROTOCOL_STATES = frozenset({"draft", "reviewed", "locked", "amended", "executed"})

_METHOD_PROTOCOL_TRANSITIONS = frozenset(
    {
        ("draft", "reviewed"),
        ("reviewed", "locked"),
        ("locked", "amended"),
        ("locked", "executed"),
        ("amended", "reviewed"),
    }
)

METHOD_PROTOCOL_LIFECYCLE = StateMachine(
    name="MethodProtocol",
    states=_METHOD_PROTOCOL_STATES,
    transitions=_METHOD_PROTOCOL_TRANSITIONS,
    initial_state="draft",
)


# ---------------------------------------------------------------------------
# EvidenceMatrix — task-packets/K1-T01.yaml (docs/spec/08_RESEARCH_METHOD_KERNEL.md
# section 3's table). No section-6 diagram exists for this entity either —
# grounded only in spec 08 section 3's table row: "EvidenceMatrix |
# structured evidence ... | draft -> active -> frozen -> superseded".
# ---------------------------------------------------------------------------
#
#   draft -> active -> frozen -> superseded
#
# `superseded` has no drawn outgoing edge (open question above, mirroring
# METHOD_PROFILE_LIFECYCLE's own identical treatment).

_EVIDENCE_MATRIX_STATES = frozenset({"draft", "active", "frozen", "superseded"})

_EVIDENCE_MATRIX_TRANSITIONS = frozenset(
    {
        ("draft", "active"),
        ("active", "frozen"),
        ("frozen", "superseded"),
    }
)

EVIDENCE_MATRIX_LIFECYCLE = StateMachine(
    name="EvidenceMatrix",
    states=_EVIDENCE_MATRIX_STATES,
    transitions=_EVIDENCE_MATRIX_TRANSITIONS,
    initial_state="draft",
)


# ---------------------------------------------------------------------------
# MethodRuling — task-packets/K1-T01.yaml (docs/spec/08_RESEARCH_METHOD_KERNEL.md
# section 3's table). No section-6 diagram exists for this entity either —
# grounded only in spec 08 section 3's table row: "MethodRuling | the
# ruling that licenses claim language ... | pending -> issued -> superseded".
# ---------------------------------------------------------------------------
#
#   pending -> issued -> superseded
#
# `superseded` has no drawn outgoing edge (open question above, mirroring
# METHOD_PROFILE_LIFECYCLE's own identical treatment).

_METHOD_RULING_STATES = frozenset({"pending", "issued", "superseded"})

_METHOD_RULING_TRANSITIONS = frozenset(
    {
        ("pending", "issued"),
        ("issued", "superseded"),
    }
)

METHOD_RULING_LIFECYCLE = StateMachine(
    name="MethodRuling",
    states=_METHOD_RULING_STATES,
    transitions=_METHOD_RULING_TRANSITIONS,
    initial_state="pending",
)


# ---------------------------------------------------------------------------
# ResearchDecision — task-packets/K1-T01.yaml (docs/spec/08_RESEARCH_METHOD_KERNEL.md
# section 3's table). No section-6 diagram exists for this entity either —
# grounded only in spec 08 section 3's table row: "ResearchDecision |
# adaptive decision record ... | issued (append-only)".
# ---------------------------------------------------------------------------
#
#   issued  (one state, zero transitions — append-only)
#
# Deliberately declared with exactly one state and an EMPTY transition set
# (see the module docstring's "Open specification questions" section) —
# every `(from, to)` pair, including `("issued", "issued")`, is illegal,
# proving append-only-ness structurally rather than by convention.

_RESEARCH_DECISION_STATES = frozenset({"issued"})

_RESEARCH_DECISION_TRANSITIONS: frozenset[Transition] = frozenset()

RESEARCH_DECISION_LIFECYCLE = StateMachine(
    name="ResearchDecision",
    states=_RESEARCH_DECISION_STATES,
    transitions=_RESEARCH_DECISION_TRANSITIONS,
    initial_state="issued",
)


# ---------------------------------------------------------------------------
# ReleaseRecord — task-packets/E8-T04.yaml (docs/spec/adr/ADR-0011-RELEASE-
# RECORD-AND-A4-APPROVAL-EVENT.md decision 1). No section-6 diagram exists
# for this entity either — grounded only in the ADR's own literal text:
# "Lifecycle: released -> superseded is the ONLY transition (E8-T05 drives
# it); release records are never edited, deleted, or re-released."
# ---------------------------------------------------------------------------
#
#   released -> superseded
#
# Exactly one edge. `superseded` has no drawn outgoing edge (open question
# above, mirroring METHOD_PROFILE_LIFECYCLE's own identical treatment).
# task-packets/E8-T04.yaml itself never drives this machine — it only ever
# creates revision-1 "released" records; the edge is declared here for a
# future E8-T05 to drive, exactly as ADR-0011 decision 1 states.

_RELEASE_RECORD_STATES = frozenset({"released", "superseded"})

_RELEASE_RECORD_TRANSITIONS = frozenset(
    {
        ("released", "superseded"),
    }
)

RELEASE_RECORD_LIFECYCLE = StateMachine(
    name="ReleaseRecord",
    states=_RELEASE_RECORD_STATES,
    transitions=_RELEASE_RECORD_TRANSITIONS,
    initial_state="released",
)
