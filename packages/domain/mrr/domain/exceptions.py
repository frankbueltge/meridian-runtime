"""Exception hierarchy for mrr.domain identity and hashing-policy primitives."""

from __future__ import annotations


class DomainError(Exception):
    """Base class for all mrr.domain errors."""


class InvalidEntityError(DomainError):
    """Raised when a URN entity segment does not match ``[a-z0-9-]+``."""


class InvalidUrnError(DomainError):
    """Raised when a value does not match the exact ``$defs.urn`` pattern in
    schemas/common.schema.json (``^urn:mrr:[a-z0-9-]+:[0-9A-HJKMNP-TV-Z]{26}$``).
    """
