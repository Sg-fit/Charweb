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

TIMING = ["timing_iv_mean", "timing_iv_cv", "timing_iv_median", "timing_iv_p90",
          "timing_iv_min", "timing_kd_mean", "timing_kd_cv", "timing_rate",
          # Deliberation latency. An LLM-in-the-loop harness stops to think
          # after each page render; a scripted one does not. These separate
          # "how long before acting" from "how fast once acting", which the
          # inter-event features above blend together.
          "timing_ttfa_ms", "timing_think_mean", "timing_think_p90",
          "timing_think_max"]
ACTION = ["action_click_pct", "action_keydown_pct", "action_mousemove_pct",
          "action_scroll_pct", "action_pageload_pct", "action_other_pct",
          "action_n_types", "action_entropy"]
STRUCT = ["struct_n_urls", "struct_events_per_url", "struct_revisit_rate",
          "struct_duration_s", "struct_n_events"]
GEOM = ["geom_vel_mean", "geom_vel_cv", "geom_vel_max", "geom_mousemove_n"]

FEATURES = TIMING + ACTION + STRUCT + GEOM
# first_seen is exported so the batch/temporal confound can be tested directly
# ("does collection time predict the label?"), which needs a clock per session.
LABELS = ["session_uid", "harness", "model", "instruction_condition", "run_id",
          "logged_in", "adversarial_condition", "first_seen"]


def _mean_cv(xs):
    """Mean and coefficient of variation; (0,0) when there's too little data.
    CV (sd/mean) is the scale-free 'how irregular is this rhythm' number --
    the part of timing that survives a machine simply being faster."""
    xs = [x for x in xs if x is not None]
    if len(xs) < 2:
        return 0.0, 0.0
    m = statistics.fmean(xs)
    if m == 0:
        return 0.0, 0.0
    return m, statistics.pstdev(xs) / m


def _pct(n, total):
    return (n / total * 100.0) if total else 0.0


