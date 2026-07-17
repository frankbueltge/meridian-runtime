"""Canonical MRR object identifiers per docs/spec/02_DOMAIN_MODEL.md section
1.1 and the ``urn`` definition in schemas/common.schema.json:

    urn:mrr:<entity>:<ulid>

Identifiers never change (docs/spec/02_DOMAIN_MODEL.md section 1.1;
task-packet invariant "identifiers are stable and not derived from mutable
labels"): ``new_urn`` always mints a fresh random ULID suffix and takes no
label or other mutable input, so an identifier can never be recomputed from,
or accidentally tied to, data that later changes. Revisions of an object get
a new object record (see ``supersedes`` in schemas/common.schema.json), not a
new identifier for the same record.
"""

from __future__ import annotations

import re

import ulid
from mrr.domain.exceptions import InvalidEntityError, InvalidUrnError

#: Matches a single URN entity segment, e.g. "claim" or "evidence-crate".
_ENTITY_PATTERN = re.compile(r"^[a-z0-9-]+$")

#: Matches the exact `$defs.urn` pattern in schemas/common.schema.json.
URN_PATTERN = re.compile(r"^urn:mrr:(?P<entity>[a-z0-9-]+):(?P<ulid>[0-9A-HJKMNP-TV-Z]{26})$")


def new_urn(entity: str) -> str:
    """Mint a new ``urn:mrr:<entity>:<ulid>`` identifier.

    Args:
        entity: the URN entity segment, e.g. "claim". Must match
            ``[a-z0-9-]+``.

    Raises:
        InvalidEntityError: if ``entity`` does not match ``[a-z0-9-]+``.
    """
    _require_valid_entity(entity)
    return f"urn:mrr:{entity}:{ulid.ULID()}"


def is_valid_urn(value: str) -> bool:
    """Return ``True`` if ``value`` matches the exact ``$defs.urn`` pattern."""
    return URN_PATTERN.match(value) is not None


def parse_urn(value: str) -> tuple[str, str]:
    """Validate ``value`` against the ``$defs.urn`` pattern and return its
    ``(entity, ulid)`` parts.

    Raises:
        InvalidUrnError: if ``value`` does not match the pattern (wrong
            scheme, invalid entity segment, or a malformed ULID suffix —
            wrong length, characters outside the reduced Crockford32
            alphabet, or lowercase).
    """
    match = URN_PATTERN.match(value)
    if match is None:
        raise InvalidUrnError(f"not a valid MRR urn: {value!r}")
    return match.group("entity"), match.group("ulid")


def _require_valid_entity(entity: str) -> None:
    if _ENTITY_PATTERN.match(entity) is None:
        raise InvalidEntityError(f"invalid urn entity segment: {entity!r}")
