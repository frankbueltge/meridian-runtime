"""The numeric verifier tool (task-packets/E4-T05.yaml, MRR-FR-073: "Numeric
verification MUST recompute the value or explicitly record why recomputation
is impossible").

--- A CLOSED, safe operation set — never eval/exec/compile ------------------

:func:`recompute_numeric_claim` recomputes a claimed value from named inputs
and a caller-DECLARED operation name (plain ``str`` — deliberately not typed
as a ``Literal`` here, since an unrecognized or malicious operation name is
an expected, handled input, not a programmer error to reject at the type
level). The operation is looked up in :data:`NUMERIC_OPERATIONS` — a fixed,
closed, hand-written dispatch table of eight named arithmetic functions,
chosen to cover every docs/spec/05_EVALUATION_AND_ACCEPTANCE.md MB-NUM case
(numerator/denominator swap, percentage-vs-percentage-point confusion, unit
conversion, a recomputable analysis with a known output). An operation name
outside this closed set — or a name for which a REQUIRED named input is
missing — yields an explicit ``NumericRecomputation.impossible_reason``
(never a silent pass, never a fallback to ``eval``/``exec``/``compile`` of
any kind: this module contains none of those three calls anywhere, and never
will — see ``tests/unit/architecture/test_verifier_determinism_boundary.py``
for the machine-checked form of this guarantee alongside the no-model/
no-network invariant).

--- ``ratio``/``quotient`` and ``difference``/``percentage_point`` are each a
    deliberately duplicated pair, not four independent formulas -------------

``quotient`` (``dividend``/``divisor``) and ``ratio`` (``numerator``/
``denominator``) both compute a plain division — same arithmetic, different
named inputs — so a claim phrased either way (a "quotient of X and Y" or "the
ratio of X to Y") has a natural, self-documenting operation name to declare,
without this module inventing two different division algorithms that do not
exist. Likewise ``difference`` (``minuend``/``subtrahend``) and
``percentage_point`` (``value_a``/``value_b``) both compute a plain
subtraction; ``percentage_point`` exists as its own named operation
specifically so a caller can declare "this claim's actual arithmetic is a
plain percentage-POINT difference between two already-percentage figures"
distinctly from ``percentage`` itself (a ratio, times 100) — this is exactly
the MB-NUM "percentage vs percentage-point confusion" case: if a claim
asserts a value that was actually computed as a relative percentage change
but the correct interpretation is a percentage-point difference (or vice
versa), declaring the CORRECT operation here and recomputing against it
yields a mismatch, without this module having to guess which interpretation
the claim intended — the caller (the orchestrator's caller) declares that
intent by naming the operation.

--- Exactness: ``decimal.Decimal`` throughout, never ``float`` --------------

Every input, the claimed value, and the optional comparison tolerance are
parsed to ``decimal.Decimal`` via :func:`_parse_decimal`, which explicitly
REJECTS a Python ``float`` argument (raising the same internal
"impossible" signal as any other malformed input) rather than silently
converting one — a caller must supply an exact ``str``/``int``/
``decimal.Decimal`` representation of every number itself, so this module
never has to guess how much of a float's binary representation was
"intended". All arithmetic runs inside a ``decimal.localcontext()`` with a
raised precision (50 significant digits) scoped to the single call — never
mutating the process-global decimal context other code might depend on.

--- Comparison: exact by default, an explicit tolerance is opt-in ----------

``matches_claimed_value`` is ``abs(recomputed - claimed) <= tolerance``,
where ``tolerance`` defaults to ``Decimal(0)`` (exact equality) unless the
caller explicitly supplies one (e.g. to accommodate a claim that legitimately
rounds a recomputed figure to fewer significant digits). This module never
invents its own default nonzero tolerance — MRR-FR-073's own acceptance
target ("numeric verification accuracy >= 0.95") says nothing about how much
rounding slack is legitimate, and guessing one here would be exactly the
kind of invented domain behavior AGENTS.md rule 3 forbids. Flagged as an
open specification question in this task's PR body.
"""

