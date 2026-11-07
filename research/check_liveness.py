"""Collection liveness check: verify the DB actually received data.

Four failure modes in this project had the same shape -- two writers, one shared
resource, no validation, and failure that reports success. The 10-day outage is
the clearest: the site crash-looped and every collection script still exited 0.
An exit code says the *driver* finished; it says nothing about whether rows
reached the database. So this checks the database itself.

Use it as a before/after pair around every collection batch:

    ./venv/bin/python research/check_liveness.py snapshot before.json
    ... run the batch ...
    ./venv/bin/python research/check_liveness.py verify before.json \\
            --min-new-events 500 --run-id harness_M2 --min-new-sessions 20

`verify` exits 1 (and prints why) unless every stated expectation holds, so a
wrapper can `|| exit 1` and a silent batch can no longer report success.

Deliberately does NOT import `app`. Importing it initialises the rotating log
handler, which as root can recreate Charweb.log as root:root and crash-loop the
live service. This script opens the database read-only and touches nothing
else, so it is safe to run under any user at any time.

Backends. Production (the VPS) is PostgreSQL; local copies and old exports are
SQLite. Both are supported and chosen from the URL:

    DATABASE_URL=postgresql://user:pw@localhost:5432/charweb   (psycopg2)
    DATABASE_URL=sqlite:///app.db        (relative -> joined to --instance-dir,
                                          exactly as Flask-SQLAlchemy would)

or pass --db with either a URL or a SQLite file path. Postgres sessions are
opened READ ONLY. The password is never printed and never written to the
snapshot file; only host/dbname is recorded, and `verify` refuses to compare
two different databases.

On the VPS the env file is not exported by default, so load it first:

    set -a; . /etc/charweb.env; set +a
    ./venv/bin/python research/check_liveness.py snapshot before.json
"""
import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit


def _is_pg(url):
    return url.split(":", 1)[0].split("+", 1)[0] in ("postgresql", "postgres")


def _redact_pg(url):
    """host:port/dbname only -- never the user or password."""
    u = urlsplit(url)
    return f"postgres:{u.hostname or 'localhost'}:{u.port or 5432}/{(u.path or '/').lstrip('/')}"


def open_db(db_arg, instance_dir):
    """-> (connection, kind, label). label is safe to print and to store."""
    target = db_arg or os.environ.get("DATABASE_URL", "sqlite:///app.db")

    if _is_pg(target):
        try:
            import psycopg2
        except ImportError:
            sys.exit("PostgreSQL URL given but psycopg2 is not installed "
                     "(use the app venv: ./venv/bin/python)")
        # SQLAlchemy-style 'postgresql+psycopg2://' -> plain libpq URL
        url = "postgresql://" + target.split("://", 1)[1]
        try:
            con = psycopg2.connect(url, connect_timeout=10)
        except psycopg2.Error as e:
            # psycopg2 messages can echo the DSN; print only the first line
            sys.exit(f"cannot connect to {_redact_pg(url)}: "
                     f"{str(e).strip().splitlines()[0][:120]}")
        con.set_session(readonly=True, autocommit=True)
        return con, "pg", _redact_pg(url)

    if target.startswith("sqlite:///"):
        path = target[len("sqlite:///"):]
        p = Path(path)
        # sqlite:////abs/path -> '/abs/path'; sqlite:///rel.db -> instance/rel.db
        if not (p.is_absolute() or (len(path) > 1 and path[1] == ":")):
            p = Path(instance_dir) / p
    else:
        p = Path(target)
    if not p.exists():
        sys.exit(f"DB not found: {p}")
    # mode=ro: cannot create, cannot write, cannot take a write lock.
    con = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=10)
    return con, "sqlite", str(p)


def q1(con, kind, sql, params=()):
    """First column of the first row. sql is written with ? and adapted."""
    if kind == "pg":
        cur = con.cursor()
        cur.execute(sql.replace("?", "%s"), params)
        row = cur.fetchone()
        cur.close()
        return row[0]
    return con.execute(sql, params).fetchone()[0]


def has_column(con, kind, table, col):
    if kind == "pg":
        return bool(q1(con, kind,
                       "SELECT COUNT(*) FROM information_schema.columns "
                       "WHERE table_name = ? AND column_name = ?", (table, col)))
    return any(r[1] == col for r in con.execute(f"PRAGMA table_info({table})"))


