"""PostgreSQL persistence for first-class MRR objects and typed graph edges
(task-packets/E1-T05.yaml): SQLAlchemy Core table definitions
(``mrr.persistence.tables``) and repository implementations
(``mrr.persistence.repositories``) of the framework-free protocols declared
in ``mrr.domain.repositories``.

This package is the one place SQLAlchemy and the ``psycopg`` driver are
imported anywhere in ``mrr`` (the import-linter contract in pyproject.toml
permits it here and nowhere in the other, framework-free packages).
"""