from __future__ import annotations

import decimal
from collections.abc import Callable, Mapping
from decimal import Decimal, InvalidOperation

from mrr.contracts.verification_result import NumericRecomputation

#: A number as a caller may supply it to this module: an exact string
#: representation, a plain integer, or an already-constructed
#: ``decimal.Decimal`` — deliberately NEVER a ``float`` (see the module
#: docstring's "Exactness" section; a ``float`` argument is rejected at
#: runtime by :func:`_parse_decimal`, not merely discouraged by the type
#: hint).
NumberLike = str | int | Decimal

#: The precision (significant digits) every recomputation runs at, via a
#: call-scoped ``decimal.localcontext()`` — ample for any MB-NUM fixture and
#: far beyond the default 28-digit context, without ever mutating the
#: process-global decimal context.
_DECIMAL_PRECISION = 50


class _NumericInputError(Exception):
    """Internal-only signal: recomputation is impossible for a plain,
    statable reason (unknown operation, missing/malformed/non-finite input,
    division by zero, a negative tolerance). Always caught by
    :func:`recompute_numeric_claim` and turned into an explicit
    ``NumericRecomputation.impossible_reason`` string — never leaks out of
    this module, and never triggers any ``eval``/``exec``/``compile``
    fallback (this module contains none).
    """


def _parse_decimal(value: NumberLike, *, label: str) -> Decimal:
    """Parse ``value`` to an exact, finite ``Decimal``, or raise
    :class:`_NumericInputError` naming ``label`` (the input's own name, or
    ``"claimed_value"``/``"tolerance"``) in the message.

    Rejects a ``bool`` (a ``bool`` is a ``int`` subclass in Python, and
    ``True``/``False`` are never a meaningful numeric input here), a
    ``float`` (see the module docstring's "Exactness" section), a
    non-finite ``Decimal``/parsed string (``Infinity``/``NaN`` — RFC 8785
    canonical JSON cannot represent either, and neither is a meaningful
    recomputed value), and a string that does not parse as a decimal number
    at all.
    """
    if isinstance(value, bool):
        raise _NumericInputError(f"{label} must be a number, not a bool: {value!r}")
    if isinstance(value, float):
        raise _NumericInputError(
            f"{label} was supplied as a float ({value!r}) — numeric recomputation requires "
            "an exact str/int/decimal.Decimal input to avoid float drift; convert it to its "
            "exact string representation first"
        )
    if isinstance(value, Decimal):
        parsed = value
    elif isinstance(value, int):
        parsed = Decimal(value)
    elif isinstance(value, str):
        try:
            parsed = Decimal(value)
        except InvalidOperation as exc:
            raise _NumericInputError(f"{label} could not be parsed as a number: {value!r}") from exc
    else:
        raise _NumericInputError(
            f"{label} has an unsupported type {type(value).__name__!r}: {value!r}"
        )
    if not parsed.is_finite():
        raise _NumericInputError(f"{label} is not a finite number: {value!r}")
    return parsed


def _require(inputs: Mapping[str, NumberLike], name: str, *, operation: str) -> NumberLike:
    if name not in inputs:
        raise _NumericInputError(
            f"operation {operation!r} requires a named input {name!r}, which was not supplied "
            f"(inputs supplied: {sorted(inputs)!r})"
        )
    return inputs[name]


def _op_sum(inputs: Mapping[str, NumberLike]) -> Decimal:
    """Sum of every supplied named input, in whatever order ``inputs``
    iterates (addition is commutative and associative, so order never
    changes the result). Requires at least one input.
    """
    if not inputs:
        raise _NumericInputError("operation 'sum' requires at least one named input")
    total = Decimal(0)
    for name, value in inputs.items():
        total += _parse_decimal(value, label=name)
    return total


