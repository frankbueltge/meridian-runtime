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
- ``mrr.services.release.supersede`` (task-packets/E8-T05.yaml) —
  ``create_and_supersede``/``resolve_release_status``: the CLI-facing
  composition functions for ``mrr release supersede``/``mrr release
  status``, keeping ``mrr.services.cli.release_main`` free of any direct
  ``mrr.services.release.service``/``mrr.domain.release_status`` import,
  exactly mirroring ``mrr.services.release.bundle``'s own identical
  boundary for ``mrr release create``.

``ReleaseService`` itself (``mrr.services.release.service``) additionally
gains ``supersede``/``status`` (task-packets/E8-T05.yaml R1/R2) — the
``released -> superseded`` lifecycle driver
(``mrr.domain.lifecycles.RELEASE_RECORD_LIFECYCLE``'s own reserved edge) and
the read-only banner-status resolution over the pure
``mrr.domain.release_status.compute_release_banner``.

The CLI (``mrr release create`` / ``verify`` / ``supersede`` / ``status``) is
``mrr.services.cli.release_main``, a sibling module outside this package,
mirroring every other CLI module's own "thin transport, no domain behavior"
placement in this codebase.
"""

from __future__ import annotations
