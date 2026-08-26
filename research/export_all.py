"""Export EVERYTHING to one archive: raw events, sessions, features, DB dump.

The feature CSV is a lossy summary -- 25 numbers per session. This dumps the
underlying data too, so the whole study can be re-analysed (or re-featurised
with different definitions) off the server, and so there is a backup that does
not depend on the VPS surviving.

Produces charweb_export_<UTC>.tar.gz containing:

    sessions.csv      every user_session row, all columns
    events.csv        every tracked_action row -- the raw per-event log
    m3_features.csv   the 25-feature-per-session analysis table
    database.sql      full pg_dump (only if pg_dump is available)
    MANIFEST.txt      row counts, column lists, and the export timestamp

    cd /srv/charweb; set -a; . /etc/charweb.env; set +a
    ./venv/bin/python research/export_all.py
"""
import csv
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sqlalchemy as sa

from app import app, db
from app.models import UserSession, TrackedAction


def dump_table(model, path, chunk=5000):
    """Stream a whole table to CSV. Columns are read off the mapper rather than
    hardcoded, so a future migration can't silently drop a column here."""
    cols = [c.key for c in sa.inspect(model).mapper.column_attrs]
    n = 0
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        # yield_per keeps a large tracked_action table off the heap
        q = db.session.execute(
            sa.select(model).execution_options(yield_per=chunk)).scalars()
        for row in q:
            w.writerow([getattr(row, c) for c in cols])
            n += 1
    return n, cols


def pg_dump(path):
    """Full logical dump, if this is Postgres and pg_dump exists."""
    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith(("postgres://", "postgresql://")):
        return None, "DATABASE_URL is not Postgres -- skipped"
    if not shutil.which("pg_dump"):
        return None, "pg_dump not installed -- skipped (apt install postgresql-client)"
    url = url.replace("postgres://", "postgresql://", 1)
    try:
        with open(path, "w", encoding="utf-8") as fh:
            p = subprocess.run(["pg_dump", "--no-owner", "--no-acl", url],
                               stdout=fh, stderr=subprocess.PIPE, timeout=600)
        if p.returncode != 0:
            return None, f"pg_dump failed: {p.stderr.decode()[:200]}"
        return os.path.getsize(path), "ok"
    except Exception as e:
        return None, f"pg_dump error: {str(e)[:200]}"


def main():
    stamp = f"{datetime.now(timezone.utc):%Y%m%d_%H%M%S}"
    out = Path(f"charweb_export_{stamp}.tar.gz")
    tmp = Path(tempfile.mkdtemp(prefix="charweb_export_"))
    manifest = [f"Charweb full export", f"created_utc: {stamp}", ""]

    with app.app_context():
        n_s, cols_s = dump_table(UserSession, tmp / "sessions.csv")
        print(f"  sessions.csv   {n_s:>7} rows")
        manifest += [f"sessions.csv: {n_s} rows", f"  columns: {', '.join(cols_s)}", ""]

        n_e, cols_e = dump_table(TrackedAction, tmp / "events.csv")
        print(f"  events.csv     {n_e:>7} rows")
        manifest += [f"events.csv: {n_e} rows", f"  columns: {', '.join(cols_e)}", ""]

    # reuse the analysis exporter so the feature table is identical to the one
    # the results were computed from, rather than a second implementation
    feat = tmp / "m3_features.csv"
    r = subprocess.run(
        [sys.executable, str(Path(__file__).parent / "export_research_features.py"),
         "-o", str(feat)],
        capture_output=True, text=True)
    if feat.exists():
        n_f = sum(1 for _ in open(feat, encoding="utf-8")) - 1
        print(f"  m3_features.csv{n_f:>7} rows")
        manifest.append(f"m3_features.csv: {n_f} rows (labelled sessions only)")
    else:
        print("  m3_features.csv  SKIPPED")
        manifest.append(f"m3_features.csv: FAILED -- {r.stderr.strip()[:300]}")

    size, note = pg_dump(tmp / "database.sql")
    print(f"  database.sql   {'%7d bytes' % size if size else '   ' + note}")
    manifest.append(f"database.sql: {note}")
    if not size:
        (tmp / "database.sql").unlink(missing_ok=True)

    (tmp / "MANIFEST.txt").write_text("\n".join(manifest), encoding="utf-8")

    with tarfile.open(out, "w:gz") as tar:
        for f in sorted(tmp.iterdir()):
            tar.add(f, arcname=f.name)
    shutil.rmtree(tmp, ignore_errors=True)

    mb = out.stat().st_size / 1e6
    print(f"\nwrote {out}  ({mb:.1f} MB)")
    print(f"\nDownload it from your PC with:")
    print(f"  scp root@<server-ip>:/srv/charweb/{out.name} "
          f"C:\\Users\\charl\\ProjectStart\\")


if __name__ == "__main__":
    main()
