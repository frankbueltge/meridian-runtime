"""Framework-independent state-machine library for the MRR lifecycles defined
in docs/spec/01_SYSTEM_SPEC.md section 6 (task-packets/E1-T04.yaml):
``RESEARCH_SCORE_LIFECYCLE``, ``TASK_BUNDLE_LIFECYCLE``, ``CLAIM_LIFECYCLE``,
``CORRECTION_LIFECYCLE``, plus ``TRANSFER_LIFECYCLE`` (task-packets/
E6-T01.yaml) and ``OBLIGATION_LIFECYCLE`` (task-packets/E6-T02.yaml), both
added without a section-6 diagram to anchor them — see each machine's own
comment block below and the "Open specification questions" list at the end
of this docstring.

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
