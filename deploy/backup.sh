#!/usr/bin/env bash
# Nightly SQLite backup. Uses .backup rather than cp because the database is
# live and WAL-mode -- copying the file directly can capture a torn state.
set -euo pipefail

DB="${DB:-/srv/charweb/instance/app.db}"
DEST="${DEST:-/srv/backups}"
KEEP_DAYS="${KEEP_DAYS:-30}"

mkdir -p "$DEST"
STAMP=$(date +%F-%H%M)
OUT="$DEST/app-$STAMP.db"

sqlite3 "$DB" ".backup '$OUT'"
gzip -f "$OUT"

# Verify the backup actually opens before trusting it.
if ! zcat "$OUT.gz" > /tmp/verify.db 2>/dev/null || \
   ! sqlite3 /tmp/verify.db "PRAGMA integrity_check;" | grep -q '^ok$'; then
    echo "BACKUP VERIFY FAILED for $OUT.gz" >&2
    rm -f /tmp/verify.db
    exit 1
fi
rm -f /tmp/verify.db

find "$DEST" -name 'app-*.db.gz' -mtime +"$KEEP_DAYS" -delete
echo "ok: $OUT.gz ($(du -h "$OUT.gz" | cut -f1))"

# Offsite copy -- fill in. A backup that only exists on the same VPS is not
# a backup. Human-subject data cannot be re-collected.
# rclone copy "$OUT.gz" remote:charweb-backups/
