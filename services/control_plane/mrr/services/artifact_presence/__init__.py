"""The Artifact Presence application service (task-packets/A2-T01.yaml,
"Teil 2 — Nachsehen"): ``ArtifactPresenceService`` — a read-only,
NO-NETWORK, NO-DATABASE service that reads ONE already-committed archive
dump, derives the recorded artifact-store root from the RunManifest(s) it
declares, and checks, for every EvidenceAnchor with a ``snapshot_hash``,
whether the expected content-addressed blob is present at the derived path
with a matching hash.

Like ``mrr.services.citation_audit``/``mrr.services.anchoring_integrity``/
``mrr.services.support_audit``, this service opens no database connection
and constructs no repository anywhere, and never imports ``sqlalchemy`` —
see ``mrr.services.artifact_presence.service`` for the full design
rationale, above all why it never constructs a
``LocalFilesystemArtifactStore`` (whose constructor writes to disk as a
side effect — a read-only audit must never do that).
"""

from __future__ import annotations
