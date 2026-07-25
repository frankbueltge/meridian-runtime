"""Concrete transport for the E5-T06 offline store-and-forward path
(task-packets/E5-T08.yaml).

``mrr.adapters.federation.local`` is the first implementation — a local
filesystem transport, mirroring ``mrr.adapters.object_store.local``'s own
precedent (a small adapter class, typed errors, no global state, stdlib plus
this codebase's own ``mrr.contracts``/``mrr.crypto`` — no framework or
provider SDK). It writes/reads an already-built, already-signed
``mrr.contracts.offline_bundle.OfflineBundle`` to/from a file and implements
a file-backed replay ledger; it builds nothing, signs nothing, and evaluates
no trust — those remain ``mrr.domain.offline_bundle``'s job, reused UNCHANGED
by ``mrr.services.cli.federation_main``.

This root is registered in the same import-linter "framework- and
provider-free" contract precedent as ``mrr.adapters.object_store``/
``mrr.adapters.llm``/``mrr.adapters.prompts`` would be, and is additionally
checked by its own explicit AST-based boundary test
(``tests/unit/architecture/test_federation_boundary.py``) for the stricter,
packet-specific invariant this task adds: no socket, no TLS, no HTTP client
or server, no database — this transport is store-and-forward over a
committed file, never a live connection of any kind.
"""