def _op_product(inputs: Mapping[str, NumberLike]) -> Decimal:
    """Product of every supplied named input. Requires at least one input."""
    if not inputs:
        raise _NumericInputError("operation 'product' requires at least one named input")
    total = Decimal(1)
    for name, value in inputs.items():
        total *= _parse_decimal(value, label=name)
    return total


def _op_difference(inputs: Mapping[str, NumberLike]) -> Decimal:
    """``minuend - subtrahend``."""
    minuend = _parse_decimal(_require(inputs, "minuend", operation="difference"), label="minuend")
    subtrahend = _parse_decimal(
        _require(inputs, "subtrahend", operation="difference"), label="subtrahend"
    )
    return minuend - subtrahend


def _op_quotient(inputs: Mapping[str, NumberLike]) -> Decimal:
    """``dividend / divisor``. A zero ``divisor`` is impossible, not a
    mismatch — there is no recomputed value to compare against the claim.
    """
    dividend = _parse_decimal(_require(inputs, "dividend", operation="quotient"), label="dividend")
    divisor = _parse_decimal(_require(inputs, "divisor", operation="quotient"), label="divisor")
    if divisor == 0:
        raise _NumericInputError("operation 'quotient' cannot divide by a zero 'divisor'")
    return dividend / divisor


def _op_ratio(inputs: Mapping[str, NumberLike]) -> Decimal:
    """``numerator / denominator`` — see the module docstring for why this
    is deliberately the same arithmetic as ``quotient`` under different
    named inputs. A zero ``denominator`` is impossible, not a mismatch.
    """
    numerator = _parse_decimal(_require(inputs, "numerator", operation="ratio"), label="numerator")
    denominator = _parse_decimal(
        _require(inputs, "denominator", operation="ratio"), label="denominator"
    )
    if denominator == 0:
        raise _NumericInputError("operation 'ratio' cannot divide by a zero 'denominator'")
    return numerator / denominator


def _op_percentage(inputs: Mapping[str, NumberLike]) -> Decimal:
    """``(part / whole) * 100``. A zero ``whole`` is impossible, not a
    mismatch.
    """
    part = _parse_decimal(_require(inputs, "part", operation="percentage"), label="part")
    whole = _parse_decimal(_require(inputs, "whole", operation="percentage"), label="whole")
    if whole == 0:
        raise _NumericInputError("operation 'percentage' cannot divide by a zero 'whole'")
    return (part / whole) * Decimal(100)


def _op_percentage_point(inputs: Mapping[str, NumberLike]) -> Decimal:
    """``value_a - value_b`` — see the module docstring for why this is
    deliberately the same arithmetic as ``difference`` under named inputs
    that state the claim is about two percentage FIGURES, not two arbitrary
    quantities (the MB-NUM "percentage vs percentage-point confusion" case).
    """
    value_a = _parse_decimal(
        _require(inputs, "value_a", operation="percentage_point"), label="value_a"
    )
    value_b = _parse_decimal(
        _require(inputs, "value_b", operation="percentage_point"), label="value_b"
    )
    return value_a - value_b


def _op_unit_conversion(inputs: Mapping[str, NumberLike]) -> Decimal:
    """``value * factor + offset`` — a multiplicative conversion (e.g.
    kilometers to miles, ``factor`` alone) or an affine one (e.g. Celsius to
    Fahrenheit, ``factor`` and a nonzero ``offset``). ``offset`` is the only
    OPTIONAL named input anywhere in this closed operation set; it defaults
    to ``0`` when not supplied.
    """
    value = _parse_decimal(_require(inputs, "value", operation="unit_conversion"), label="value")
    factor = _parse_decimal(_require(inputs, "factor", operation="unit_conversion"), label="factor")
    offset = _parse_decimal(inputs.get("offset", 0), label="offset")
    return value * factor + offset


