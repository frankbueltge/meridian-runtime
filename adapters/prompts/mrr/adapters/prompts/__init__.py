"""Git-backed prompt/version registry (task-packets/E4-T06.yaml).

``mrr.adapters.prompts.registry`` is the first module under this root — it
resolves a named, versioned prompt template committed under the top-level
``prompts/`` directory to its exact bytes, content hash, kind (``system`` |
``task``), and declared variables, and renders it deterministically by
filling exactly those variables. Every ``(name, version)`` is immutable: a
change to a prompt is committed as a NEW version file, never an edit in
place.

This root is registered in the same import-linter "framework- and
provider-free" contract (MRR-NFR-004, MRR-NFR-010, pyproject.toml) as
``mrr.adapters.object_store`` and ``mrr.adapters.llm`` — safe here because
this package does nothing but read committed local files and fill text
templates: it imports no provider SDK, no network client, and (unlike
``mrr.adapters.llm``) no model adapter either, since it does not call a
model at all. It reuses ``mrr.crypto.hashing.content_hash`` for hashing
(never a new hash) and does not import ``mrr.adapters.llm`` or
``mrr.domain.model_adapter`` — this package PROVIDES a store of named
prompt bodies; wiring the planner, skeptic, or the structured-generation
layer onto it is a later, separate integration task (task-packets/
E4-T06.yaml forbidden_changes).
"""
