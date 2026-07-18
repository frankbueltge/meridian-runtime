"""Provenance capture for authoritative state transitions (actor, timestamp, causation,
correlation, object revision) per docs/spec/01_SYSTEM_SPEC.md MRR-NFR-001.

Framework-free interfaces for the append-only, tamper-evident domain event
log and its transactional outbox (task-packets/E1-T06.yaml, MRR-NFR-002):
``mrr.provenance.events`` (the ``DomainEvent`` dataclass and the pure
hash-chain function), ``mrr.provenance.log`` (the ``EventLog``/
``OutboxDispatcher`` protocols and the pure chain-verification function),
and ``mrr.provenance.exceptions`` (``ChainVerificationError``,
``EventAppendError``). Concrete PostgreSQL implementations live in
``mrr.persistence`` - this package carries no SQLAlchemy, driver, or
framework import (MRR-NFR-010; enforced by the import-linter contract in
pyproject.toml).
"""
