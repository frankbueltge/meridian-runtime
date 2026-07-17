"""Exception hierarchy for mrr.crypto.

Every failure path in this package raises a specific, typed exception instead
of returning a bare boolean that a caller could silently ignore. In
particular, signature and hash verification never return ``False`` on
failure — they raise, so callers fail closed by default
(docs/spec/04_SECURITY_AND_POLICY.md section 8.2, "Replay and tampering";
AGENTS.md rule 9, "No cross-practice object may be accepted without
signature and hash verification").
"""

from __future__ import annotations


class CryptoError(Exception):
    """Base class for all mrr.crypto errors."""


class CanonicalizationError(CryptoError):
    """Raised when a payload cannot be serialized to RFC 8785 canonical JSON bytes."""


class UnsupportedAlgorithmError(CryptoError):
    """Raised when a signature or verification request names an algorithm that is
    not a member of ``mrr.crypto.signatures.SUPPORTED_ALGORITHMS``.

    This check happens before any cryptographic call is attempted, so an
    unsupported or malformed algorithm string fails closed rather than
    silently falling through to a default algorithm.
    """


class SignatureVerificationError(CryptoError):
    """Raised when a signature does not verify against the supplied payload and key,
    or when the signature value is not well-formed base64.
    """


class ContentHashMismatchError(CryptoError):
    """Raised when a computed content hash does not match an expected value."""