#: The CLOSED, safe operation dispatch table (task-packets/E4-T05.yaml
#: derived_decisions' own "e.g." list, implemented exactly, with none added
#: beyond it). Iteration order is this fixed declaration order, which is
#: also :data:`NUMERIC_OPERATIONS`'s own order — never eval/exec/compile of
#: caller or model input.
_OPERATIONS: dict[str, Callable[[Mapping[str, NumberLike]], Decimal]] = {
    "sum": _op_sum,
    "difference": _op_difference,
    "product": _op_product,
    "quotient": _op_quotient,
    "ratio": _op_ratio,
    "percentage": _op_percentage,
    "percentage_point": _op_percentage_point,
    "unit_conversion": _op_unit_conversion,
}

#: The closed set of operation names :func:`recompute_numeric_claim` accepts
#: — exposed so a caller (or the orchestrator, or a test) can enumerate or
#: validate against it directly without reaching into this module's private
#: dispatch table.
NUMERIC_OPERATIONS: tuple[str, ...] = tuple(_OPERATIONS)


def recompute_numeric_claim(
    *,
    operation: str,
    claimed_value: NumberLike,
    inputs: Mapping[str, NumberLike],
    tolerance: NumberLike | None = None,
    method: str | None = None,
) -> NumericRecomputation:
    """Recompute ``claimed_value`` from ``inputs`` via the named
    ``operation``, producing a schema-valid
    ``mrr.contracts.verification_result.NumericRecomputation`` — MRR-FR-073's
    invariant, enforced by that contract's own ``model_validator``: exactly
    one of (``recomputed_value`` and ``matches_claimed_value``) or
    ``impossible_reason`` is ever set, never neither, never both.

    ``impossible_reason`` (never a raised exception, and never a silent
    pass) is set for exactly three reasons, all handled uniformly:

    - ``operation`` is not one of :data:`NUMERIC_OPERATIONS`;
    - a named input the declared operation requires is missing from
      ``inputs``, or is not itself a well-formed, finite number (or is a
      ``float`` — see the module docstring);
    - the declared operation is arithmetically undefined for the supplied
      inputs (division by a zero divisor/denominator/whole), or ``tolerance``
      is negative.

    Otherwise, ``recomputed_value`` is set to the EXACT decimal string of the
    recomputed result (never a ``float`` — see the module docstring's
    "Exactness" section) and ``matches_claimed_value`` is
    ``abs(recomputed - claimed) <= tolerance`` (``tolerance`` defaults to
    exact equality, ``Decimal(0)``).

    This function makes no model call, opens no network connection, and
    contains no ``eval``/``exec``/``compile`` of any kind — the closed
    dispatch table above is the only way ``operation`` is ever turned into
    arithmetic.
    """
    op_fn = _OPERATIONS.get(operation)
    if op_fn is None:
        return NumericRecomputation(
            impossible_reason=(
                f"unknown numeric operation {operation!r}; the closed operation set is "
                f"{NUMERIC_OPERATIONS!r} — recomputation never falls back to eval/exec"
            ),
            method=method,
        )

    with decimal.localcontext() as ctx:
        ctx.prec = _DECIMAL_PRECISION
        try:
            claimed_decimal = _parse_decimal(claimed_value, label="claimed_value")
            recomputed_decimal = op_fn(inputs)
            tolerance_decimal = (
                Decimal(0) if tolerance is None else _parse_decimal(tolerance, label="tolerance")
            )
        except _NumericInputError as exc:
            return NumericRecomputation(impossible_reason=str(exc), method=method)

        if tolerance_decimal < 0:
            return NumericRecomputation(
                impossible_reason=f"tolerance must be >= 0, got {tolerance_decimal}",
                method=method,
            )

        matches = abs(recomputed_decimal - claimed_decimal) <= tolerance_decimal
        return NumericRecomputation(
            recomputed_value=str(recomputed_decimal),
            matches_claimed_value=matches,
            method=method or f"recomputed via operation {operation!r} over decimal.Decimal",
        )


__all__ = [
    "NUMERIC_OPERATIONS",
    "NumberLike",
    "recompute_numeric_claim",
]
