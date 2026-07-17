"""Deterministic canonical JSON bytes via RFC 8785 (the JSON Canonicalization
Scheme, JCS).

docs/spec/02_DOMAIN_MODEL.md section 1.2: "Content hashes are computed over
canonical JSON with signatures and non-semantic transport metadata excluded.
The implementation SHOULD use RFC 8785 canonicalization and SHA-256." This
module owns the canonicalization half; ``mrr.crypto.hashing`` owns SHA-256,
and ``mrr.domain.hashing_policy`` owns the field-selection policy (which
fields are excluded before canonicalization).

JCS canonicalization is deterministic in the sense this codebase relies on:
map key insertion order never changes the output bytes (object members are
re-sorted by UTF-16 code unit), and any semantic change to the payload
(added, removed, or changed key/value) changes the output bytes. Both
properties are asserted directly in tests/property/.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import rfc8785
from mrr.crypto.exceptions import CanonicalizationError

# A JSON-safe value as accepted by RFC 8785 canonicalization: the JSON
# scalars, plus arrays and objects built out of them. Exposed here so callers
# (mrr.domain.hashing_policy, tests) can type payloads precisely instead of
# reaching for `Any`.
JSONScalar = str | int | float | bool | None
JSONValue = JSONScalar | Sequence["JSONValue"] | Mapping[str, "JSONValue"]


def canonicalize(payload: JSONValue) -> bytes:
    """Serialize ``payload`` to RFC 8785 canonical JSON bytes.

    The returned bytes are exposed (not just a hash of them) so tests can
    assert directly on the deterministic canonical form, e.g. that two dicts
    built with different key insertion orders produce byte-identical output.

    Raises:
        CanonicalizationError: if ``payload`` contains a value RFC 8785
            cannot represent (e.g. a non-finite float, or an integer outside
            the IEEE-754 double-precision safe range). The underlying
            ``rfc8785`` exception is wrapped rather than leaked, so callers
            only need to catch ``mrr.crypto`` exception types.
    """
    try:
        return rfc8785.dumps(payload)
    except rfc8785.CanonicalizationError as exc:
        raise CanonicalizationError(str(exc)) from exc
