"""Concrete implementations of ``mrr.domain.artifacts.ArtifactStore``
(task-packets/E1-T07.yaml).

``mrr.adapters.object_store.local`` is the first implementation — a local
filesystem store, stdlib only. This root is registered in the same
import-linter "framework- and provider-free" contract (MRR-NFR-010,
pyproject.toml) as the other core packages, which is safe today because
nothing under it imports a framework or an object-storage SDK. A future
MinIO/S3-compatible adapter would live under its own namespace root (e.g.
``adapters/object_store_s3/mrr/adapters/object_store_s3/``) and would need
its own contract treatment at that point, since it would legitimately need
an object-storage SDK the current contract forbids — that root is
deliberately not created here (out of scope per task-packets/E1-T07.yaml
``forbidden_changes``, "a remote object-storage SDK").
"""
