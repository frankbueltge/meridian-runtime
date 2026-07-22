#!/usr/bin/env bash
# Archive durability (docs/design/2026-07-22-nutzungsentscheidung-e9-vertagt.md,
# "Erster identifizierter Nutzungs-Bedarf"): dump the real-run archival schemas
# from the local Postgres into versioned, diffable SQL files under
# archive/dumps/ — git becomes the durable home of the authoritative archive
# state, matching the wider practice's "git is the archive" ethos. The
# miniature of E9-T02 (backup/restore), built on concrete need per the owner's
# use-first decision — not the full runbook.
#
# Usage:
#   scripts/archive-dump.sh            # dump both schemas
#   scripts/archive-dump.sh --verify   # dump, then round-trip prove each dump:
#                                      #   restore into a throwaway database and
#                                      #   run the event-hash chain verification
#                                      #   against the restored schema (the
#                                      #   archive's own integrity criterion —
#                                      #   a dump that merely restores is not
#                                      #   yet a dump that VERIFIES).
#
# The connection URL comes from MRR_ARCHIVE_DATABASE_URL (libpq form), default
# the session-documented local instance. Dumps are plain SQL with --no-owner
# --no-privileges so a restore does not depend on this machine's role setup.
set -euo pipefail

DB_URL="${MRR_ARCHIVE_DATABASE_URL:-postgresql://mrr@127.0.0.1:54329/mrr_test}"
SCHEMAS=(mrr_k1t04_real_run_v2 mrr_run2_corroboration_floor_v1)
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="$REPO_ROOT/archive/dumps"
VERIFY="${1:-}"

mkdir -p "$OUT_DIR"

for schema in "${SCHEMAS[@]}"; do
  out_file="$OUT_DIR/$schema.sql"
  pg_dump "$DB_URL" --schema="$schema" --no-owner --no-privileges >"$out_file"
  echo "dumped $schema -> ${out_file#"$REPO_ROOT"/} ($(wc -l <"$out_file" | tr -d ' ') lines)"
done

if [[ "$VERIFY" == "--verify" ]]; then
  verify_db="mrr_dump_verify_$$"
  admin_url="${DB_URL%/*}/postgres"
  psql "$admin_url" -q -c "CREATE DATABASE $verify_db"
  trap 'psql "$admin_url" -q -c "DROP DATABASE IF EXISTS $verify_db"' EXIT
  verify_url="${DB_URL%/*}/$verify_db"
  for schema in "${SCHEMAS[@]}"; do
    psql "$verify_url" -q -v ON_ERROR_STOP=1 -f "$OUT_DIR/$schema.sql" >/dev/null
    MRR_DUMP_VERIFY_URL="${verify_url}" MRR_DUMP_VERIFY_SCHEMA="$schema" \
      "$REPO_ROOT/.venv/bin/python" - <<'PYEOF'
import os
import sqlalchemy as sa
from mrr.persistence.repositories import PostgresEventLog

url = os.environ["MRR_DUMP_VERIFY_URL"].replace("postgresql://", "postgresql+psycopg://")
schema = os.environ["MRR_DUMP_VERIFY_SCHEMA"]
engine = sa.create_engine(f"{url}?options=-c%20search_path%3D{schema}")
log = PostgresEventLog(engine)
count = len(log.read_all())
log.verify_chain()
engine.dispose()
print(f"restored {schema}: {count} events, hash chain VERIFIED")
PYEOF
  done
fi
