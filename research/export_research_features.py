"""Export one feature row per labelled session for the M3 analysis.

Supersedes build_features.py's username-parsing for the AI-only study: the
labels (harness / model / instruction_condition / run_id) are now real
columns on user_session, written from the X-* headers and cw_* cookies the
harnesses set, so there is nothing to infer.

Feature groups are kept explicit because H3 ablates between them:

  timing_*    -- inter-event and keystroke timing (harness "style")
  action_*    -- action-type distribution (what the agent does)
  struct_*    -- episode structure: pages visited, revisits, events/page
  geom_*      -- mouse geometry (velocity/path). DOM-driven agents produce
                 almost none of these, which is the point of H3.

Run on the server (needs the live DB):

    cd /srv/charweb; set -a; . /etc/charweb.env; set +a
    ./venv/bin/python research/export_research_features.py -o m3_features.csv
"""
import argparse
import csv
import json
import math
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sqlalchemy as sa

from app import app, db
from app.models import UserSession, TrackedAction

# Pure feature code lives in features_core.py so analysis scripts can use it
# without importing the Flask app (see that module's docstring).
from features_core import (  # noqa: E402,F401
    TIMING, ACTION, STRUCT, GEOM, FEATURES, _mean_cv, _pct, _percentile, _think_gaps, featurise)

# first_seen is exported so the batch/temporal confound can be tested directly
# ("does collection time predict the label?"), which needs a clock per session.
LABELS = ["session_uid", "harness", "model", "instruction_condition", "run_id",
          "logged_in", "adversarial_condition", "first_seen"]


def main():
    ap = argparse.ArgumentParser(description="Export per-session features for M3")
    ap.add_argument("-o", "--out", default="m3_features.csv")
    ap.add_argument("--min-events", type=int, default=5)
    ap.add_argument("--include-unlabelled", action="store_true",
                    help="also export sessions with no harness label")
    ap.add_argument("--run-id", default=None,
                    help="export only sessions from this run_id "
                         "(e.g. an interleaved batch), or a comma-separated list")
    ap.add_argument("--health-csv", default=None,
                    help="collection_health.csv written by llm_agent. Sessions "
                         "listed there as not clean are DROPPED. A session "
                         "where the model returned empty replies did not browse "
                         "the way that model browses -- it browsed the way the "
                         "plumbing let it -- and pooling those with clean ones "
                         "measures the pipeline as much as the agent.")
    ap.add_argument("--require-health", action="store_true",
                    help="drop sessions that have NO row in the health file. "
                         "Absence normally means 'collected before health "
                         "tracking existed', so it is tolerated -- but in a run "
                         "that should have health for every session, a missing "
                         "row means the collector died before writing one, "
                         "which is the opposite of a reason to trust it.")
    ap.add_argument("--max-empty-rate", type=float, default=0.0,
                    help="with --health-csv, tolerate up to this fraction of "
                         "empty replies (0.0 = require perfectly clean)")
    ap.add_argument("--clock", choices=("client", "server"), default="client",
                    help="which timestamp the timing features are built from. "
                         "'client' is track.js's clock (an attacker controls "
                         "it); 'server' is server_ts, assigned in /api/track "
                         "(they do not). Export both on the clean data to get "
                         "the client-trust vs server-trust baseline.")
    args = ap.parse_args()

    clock = ((lambda r: r.server_ts) if args.clock == "server"
             else (lambda r: r.timestamp))

    # session_uid -> keep?  Only sessions PRESENT in the health file are
    # judged; sessions collected before health tracking existed are left alone
    # rather than silently dropped, since absence here means "unknown", not
    # "bad".
    health = {}
    if args.health_csv:
        with open(args.health_csv, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                uid = (row.get("session_uid") or "").strip()
                if not uid:
                    continue
                try:
                    rate = float(row.get("empty_rate") or 0)
                except ValueError:
                    rate = 1.0
                # AND, not OR. The old `or` let any session with zero empty
                # replies through regardless of stalls or a dead model -- which
                # is how a batch of 3-step throttled stubs reached the analysis
                # and scored as the most separable class in it. `clean` is the
                # authority; --max-empty-rate only ever RELAXES it.
                ok = row.get("clean") == "1"
                if not ok and args.max_empty_rate > 0:
                    ok = (rate <= args.max_empty_rate
                          and row.get("model_dead") != "1"
                          and int(row.get("stalls") or 0) == 0)
                health[uid] = ok
        bad = sum(1 for v in health.values() if not v)
        print(f"health file: {len(health)} sessions, {bad} marked degraded "
              f"(max_empty_rate={args.max_empty_rate})"
              + ("  [--require-health: sessions with no row are DROPPED]"
                 if args.require_health else
                 "  [sessions with no row are KEPT -- pass --require-health "
                 "to drop them]"))

    wanted_runs = ({r.strip() for r in args.run_id.split(",") if r.strip()}
                   if args.run_id else None)

    with app.app_context():
        sessions = db.session.scalars(sa.select(UserSession)).all()
        written = skipped_short = skipped_unlabelled = 0
        skipped_degraded = 0

        with open(args.out, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=LABELS + FEATURES)
            w.writeheader()

            for s in sessions:
                if wanted_runs and (s.run_id or "none") not in wanted_runs:
                    continue
                if not s.harness and not args.include_unlabelled:
                    skipped_unlabelled += 1
                    continue
                verdict = health.get(s.session_uid)
                if verdict is False or (args.require_health and args.health_csv
                                        and verdict is None):
                    skipped_degraded += 1
                    continue
                rows = db.session.scalars(
                    sa.select(TrackedAction)
                    .where(TrackedAction.session_uid == s.session_uid)
                    # seq is server-assigned and monotonic; timestamp is the
                    # client's clock and can tie or drift, so seq leads.
                    .order_by(TrackedAction.seq.asc(), TrackedAction.timestamp.asc())
                ).all()
                if len(rows) < args.min_events:
                    skipped_short += 1
                    continue
                feats = featurise(rows, clock)
                if feats is None:
                    skipped_short += 1
                    continue
                w.writerow({
                    "session_uid": s.session_uid,
                    "harness": s.harness or "unlabelled",
                    "model": s.model or "none",
                    "instruction_condition": s.instruction_condition or "none",
                    "run_id": s.run_id or "none",
                    "logged_in": int(s.user_id is not None),
                    "adversarial_condition": s.adversarial_condition or "clean",
                    "first_seen": s.first_seen.isoformat() if s.first_seen else "",
                    **feats,
                })
                written += 1

    print(f"wrote {written} sessions -> {args.out}")
    print(f"skipped: {skipped_short} too-short, {skipped_unlabelled} unlabelled"
          + (f", {skipped_degraded} degraded/uncertified" if skipped_degraded else ""))

    if written == 0:
        # An empty export used to sail on silently and only blow up two scripts
        # later with an unrelated sklearn error. Fail here, where the cause is
        # obvious, and show what run_ids actually exist.
        print("\nERROR: no sessions were exported.")
        if wanted_runs:
            print(f"  --run-id {sorted(wanted_runs)} matched nothing.")
            with app.app_context():
                rows = db.session.execute(
                    sa.select(UserSession.run_id, sa.func.count())
                    .group_by(UserSession.run_id)
                    .order_by(sa.func.count().desc())).all()
            print("  run_ids present in the database:")
            for rid, n in rows:
                print(f"    {rid or '(none)':<40} {n} sessions")
        sys.exit(1)


if __name__ == "__main__":
    main()
