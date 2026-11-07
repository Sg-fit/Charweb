#!/usr/bin/env bash
# Nightly PostgreSQL backup for the production Charweb database.
#
# Runs the dump as the `postgres` OS user (peer auth), so no password is read,
# stored or exposed. This is the script installed on the VPS as
# /usr/local/bin/charweb-backup (cron: 0 3 * * *).
#
# Restore-tested 2026-11-07: 6.7 MB dump -> fresh database -> 309,378 rows in
# tracked_action. The dump is root-only (mode 600), so restore it by piping:
#   cat /srv/backups/<file> | runuser -u postgres -- pg_restore -d <newdb>
set -euo pipefail

DEST="${DEST:-/srv/backups}"
KEEP_DAYS="${KEEP_DAYS:-30}"
DBNAME="${DBNAME:-charweb}"

mkdir -p "$DEST"
OUT="$DEST/charweb-$(date +%F-%H%M).dump"

runuser -u postgres -- pg_dump -Fc "$DBNAME" > "$OUT"

# Verify the write: a dump that cannot be listed is not a backup. (Exit codes
# alone have reported success on empty output in this project before.)
pg_restore --list "$OUT" > /dev/null
[ "$(stat -c %s "$OUT")" -gt 10000 ] || { echo "BACKUP TOO SMALL: $OUT" >&2; exit 1; }

chmod 600 "$OUT"
find "$DEST" -name 'charweb-*.dump' -mtime +"$KEEP_DAYS" -delete
echo "ok: $OUT ($(du -h "$OUT" | cut -f1))"

# Offsite: the VPS cannot reach the owner's PC, so copies are PULLED from the PC
# (scp). Add an rclone/object-store push here if one is ever configured.
