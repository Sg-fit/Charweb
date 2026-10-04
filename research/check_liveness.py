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
live service. This script opens the SQLite file read-only and touches nothing
else, so it is safe to run under any user at any time.

DB path: --db, else DATABASE_URL (sqlite:///...). A *relative* sqlite URL
resolves against Flask's instance folder, not the cwd, so it is joined to
--instance-dir (default ./instance) exactly as Flask-SQLAlchemy would.
"""
import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path


def resolve_db(db_arg, instance_dir):
    if db_arg:
        return Path(db_arg)
    url = os.environ.get("DATABASE_URL", "sqlite:///app.db")
    if not url.startswith("sqlite:///"):
        sys.exit(f"DATABASE_URL is not sqlite ({url!r}); pass --db explicitly")
    path = url[len("sqlite:///"):]
    p = Path(path)
    # sqlite:////abs/path -> '/abs/path'; sqlite:///rel.db -> instance/rel.db
    if p.is_absolute() or (len(path) > 1 and path[1] == ":"):
        return p
    return Path(instance_dir) / p


def connect_ro(path):
    if not path.exists():
        sys.exit(f"DB not found: {path}")
    # mode=ro: cannot create, cannot write, cannot take a write lock.
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=10)


def has_column(con, table, col):
    return any(r[1] == col for r in con.execute(f"PRAGMA table_info({table})"))


def measure(con, run_id=None):
    m = {"taken_at": time.time()}
    m["events"] = con.execute("SELECT COUNT(*) FROM tracked_action").fetchone()[0]
    m["max_event_id"] = con.execute(
        "SELECT COALESCE(MAX(id), 0) FROM tracked_action").fetchone()[0]
    m["sessions"] = con.execute("SELECT COUNT(*) FROM user_session").fetchone()[0]
    m["run_sessions"] = None
    if run_id is not None:
        if has_column(con, "user_session", "run_id"):
            m["run_sessions"] = con.execute(
                "SELECT COUNT(*) FROM user_session WHERE run_id = ?",
                (run_id,)).fetchone()[0]
        else:
            m["run_sessions"] = "no run_id column"
    m["run_id"] = run_id
    return m


def cmd_snapshot(a):
    path = resolve_db(a.db, a.instance_dir)
    with connect_ro(path) as con:
        m = measure(con, a.run_id)
    m["db"] = str(path)
    Path(a.file).write_text(json.dumps(m, indent=1))
    # Read it back: verify the write, never the exit code.
    back = json.loads(Path(a.file).read_text())
    assert back["events"] == m["events"], "snapshot did not round-trip"
    print(f"snapshot -> {a.file}: events={m['events']} max_id={m['max_event_id']} "
          f"sessions={m['sessions']} run_sessions={m['run_sessions']} db={path}")
    return 0


def cmd_verify(a):
    try:
        before = json.loads(Path(a.file).read_text())
    except (OSError, ValueError) as e:
        # No usable "before" snapshot means nothing can be verified. That is
        # a failure, not a pass -- never fall through to "OK".
        print(f"LIVENESS FAILED -- cannot read snapshot {a.file}: {e}")
        return 2
    path = resolve_db(a.db or before.get("db"), a.instance_dir)
    run_id = a.run_id if a.run_id is not None else before.get("run_id")
    with connect_ro(path) as con:
        now = measure(con, run_id)

    new_events = now["events"] - before["events"]
    new_sessions = now["sessions"] - before["sessions"]
    print(f"db: {path}")
    print(f"  events    {before['events']} -> {now['events']}   (+{new_events})")
    print(f"  max id    {before['max_event_id']} -> {now['max_event_id']}")
    print(f"  sessions  {before['sessions']} -> {now['sessions']}   (+{new_sessions})")

    problems = []
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
    ap.add_argument("--db", default=None, help="SQLite file (default: from DATABASE_URL)")
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
