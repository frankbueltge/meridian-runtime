"""Provider-neutral structured generation with bounded schema-repair
(task-packets/E4-T02.yaml), built directly on the merged E4-T01
``mrr.domain.model_adapter`` port.

``generate_structured`` takes an injected ``ModelAdapter`` (E4-T01's
Protocol port), a ``ModelInvocationRequest``, a caller-supplied Pydantic v2
target model, and an explicit ``max_repair_attempts`` (an int >= 0). It
invokes the adapter, validates the candidate response against the target
model, and — on a validation failure — performs a BOUNDED number of repair
attempts, feeding the Pydantic ``ValidationError`` back into a follow-up
call's prompt. Every underlying call (the initial one and each repair) is
recorded as a ``ModelInvocationOutcome`` in an ordered audit trail
(``StructuredGenerationResult.attempts``), regardless of whether the overall
call succeeds.

This module is NOT itself a ``ModelAdapter`` (docs/spec/06_IMPLEMENTATION_
PLAN.md#e4 -- agent-roles-and-model-adapters, task-packets/E4-T02.yaml
derived_decisions): its return type is a parsed proposal plus an ordered
audit trail, not a single ``ModelInvocationOutcome``. It reuses
``mrr.domain.model_adapter`` VERBATIM -- the ``ModelAdapter`` Protocol,
``ModelInvocationRequest``/``ModelInvocationOutcome``, and the redaction
vocabulary -- and changes none of their semantics. It validates candidate
output with Pydantic v2 (the caller supplies the target model) and does not
reimplement validation or hashing. The repair prompt is built inline from
the target model's JSON schema and the validation error; a versioned named-
prompt registry is a separate, later task (E4-T06) and is out of scope here
(task-packets/E4-T02.yaml derived_decisions).

--- Why this module is framework- and provider-free ------------------------

This module imports only the standard library, Pydantic (already a core
project dependency, not a model-provider SDK), and ``mrr.domain.
model_adapter``. It opens no network connection and calls no model-provider
SDK anywhere -- the ONLY way it ever reaches a model is through the caller-
injected ``ModelAdapter.invoke`` method. This is machine-enforced by adding
``mrr.adapters.llm`` to import-linter contract 1 ("Core packages stay
framework- and provider-free", pyproject.toml) alongside ``mrr.adapters.
object_store`` -- the same guarantee that root already carries, and exactly
the provider neutrality MRR-NFR-004/MRR-NFR-010 require (task-packets/
E4-T02.yaml derived_decisions).

--- The proposal-only, no-invalid-passes result shape ----------------------

``StructuredGenerationResult`` mirrors ``ModelInvocationOutcome``'s own
"biconditional enforced in ``__post_init__``" style (E4-T01): ``proposal``
is present if and only if ``status == "proposal"``, structurally guaranteeing
that invalid, partially parsed, or coerced output can never be returned
alongside (or mistaken for) a valid proposal (MRR-FR-046; docs/spec/
02_DOMAIN_MODEL.md global invariant "No model output bypasses schema and
domain validation"). ``status`` carries a CLOSED, six-value vocabulary:
``"proposal"`` (success -- A1 "clearly marked proposal", docs/spec/
01_SYSTEM_SPEC.md section 5) or one of the five DISTINCT failure kinds --
``"schema_invalid"`` (this layer's own outcome: every repair attempt was
exhausted without a schema-valid response) or a verbatim, never-relabeled
``mrr.domain.model_adapter.TerminalStatus`` value other than ``"completed"``
(``"refused"``, ``"content_filtered"``, ``"error"``, ``"timed_out"``) --
never collapsed into one generic failure (AGENTS.md prohibited shortcut).
Nothing on this result expresses claim status, verification verdict, or
authoritative acceptance: the successful result is a schema-valid PROPOSAL
only, and downstream domain validation (out of this task's scope) must
still accept it before it is authoritative (MRR-FR-046).

--- The bounded repair loop -------------------------------------------------

``generate_structured`` calls ``adapter.invoke`` at most ``1 +
max_repair_attempts`` times and STOPS as soon as either (a) a schema-valid
response is produced, or (b) the underlying outcome's status is not
``"completed"`` -- a refusal, content filter, error, or timeout is a
DISTINCT failure this layer surfaces as itself and never attempts to
"repair" (a validation-error-shaped repair prompt cannot fix a call the
model declined or that errored/timed out before producing any candidate).
Only a schema-invalid ``"completed"`` response consumes a repair attempt.
The loop is a plain bounded ``for`` loop over ``range(max_repair_attempts +
1)`` -- there is no unbounded retry path anywhere in this module.

--- Redaction and what this layer can validate ------------------------------

This module reuses ``ModelInvocationRequest``/``ModelInvocationOutcome``
exactly as E4-T01 defined them and does not alter the port's own redaction
enforcement in any way: every underlying request is sent with the SAME
``redaction_policy`` the caller supplied on their original request (repair
requests are built via ``dataclasses.replace(..., prompt_text=...)``, never
touching ``redaction_policy``), and every recorded ``ModelInvocationOutcome``
in the returned audit trail is exactly what ``adapter.invoke`` returned for
that call -- never edited, replaced, or stripped after the fact. Under the
DEFAULT policy (``"hashes_only"``), ``ModelInvocationOutcome.__post_init__``
already forbids any conforming adapter from returning raw prompt/response
text (E4-T01), so no recorded outcome in this layer's audit trail EVER
carries raw text under that policy -- this holds structurally, with no
special-casing needed here.

A direct, documented consequence: content-based structural validation
against the caller's Pydantic model requires the actual candidate text, which
is only present on ``ModelInvocationOutcome.raw_response_text`` when the
request's redaction policy is ``"raw_permitted"`` AND the adapter chooses to
populate it. When a ``"completed"`` outcome carries no response text (either
because the policy is ``"hashes_only"``, or because a conforming adapter
simply did not populate it), this layer cannot confirm validity from a hash
alone -- consistent with "no invalid output passes", it treats this exactly
like a schema-validation failure (never inferring success from an
unobservable candidate) and records why in ``validation_errors``. A caller
that wants this layer to actually produce proposals must therefore supply a
request with ``redaction_policy="raw_permitted"``; this is flagged as an
open specification question in this task's pull request, since MRR-FR-045's
safe default and this layer's need to inspect candidate content are in
tension, and resolving that tension (e.g. a future task teaching this layer
or the executor to momentarily inspect raw text in-process without ever
returning or persisting it) is left to a subsequent task.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Literal

from mrr.domain.model_adapter import (
    ModelAdapter,
    ModelInvocationOutcome,
    ModelInvocationRequest,
    TerminalStatus,
)
from pydantic import BaseModel, ValidationError

#: This layer's own failure kind, distinct from every ``TerminalStatus``
#: value (task-packets/E4-T02.yaml invariant "distinct failure taxonomy"):
#: every repair attempt was exhausted without a schema-valid response.
_SCHEMA_INVALID: Literal["schema_invalid"] = "schema_invalid"

#: The closed status vocabulary of a ``StructuredGenerationResult``:
#: ``"proposal"`` (success) or one of the five DISTINCT failure kinds --
#: this layer's own ``"schema_invalid"``, or a verbatim, never-relabeled
#: ``TerminalStatus`` value other than ``"completed"``. Never collapsed into
#: one generic failure (AGENTS.md prohibited shortcut).
StructuredGenerationStatus = Literal[
    "proposal", "schema_invalid", "refused", "content_filtered", "error", "timed_out"
]


@dataclass(frozen=True, slots=True, kw_only=True)
class StructuredGenerationResult[TargetModelT: BaseModel]:
    """The result of one ``generate_structured`` call: a schema-valid
    PROPOSAL (A1 "clearly marked proposal", docs/spec/01_SYSTEM_SPEC.md
    section 5) or an explicit, distinct failure -- never both, and never
    invalid/partial/coerced output mistaken for either (MRR-FR-046).

    ``proposal`` is present if and only if ``status == "proposal"`` --
    structurally enforced in ``__post_init__``, mirroring ``mrr.domain.
    model_adapter.ModelInvocationOutcome``'s own ``response_hash``<->
    ``status`` biconditional. Nothing on this object expresses claim status,
    verification verdict, or authoritative acceptance (AGENTS.md rule 7):
    domain acceptance and verification are downstream, out of this task's
    scope.

    ``attempts`` is the ORDERED audit trail: one ``ModelInvocationOutcome``
    per underlying ``adapter.invoke`` call, initial call first, in the exact
    order they were made. ``repair_attempts_used`` is always exactly
    ``len(attempts) - 1`` (also structurally enforced) and never exceeds the
    ``max_repair_attempts`` the caller passed to ``generate_structured``.

    ``validation_errors`` carries one entry per schema-invalid or
    unvalidatable ``"completed"`` attempt, in the same order as ``attempts``
    (a successful proposal's own attempt contributes no entry). It is always
    empty when ``status == "proposal"``.
    """

    status: StructuredGenerationStatus
    proposal: TargetModelT | None
    attempts: tuple[ModelInvocationOutcome, ...]
    repair_attempts_used: int
    validation_errors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status == "proposal" and self.proposal is None:
            raise ValueError("status 'proposal' must carry a parsed proposal")
        if self.status != "proposal" and self.proposal is not None:
            raise ValueError(
                f"status {self.status!r} must not carry a proposal "
                "(only 'proposal' status expresses a schema-valid result)"
            )
        if self.repair_attempts_used < 0:
            raise ValueError(f"repair_attempts_used must be >= 0, got {self.repair_attempts_used}")
        if not self.attempts:
            raise ValueError("attempts must record at least the initial call")
        if len(self.attempts) != 1 + self.repair_attempts_used:
            raise ValueError(
                f"attempts has {len(self.attempts)} entries but repair_attempts_used is "
                f"{self.repair_attempts_used} -- expected exactly {1 + self.repair_attempts_used}"
            )
        if self.status == "proposal" and self.validation_errors:
            raise ValueError("status 'proposal' must not carry validation_errors")


def generate_structured[TargetModelT: BaseModel](
    adapter: ModelAdapter,
    request: ModelInvocationRequest,
    target_model: type[TargetModelT],
    *,
    max_repair_attempts: int,
) -> StructuredGenerationResult[TargetModelT]:
    """Obtain a ``target_model``-valid proposal from ``adapter``, repairing
    schema-invalid responses up to ``max_repair_attempts`` additional times.

    Calls ``adapter.invoke`` at least once and at most ``1 +
    max_repair_attempts`` times total (task-packets/E4-T02.yaml invariant
    "bounded repair"). Stops immediately -- consuming no further repair
    attempts -- as soon as the underlying outcome's ``status`` is not
    ``"completed"`` (a refusal/content-filter/error/timeout is a distinct
    failure this layer surfaces as itself, never relabeled or "repaired";
    see this module's docstring) or as soon as a ``"completed"`` response
    validates against ``target_model``.

    Args:
        adapter: the injected, provider-neutral ``ModelAdapter`` port
            (E4-T01). This is the SOLE channel through which this function
            ever reaches a model -- no provider SDK or network call is made
            directly by this module.
        request: the initial call's ``ModelInvocationRequest``. Repair
            requests are derived from it via ``dataclasses.replace`` with
            only ``prompt_text`` changed -- ``model_profile_id``,
            ``model_profile_hash``, ``operation_kind``, ``redaction_policy``,
            and ``tool_names_available`` are identical across every
            underlying call.
        target_model: the caller-supplied Pydantic v2 model a candidate
            response must validate against to become a proposal.
        max_repair_attempts: an explicit, non-negative bound on the number of
            ADDITIONAL calls made after the first. ``0`` means "one call,
            no repair" -- a single schema-invalid response is an immediate,
            explicit failure with no follow-up call.

    Returns:
        A ``StructuredGenerationResult`` carrying either a schema-valid
        proposal (``status == "proposal"``) or an explicit, distinct failure,
        plus the ordered audit trail of every underlying call made.

    Raises:
        ValueError: if ``max_repair_attempts`` is negative.
    """
    if max_repair_attempts < 0:
        raise ValueError(f"max_repair_attempts must be >= 0, got {max_repair_attempts}")

    attempts: list[ModelInvocationOutcome] = []
    validation_errors: list[str] = []
    current_request = request

    for attempt_index in range(max_repair_attempts + 1):
        outcome = adapter.invoke(current_request)
        attempts.append(outcome)

        if outcome.status != "completed":
            return StructuredGenerationResult(
                status=_as_failure_status(outcome.status),
                proposal=None,
                attempts=tuple(attempts),
                repair_attempts_used=attempt_index,
                validation_errors=tuple(validation_errors),
            )

        candidate_text = outcome.raw_response_text
        if candidate_text is not None:
            try:
                proposal = target_model.model_validate_json(candidate_text)
            except ValidationError as exc:
                validation_errors.append(str(exc))
            else:
                return StructuredGenerationResult(
                    status="proposal",
                    proposal=proposal,
                    attempts=tuple(attempts),
                    repair_attempts_used=attempt_index,
                    validation_errors=(),
                )
        else:
            # A "completed" outcome with no observable response text (the
            # request's redaction_policy is "hashes_only", or the adapter
            # simply did not populate it under "raw_permitted"). Never infer
            # validity from a hash alone -- treat this exactly like a
            # schema-invalid attempt, per this module's docstring.
            validation_errors.append(
                "no response text available to validate: this request's "
                f"redaction_policy is {current_request.redaction_policy!r}"
            )
            candidate_text = None

        if attempt_index < max_repair_attempts:
            current_request = replace(
                current_request,
                prompt_text=_build_repair_prompt(
                    original_prompt_text=request.prompt_text,
                    target_model=target_model,
                    previous_response_text=candidate_text,
                    validation_error=validation_errors[-1],
                ),
            )

    return StructuredGenerationResult(
        status=_SCHEMA_INVALID,
        proposal=None,
        attempts=tuple(attempts),
        repair_attempts_used=max_repair_attempts,
        validation_errors=tuple(validation_errors),
    )


def _as_failure_status(
    status: TerminalStatus,
) -> Literal["refused", "content_filtered", "error", "timed_out"]:
    """Narrow a non-``"completed"`` ``TerminalStatus`` to this module's own
    failure-status vocabulary, verbatim -- never relabeled (task-packets/
    E4-T02.yaml invariant "distinct failure taxonomy"). ``status`` is
    guaranteed not to be ``"completed"`` by every call site.
    """
    if status == "completed":  # pragma: no cover -- guarded by every call site
        raise ValueError("_as_failure_status must not be called with status='completed'")
    return status


def _build_repair_prompt(
    *,
    original_prompt_text: str,
    target_model: type[BaseModel],
    previous_response_text: str | None,
    validation_error: str,
) -> str:
    """Build the next repair attempt's prompt inline from the target
    model's JSON schema and the most recent validation error
    (task-packets/E4-T02.yaml derived_decisions: "The repair prompt is built
    inline from the schema and the validation error; the versioned
    named-prompt registry is a separate task (E4-T06)").

    Always anchors on ``original_prompt_text`` (never a previously built
    repair prompt) so repair prompts do not compound across attempts --
    each repair restates the original task plus the single most recent
    failure, keeping prompt size bounded regardless of
    ``max_repair_attempts``.
    """
    schema_json = json.dumps(target_model.model_json_schema(), sort_keys=True)
    previous_response_section = (
        previous_response_text
        if previous_response_text is not None
        else "(no response text was available to include)"
    )
    return (
        f"{original_prompt_text}\n\n"
        "Your previous response did not satisfy the required JSON schema.\n\n"
        f"Required JSON schema:\n{schema_json}\n\n"
        f"Your previous response was:\n{previous_response_section}\n\n"
        f"Validation error:\n{validation_error}\n\n"
        "Return ONLY a JSON object that satisfies the schema above -- no explanation, "
        "no markdown formatting, no additional text."
    )


__all__ = [
    "StructuredGenerationResult",
    "StructuredGenerationStatus",
    "generate_structured",
]
