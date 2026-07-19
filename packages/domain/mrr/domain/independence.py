"""The independence validator (task-packets/E3-T05.yaml): a pure, deterministic,
framework-free function pair that decides whether one declared
``IndependenceProfile`` (``mrr.contracts.verification_result.IndependenceProfile``,
E3-T04) is genuinely independent of another, and counts distinct independent
reviews among a set of them. Fifth task of Epic E3 (claim, evidence,
correction kernel); the closest template for "pure decision logic over an
already-validated value, no persistence, no I/O" is
``mrr.domain.lifecycles.StateMachine`` (E1-T04).

--- Scope: DIMENSION independence, not IDENTITY self-verification ------------

This module does NOT re-implement, wrap, or overlap
``mrr.services.verification.service.VerificationService``'s self-verification
gate (MRR-FR-070, AGENTS.md rule 8: "reviewer identity != claim.proposer_id /
run.executor_id") — that is a check of WHO the reviewer is against the
claim's own recorded identities, already enforced elsewhere, and this task's
``forbidden_changes`` names it explicitly as out of scope. This module
answers a narrower, different question: given two ALREADY-DISTINCT reviewer
identities (or even the same one, twice — nothing here reads
``VerificationResult.reviewer_id`` at all), are their DECLARED independence
DIMENSIONS (the six ``IndependenceProfile`` fields) far enough apart to count
the second as independent evidence rather than a restatement of the first.
``VerificationResult.independence_profile`` is declared by the reviewer, not
derived by this module (see that contract's own docstring, "Independence is
DECLARED here, not validated") — this module is exactly the validation step
that docstring says does not yet exist, taking the declared profile as its
input and returning a verdict over it.

--- The independence rule, derived from domain 2.13 ---------------------------

docs/spec/02_DOMAIN_MODEL.md section 2.13's invariant, verbatim: "a reviewer
cannot satisfy independence if it shares the same execution principal and
unaltered reasoning path as the producer." Two conjuncts, both required to
DISQUALIFY:

1. **same execution principal** — ``verifier.principal == producer.principal``,
   a direct one-to-one mapping onto MRR-FR-071's ``principal`` dimension. No
   ambiguity here.
2. **unaltered reasoning path** — MRR-FR-071 lists six dimensions
   (``principal``, ``model_family``, ``prompt_family``, ``retrieval_path``,
   ``code_path``, ``data_access_path``) but does not itself define "reasoning
   path" as a named subset of them; domain 2.13 uses the phrase without
   enumerating which dimensions compose it either.

   **OPEN SPECIFICATION QUESTION** (flagged per task-packets/E3-T05.yaml
   stop_conditions, implemented per the clearest defensible reading rather
   than guessed silently): neither MRR-FR-071 nor domain 2.13 crisply states
   which of the five non-``principal`` dimensions compose "reasoning path".
   The reading implemented here — the one task-packets/E3-T05.yaml's own
   ``derived_decisions``/approved-design text offers as "a defensible
   reading" — is:

   - ``model_family``, ``prompt_family``, and ``code_path`` together
     constitute the **reasoning path**: which model was invoked, under which
     prompt/instruction family, executed by which code — i.e. the
     machinery that actually PRODUCES a judgment from inputs. "Unaltered"
     means all three are identical between verifier and producer.
   - ``retrieval_path`` and ``data_access_path`` are **evidence access**,
     not reasoning path: which route fetched a cited source and which
     credential/dataset view was used to read it. A verifier that reasons
     with the exact same model/prompt/code as the producer but fetches
     evidence through a different retrieval or data-access route has not
     altered ITS REASONING — it has altered how it reached the evidence
     that reasoning is applied to. Since domain 2.13 names "reasoning path"
     specifically (not "evidence path" or "all six dimensions"), retrieval
     and data-access are read as deliberately excluded from it.

   A different but not unreasonable partition could fold ``retrieval_path``
   and/or ``data_access_path`` into "reasoning path" too, on the theory that
   an unaltered evidence route is itself a form of unaltered process. That
   alternative is not implemented: MRR-FR-076's own separate concern (see
   below) already covers repeated judgments from an identical FULL
   configuration, including retrieval/data-access, via the dedup step —
   so under-scoping "reasoning path" here does not silently let a
   same-everything reviewer through as independent; it is caught by dedup
   collapsing it with the producer... except the producer is never itself
   counted as a verifier (see the "reflexivity" note below), so the
   practical effect of this choice is isolated to whether a
   same-principal-same-reasoning-but-different-evidence-route reviewer is
   disqualified (this module's reading: NOT independent, since principal AND
   reasoning path both match) or would be independent under the wider
   reading (retrieval/data-access folded in would make the reasoning path
   differ, flipping the verdict to independent). This is exactly the
   ambiguity being flagged for reviewer scrutiny, not resolved by inventing
   a specification the source documents do not state.

The resulting boolean rule, implemented by ``is_independent_of_producer``:

    NOT (
        verifier.principal == producer.principal
        AND verifier.model_family == producer.model_family
        AND verifier.prompt_family == producer.prompt_family
        AND verifier.code_path == producer.code_path
    )

Equivalently: independent unless BOTH the principal and the (model_family,
prompt_family, code_path) triple match exactly. A verifier that shares the
producer's principal but alters even ONE of the three reasoning-path
dimensions (e.g. principal identical, ``model_family`` different) IS
independent under this rule — a boundary case exercised explicitly in
``tests/unit/domain/test_independence.py`` and named in
task-packets/E3-T05.yaml's own acceptance_tests. This is a direct, literal
reading of domain 2.13's "AND" — both conjuncts must hold for disqualification,
so altering either one (principal, or any reasoning-path dimension) is
sufficient to make the pair independent.

--- MRR-FR-076: dedup key for "same model/configuration" ---------------------

MRR-FR-076: "Repeated judgments from the same model/configuration MUST NOT
count as independent reviews." task-packets/E3-T05.yaml's own objective text
glosses this as "collapsing verifiers that share the SAME independence
profile (same model/configuration) to a single count" — i.e. it equates "same
model/configuration" with "same independence profile", not with some
narrower, invented subset of it. The dedup key implemented here is therefore
the **full six-dimension tuple** (``principal``, ``model_family``,
``prompt_family``, ``retrieval_path``, ``code_path``, ``data_access_path``) —
two ``IndependenceProfile`` values collapse to one distinct count if and only
if all six fields compare equal.

This is a deliberate choice, flagged for reviewer scrutiny alongside the
reasoning-path question above: a narrower key (e.g. ``model_family`` +
``prompt_family`` + ``code_path`` only, mirroring the reasoning-path triple
above and treating retrieval/data-access as irrelevant to "the same
reviewer") was considered and rejected, because nowhere does MRR-FR-071 or
MRR-FR-076 decompose "model/configuration" into a named subset the way this
module already had to do for "reasoning path" — inventing a second,
different subset for a second, differently-worded phrase would compound one
specification gap with a second, unforced one. Using the full profile as the
identity of "a reviewer configuration" is the more conservative reading in
one direction (two reviews are collapsed only when EVERY declared dimension
agrees, so it under-collapses rather than over-collapses relative to a
narrower key) and the more literal reading of the packet's own wording in
the other. Not itself flagged as an open specification question (unlike
"reasoning path" above) since it is not derived from ambiguous prose in the
source spec documents themselves — it is a design decision made in the
absence of the source spec subdividing the phrase at all, and is documented
here rather than left implicit.

--- Reflexivity: the producer is never counted among the verifiers -----------

``distinct_independent_reviews``/``has_independent_verification`` take the
producer's own profile and a SEPARATE iterable of verifier profiles — the
producer is never itself a candidate in that iterable, matching every
existing caller shape in this codebase where a claim's own proposer and its
reviewers are two structurally distinct roles (``Claim.proposer_id`` vs.
``VerificationResult.reviewer_id``). If a caller mistakenly includes a
profile identical to the producer's own in the verifiers iterable, it is
correctly excluded by ``is_independent_of_producer`` (same principal, same
reasoning path -> not independent) — there is no special-cased "is this
actually the producer" identity check here, because none is needed: the
dimension rule already disqualifies an exact self-match on its own terms.

--- Additive, non-destructive stance (matches SourceFamily) ------------------

Neither function here deletes, reorders, or reweights anything. Both take an
iterable of already-recorded ``IndependenceProfile`` values and return a
verdict (bool) or a count (int) computed fresh each call — no caching, no
mutation of the inputs, no side effects, no I/O, no framework import. This
mirrors task-packets/E3-T05.yaml's own invariant ("the validator never
silently deletes or reweights a verification record; it only reports
independence verdicts and counts, analogous to SourceFamily's
additive-representation stance").

--- Not wired into ClaimService's supported-gate (decision, documented) -----

task-packets/E3-T05.yaml leaves "wire a `supported requires >= N independent
verifications` check into the E3-T02 ClaimService" as an optional, caller's-
choice addition to this task. It is deliberately NOT done here.
``mrr.services.claim.service.ClaimService`` has no dependency capable of
resolving a ``VerificationResult`` by id at all today — its constructor is
given an ``ObjectRepository``/``EdgeRepository`` pair scoped to ``Claim``
objects and typed edges, never a verification-result reader — so wiring this
validator in would first require adding a brand-new read dependency to that
service (a ``VerificationResult`` lookup-by-id capability), which is a
materially larger, separately-scoped change than "a small, clearly-scoped,
documented addition" the packet asks for. This module is therefore delivered
as a standalone building block only; wiring "supported requires >= N
independent verifications" into ``ClaimService.to_supported`` is left for a
later, explicitly scoped task.
"""