def _percentile(xs, q):
    if not xs:
        return 0.0
    s = sorted(xs)
    k = (len(s) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return float(s[int(k)])
    return float(s[lo] * (hi - k) + s[hi] * (k - lo))


def _think_gaps(rows, clock):
    """Gap (ms) from each pageload to the next real action on that page.

    This is the agent's deliberation time. It is deliberately NOT the same as
    timing_iv_*: those average over every consecutive pair, so a long think
    followed by a burst of fast clicks washes out. Isolating the post-pageload
    gap keeps the think visible.
    """
    gaps = []
    for i, r in enumerate(rows):
        if r.action_type != "pageload":
            continue
        for nxt in rows[i + 1:]:
            if nxt.action_type == "pageload":
                break                      # navigated again without acting
            a, b = clock(r), clock(nxt)
            if a and b:
                gaps.append((b - a).total_seconds() * 1000.0)
            break
    return gaps


def featurise(rows, clock=lambda r: r.timestamp):
    """rows: TrackedAction-like, ordered by (seq, timestamp). None if too short.

    `clock` selects which timestamp to trust. The client clock comes from
    track.js and an attacker controls it; server_ts is assigned in /api/track
    and they do not. Exporting the same sessions under both is what makes the
    client-trust vs server-trust comparison (Phase 2, E3) possible -- and it
    has to be done on the CLEAN data first, or there is no baseline to compare
    an attack against.
    """
    if len(rows) < 5:
        return None
    if any(clock(r) is None for r in rows):
        # Rows predating the server_ts column would silently produce garbage
        # intervals if mixed with rows that have it.
        return None

    ts = [clock(r) for r in rows]
    total = len(rows)
    duration = (ts[-1] - ts[0]).total_seconds()

    iv = [(ts[i] - ts[i - 1]).total_seconds() * 1000.0 for i in range(1, len(ts))]
    kd_ts = [clock(r) for r in rows if r.action_type == "keydown"]
    kd = [(kd_ts[i] - kd_ts[i - 1]).total_seconds() * 1000.0
          for i in range(1, len(kd_ts))]

    think = _think_gaps(rows, clock)
    # Time to first action: from the session's first pageload to the first
    # thing that is not a pageload. Falls back to the first interval when no
    # pageload was recorded (older rows), rather than reporting a 0 that would
    # read as "acted instantly".
    ttfa = 0.0
    first_pl = next((i for i, r in enumerate(rows)
                     if r.action_type == "pageload"), None)
    if first_pl is not None:
        nxt = next((r for r in rows[first_pl + 1:]
                    if r.action_type != "pageload"), None)
        if nxt is not None:
            ttfa = (clock(nxt) - ts[first_pl]).total_seconds() * 1000.0
    elif iv:
        ttfa = iv[0]

    iv_mean, iv_cv = _mean_cv(iv)
    kd_mean, kd_cv = _mean_cv(kd)

    counts = Counter(r.action_type for r in rows)
    known = ("click", "keydown", "mousemove", "scroll", "pageload")
    # Shannon entropy over the action-type mix: a scripted harness that only
    # ever clicks and types scores low; an agent that varies scores higher.
    entropy = -sum((c / total) * math.log2(c / total) for c in counts.values() if c)

    urls = [r.url for r in rows if r.url]
    uniq_urls = set(urls)
    revisit = 1.0 - (len(uniq_urls) / len(urls)) if urls else 0.0

    vels = []
    for r in rows:
        if r.action_type == "mousemove" and r.details:
            try:
                v = json.loads(r.details).get("velocity")
            except (ValueError, TypeError):
                v = None
            if isinstance(v, (int, float)):
                vels.append(float(v))
    vel_mean, vel_cv = _mean_cv(vels)

    return {
        "timing_iv_mean": iv_mean,
        "timing_iv_cv": iv_cv,
        "timing_iv_median": _percentile(iv, 0.5),
        "timing_iv_p90": _percentile(iv, 0.9),
        "timing_iv_min": min(iv) if iv else 0.0,
        "timing_kd_mean": kd_mean,
        "timing_kd_cv": kd_cv,
        "timing_rate": (total / duration) if duration > 0 else 0.0,
        "timing_ttfa_ms": ttfa,
        "timing_think_mean": statistics.fmean(think) if think else 0.0,
        "timing_think_p90": _percentile(think, 0.9),
        "timing_think_max": max(think) if think else 0.0,
        "action_click_pct": _pct(counts.get("click", 0), total),
        "action_keydown_pct": _pct(counts.get("keydown", 0), total),
        "action_mousemove_pct": _pct(counts.get("mousemove", 0), total),
        "action_scroll_pct": _pct(counts.get("scroll", 0), total),
        "action_pageload_pct": _pct(counts.get("pageload", 0), total),
        "action_other_pct": _pct(sum(c for a, c in counts.items() if a not in known), total),
        "action_n_types": len(counts),
        "action_entropy": entropy,
        "struct_n_urls": len(uniq_urls),
        "struct_events_per_url": (total / len(uniq_urls)) if uniq_urls else 0.0,
        "struct_revisit_rate": revisit,
        "struct_duration_s": duration,
        "struct_n_events": total,
        "geom_vel_mean": vel_mean,
        "geom_vel_cv": vel_cv,
        "geom_vel_max": max(vels) if vels else 0.0,
        "geom_mousemove_n": counts.get("mousemove", 0),
    }


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
                health[uid] = (row.get("clean") == "1") or rate <= args.max_empty_rate
        bad = sum(1 for v in health.values() if not v)
        print(f"health file: {len(health)} sessions, {bad} marked degraded "
              f"(max_empty_rate={args.max_empty_rate})")

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
                if health.get(s.session_uid) is False:
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
          + (f", {skipped_degraded} degraded (empty model replies)" if skipped_degraded else ""))

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
