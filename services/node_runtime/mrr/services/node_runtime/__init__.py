"""The Node Runtime execution boundary (task-packets/E2-T04.yaml):
``Executor``, the framework-free execution Protocol, and
``ReferenceTaskExecutor``, its deterministic reference implementation.

``services/node_runtime`` is a second, independent namespace root under
``services/`` (docs/spec/01_SYSTEM_SPEC.md section 7.5, "Node Runtime"),
alongside ``services/control_plane`` (E2-T01/T02/T03). Both roots merge into
the same ``mrr.services`` PEP 420 namespace package; nothing here shares a
directory with ``services/control_plane`` on disk, only the dotted import
path. The same import-linter "forbidden" contract that already stops
``mrr.domain``/``mrr.crypto``/``mrr.contracts``/``mrr.policy``/
``mrr.provenance``/``mrr.observability``/``mrr.persistence`` from importing
``mrr.services.*`` (E2-T01) covers this new leaf automatically, since it
names the ``mrr.services`` root, not a specific sub-service.

This module owns execution only: taking an already-accepted, signed
``TaskBundle`` (E2-T03) and producing an explicit terminal
``ExecutionResult`` (MRR-FR-043) for a bounded, deterministic reference task
(MRR-FR-044). It does not persist a ``RunManifest`` (MRR-FR-042, deferred to
E2-T05), seal an evidence crate (E2-T06), invoke a model (E4), or expose
HTTP — see ``mrr.services.node_runtime.executor`` for the full design and,
above all, its HONESTY BOUNDARY section: this reference implementation is
NOT an isolation boundary for untrusted code (MRR-FR-041 is the deferred
OCI-executor adapter's job).
"""

from __future__ import annotations