from __future__ import annotations

from collections.abc import Iterable

from mrr.contracts.verification_result import IndependenceProfile

#: The three MRR-FR-071 dimensions read here as composing "reasoning path"
#: per the module docstring's derivation. A plain ``tuple[str, str, str]``
#: (not a named type) since it is never constructed by a caller — only
#: compared internally.
_ReasoningPath = tuple[str, str, str]

#: The full six MRR-FR-071 dimensions, used as the FR-076 dedup key per the
#: module docstring's "dedup key" section.
_IndependenceKey = tuple[str, str, str, str, str, str]


def _reasoning_path(profile: IndependenceProfile) -> _ReasoningPath:
    """The (``model_family``, ``prompt_family``, ``code_path``) triple this
    module treats as "reasoning path" — see the module docstring's derivation
    and its flagged open specification question.
    """
    return (profile.model_family, profile.prompt_family, profile.code_path)


def _independence_key(profile: IndependenceProfile) -> _IndependenceKey:
    """The full six-dimension tuple used as the FR-076 "same
    model/configuration" dedup key — see the module docstring's "dedup key"
    section for why this is the full profile rather than a narrower subset.
    """
    return (
        profile.principal,
        profile.model_family,
        profile.prompt_family,
        profile.retrieval_path,
        profile.code_path,
        profile.data_access_path,
    )


