"""Shared hypothesis strategies for arbitrary JSON-safe payloads (E1-T02).

Not a test module itself (no ``test_`` prefix, not collected by pytest); it
exists so the property tests in this directory can share one definition of
"arbitrary JSON-safe payload" instead of duplicating it.

Generated values stay within what ``mrr.crypto.canonical.canonicalize``
(RFC 8785 / JCS) can represent without raising ``CanonicalizationError``:
finite floats only (JCS forbids NaN/Infinity), integers within the IEEE-754
double-precision safe range, and text restricted to the UTF-8-encodable
codec (JCS strings are re-encoded as UTF-8 and reject lone surrogates).
"""

from __future__ import annotations

from hypothesis import strategies as st
from mrr.crypto.canonical import JSONValue

#: Integers outside this range trip rfc8785's `IntegerDomainError` (they
#: exceed what an IEEE-754 double can represent exactly), which is a
#: canonicalization failure mode this module intentionally does not exercise
#: here — it is not part of the map-order/semantic-mutation/round-trip
#: properties under test.
_SAFE_INT_MIN = -(2**53 - 1)
_SAFE_INT_MAX = 2**53 - 1


def json_text(*, min_size: int = 0) -> st.SearchStrategy[str]:
    """Arbitrary text restricted to the UTF-8-encodable codec, matching what
    JCS string serialization accepts (it rejects lone surrogates).
    """
    return st.text(alphabet=st.characters(codec="utf-8"), min_size=min_size)


_json_scalars: st.SearchStrategy[JSONValue] = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=_SAFE_INT_MIN, max_value=_SAFE_INT_MAX),
    st.floats(allow_nan=False, allow_infinity=False, width=32),
    json_text(),
)


def json_values(*, max_leaves: int = 20) -> st.SearchStrategy[JSONValue]:
    """Arbitrary JSON-safe scalars, lists, and string-keyed objects, nested to
    a bounded depth/size so example generation and shrinking stay fast.
    """
    return st.recursive(
        _json_scalars,
        lambda children: st.one_of(
            st.lists(children, max_size=5),
            st.dictionaries(json_text(), children, max_size=5),
        ),
        max_leaves=max_leaves,
    )


def json_objects(
    *, min_size: int = 1, max_size: int = 8
) -> st.SearchStrategy[dict[str, JSONValue]]:
    """Arbitrary JSON-safe objects (what a canonicalized/hashed/signed MRR
    payload looks like at the top level).
    """
    return st.dictionaries(json_text(), json_values(), min_size=min_size, max_size=max_size)
