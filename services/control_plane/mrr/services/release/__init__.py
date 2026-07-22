"""``mrr.services.release`` (task-packets/E8-T04.yaml, docs/spec/adr/
ADR-0011-RELEASE-RECORD-AND-A4-APPROVAL-EVENT.md): the A4 publication-
approval gate — the ``ReleaseRecord`` object kind's service layer.

- ``mrr.services.release.errors`` — the typed refusals task-packets/
  E8-T04.yaml R2 names.
- ``mrr.services.release.manifest`` — the pure, deterministic bundle
  manifest / ``root_hash`` computation (ADR-0011 decision 1/3).
- ``mrr.services.release.service`` — ``ReleaseService.create``: the atomic
  revision-1 + ``release.approved`` event write (ADR-0011 decision 2, R2).
- ``mrr.services.release.bundle`` — ``assemble_and_release``: the R3 bundle-
  assembly function, composing ``mrr.services.export.service.ExportService``
  and ``mrr.services.report.service.ReportService`` by composition (zero new
  closure/render/redaction logic — reviewer_resolution (4)).
- ``mrr.services.release.verify`` — ``mrr release verify``'s two comparison
  modes (rebuild-from-archive, and an existing ``--bundle-dir``'s bytes).

The CLI (``mrr release create`` / ``mrr release verify``) is
``mrr.services.cli.release_main``, a sibling module outside this package,
mirroring every other CLI module's own "thin transport, no domain behavior"
placement in this codebase.
"""

from __future__ import annotations