def is_independent_of_producer(
    verifier: IndependenceProfile, producer: IndependenceProfile
) -> bool:
    """``True`` unless ``verifier`` shares BOTH the producer's execution
    principal AND an unaltered reasoning path (``model_family``,
    ``prompt_family``, ``code_path`` all equal) — domain 2.13's invariant,
    derived and documented in full in the module docstring. Sharing only one
    of the two (same principal but an altered reasoning-path dimension, or a
    different principal but an identical reasoning path) is independent
    under this rule; only sharing BOTH disqualifies.

    Pure and deterministic: depends only on the two arguments' field values,
    never on argument order (``is_independent_of_producer(a, b)`` and
    ``is_independent_of_producer(b, a)`` agree, since both conjuncts are
    symmetric equality checks), with no side effects.
    """
    same_principal = verifier.principal == producer.principal
    unaltered_reasoning_path = _reasoning_path(verifier) == _reasoning_path(producer)
    return not (same_principal and unaltered_reasoning_path)


def distinct_independent_reviews(
    producer: IndependenceProfile, verifiers: Iterable[IndependenceProfile]
) -> int:
    """Count the DISTINCT independent reviews among ``verifiers`` relative to
    ``producer`` (MRR-FR-076): first keep only the profiles that
    ``is_independent_of_producer`` accepts, then collapse any that share the
    same full six-dimension profile (the module docstring's "dedup key") to a
    single count. Two verifications sharing every declared dimension count
    once; verifications differing in even one dimension count separately.

    Deterministic and order-independent — the result depends only on the SET
    of distinct independence keys among the independent verifiers, never on
    how many times a key repeats or the order ``verifiers`` is iterated in.
    Never exceeds the number of items in ``verifiers`` (dedup only removes,
    never adds); never negative. Consumes ``verifiers`` in one pass, so a
    single-use iterator (e.g. a generator) is safe to pass here.
    """
    independent_keys = {
        _independence_key(verifier)
        for verifier in verifiers
        if is_independent_of_producer(verifier, producer)
    }
    return len(independent_keys)


def has_independent_verification(
    producer: IndependenceProfile,
    verifiers: Iterable[IndependenceProfile],
    *,
    minimum: int = 1,
) -> bool:
    """``True`` iff ``distinct_independent_reviews(producer, verifiers) >=
    minimum``. A thin, optional convenience wrapper (task-packets/E3-T05.yaml
    calls it out as an OPTIONAL addition) for a caller that only needs a
    threshold gate and not the exact count — provided as a building block;
    see the module docstring's "Not wired into ClaimService" section for why
    nothing in this codebase calls it yet.

    Raises:
        ValueError: ``minimum`` is negative — a caller/programmer error
            (a threshold below zero is never a meaningful requirement),
            matching this codebase's existing "caller supplied inconsistent
            data" convention (e.g. ``ClaimService.create``'s "revision must
            be 1" guard) rather than silently treating it as always-satisfied.
    """
    if minimum < 0:
        raise ValueError(f"minimum must be >= 0, got {minimum!r}")
    return distinct_independent_reviews(producer, verifiers) >= minimum