def measure(con, kind, run_id=None):
    m = {"taken_at": time.time()}
    m["events"] = q1(con, kind, "SELECT COUNT(*) FROM tracked_action")
    m["max_event_id"] = q1(con, kind, "SELECT COALESCE(MAX(id), 0) FROM tracked_action")
    m["sessions"] = q1(con, kind, "SELECT COUNT(*) FROM user_session")
    m["run_sessions"] = None
    if run_id is not None:
        if has_column(con, kind, "user_session", "run_id"):
            m["run_sessions"] = q1(
                con, kind, "SELECT COUNT(*) FROM user_session WHERE run_id = ?", (run_id,))
        else:
            m["run_sessions"] = "no run_id column"
    m["run_id"] = run_id
    return m


def cmd_snapshot(a):
    con, kind, label = open_db(a.db, a.instance_dir)
    try:
        m = measure(con, kind, a.run_id)
    finally:
        con.close()
    m["db"] = label          # redacted for postgres; the file path for sqlite
    m["kind"] = kind
    Path(a.file).write_text(json.dumps(m, indent=1))
    # Read it back: verify the write, never the exit code.
    back = json.loads(Path(a.file).read_text())
    assert back["events"] == m["events"], "snapshot did not round-trip"
    print(f"snapshot -> {a.file}: events={m['events']} max_id={m['max_event_id']} "
          f"sessions={m['sessions']} run_sessions={m['run_sessions']} db={label}")
    return 0


def cmd_verify(a):
    try:
        before = json.loads(Path(a.file).read_text())
    except (OSError, ValueError) as e:
        # No usable "before" snapshot means nothing can be verified. That is
        # a failure, not a pass -- never fall through to "OK".
        print(f"LIVENESS FAILED -- cannot read snapshot {a.file}: {e}")
        return 2
    # A stored sqlite path is reusable; a redacted postgres label is not, so
    # postgres always reconnects through --db / DATABASE_URL.
    fallback = before.get("db") if before.get("kind") == "sqlite" else None
    con, kind, label = open_db(a.db or fallback, a.instance_dir)
    run_id = a.run_id if a.run_id is not None else before.get("run_id")
    try:
        now = measure(con, kind, run_id)
    finally:
        con.close()

    new_events = now["events"] - before["events"]
    new_sessions = now["sessions"] - before["sessions"]
    print(f"db: {label}")
    print(f"  events    {before['events']} -> {now['events']}   (+{new_events})")
    print(f"  max id    {before['max_event_id']} -> {now['max_event_id']}")
    print(f"  sessions  {before['sessions']} -> {now['sessions']}   (+{new_sessions})")

    problems = []
    if before.get("db") and before["db"] != label:
        problems.append(f"snapshot was taken on {before['db']} but this is {label} "
                        f"-- not comparable")
    if now["events"] < before["events"]:
        problems.append("event count went DOWN -- rows deleted or a different DB")
    if now["max_event_id"] <= before["max_event_id"]:
        problems.append("max event id did not advance -- nothing was written")
    if new_events < a.min_new_events:
        problems.append(f"only {new_events} new events, expected >= {a.min_new_events}")
    if new_sessions < a.min_new_sessions:
        problems.append(f"only {new_sessions} new sessions, expected >= {a.min_new_sessions}")

    if run_id is not None:
        rs, rs0 = now["run_sessions"], before.get("run_sessions")
        print(f"  run_id={run_id!r} sessions {rs0} -> {rs}")
        if isinstance(rs, str):
            problems.append(f"cannot check run_id: {rs}")
        elif a.min_new_run_sessions and (rs - (rs0 if isinstance(rs0, int) else 0)
                                         < a.min_new_run_sessions):
            problems.append(f"run {run_id!r}: fewer than {a.min_new_run_sessions} new sessions")

    if problems:
        print("\nLIVENESS FAILED -- do NOT report this batch as collected:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("\nLIVENESS OK")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=None,
                    help="postgresql:// URL, sqlite:/// URL, or SQLite file "
                         "(default: DATABASE_URL)")
    ap.add_argument("--instance-dir", default="instance")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("snapshot", help="record counts before a batch")
    s.add_argument("file")
    s.add_argument("--run-id", default=None)
    s.set_defaults(fn=cmd_snapshot)

    v = sub.add_parser("verify", help="fail unless the DB grew as expected")
    v.add_argument("file")
    v.add_argument("--run-id", default=None)
    v.add_argument("--min-new-events", type=int, default=1)
    v.add_argument("--min-new-sessions", type=int, default=1)
    v.add_argument("--min-new-run-sessions", type=int, default=0)
    v.set_defaults(fn=cmd_verify)

    a = ap.parse_args()
    sys.exit(a.fn(a))


if __name__ == "__main__":
    main()
