"""SHA-256 content hashing per docs/spec/02_DOMAIN_MODEL.md section 1.2 and the
``sha256`` format defined in schemas/common.schema.json
(``^sha256:[a-f0-9]{64}$``).

This module hashes raw bytes only; it has no opinion about which fields of an
object should be hashed. Field-selection policy (excluding ``content_hash``
and ``signature``/``signatures`` before hashing) lives in
``mrr.domain.hashing_policy``, which composes ``mrr.crypto.canonical`` and
this module.
"""

from __future__ import annotations

import hashlib
import re

from mrr.crypto.exceptions import ContentHashMismatchError

#: Matches the exact ``$defs.sha256`` pattern in schemas/common.schema.json.
SHA256_PATTERN = re.compile(r"^sha256:[a-f0-9]{64}$")

_PREFIX = "sha256:"


def content_hash(data: bytes) -> str:
    """Return the ``sha256:<64 lowercase hex>`` content hash of ``data``."""
    return f"{_PREFIX}{hashlib.sha256(data).hexdigest()}"


def verify_content_hash(data: bytes, expected: str) -> None:
    """Verify that ``data`` hashes to ``expected``.

    Raises:
        ContentHashMismatchError: if the SHA-256 hash of ``data`` does not
            equal ``expected``. There is no boolean-returning form: a
            mismatch always raises, so a caller cannot accidentally ignore
            a failed verification.
    """
    actual = content_hash(data)
    if actual != expected:
        raise ContentHashMismatchError(
            f"content hash mismatch: expected {expected!r}, computed {actual!r}"
        )
