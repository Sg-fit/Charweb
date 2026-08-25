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
          "timing_iv_min", "timing_kd_mean", "timing_kd_cv", "timing_rate"]
ACTION = ["action_click_pct", "action_keydown_pct", "action_mousemove_pct",
          "action_scroll_pct", "action_pageload_pct", "action_other_pct",
          "action_n_types", "action_entropy"]
STRUCT = ["struct_n_urls", "struct_events_per_url", "struct_revisit_rate",
          "struct_duration_s", "struct_n_events"]
GEOM = ["geom_vel_mean", "geom_vel_cv", "geom_vel_max", "geom_mousemove_n"]

FEATURES = TIMING + ACTION + STRUCT + GEOM
LABELS = ["session_uid", "harness", "model", "instruction_condition", "run_id",
          "logged_in", "adversarial_condition"]


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


def featurise(rows):
    """rows: TrackedAction-like, ordered by (seq, timestamp). None if too short."""
    if len(rows) < 5:
        return None

    ts = [r.timestamp for r in rows]
    total = len(rows)
    duration = (ts[-1] - ts[0]).total_seconds()

    iv = [(ts[i] - ts[i - 1]).total_seconds() * 1000.0 for i in range(1, len(ts))]
    kd_ts = [r.timestamp for r in rows if r.action_type == "keydown"]
    kd = [(kd_ts[i] - kd_ts[i - 1]).total_seconds() * 1000.0
          for i in range(1, len(kd_ts))]

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
    args = ap.parse_args()

    with app.app_context():
        sessions = db.session.scalars(sa.select(UserSession)).all()
        written = skipped_short = skipped_unlabelled = 0

        with open(args.out, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=LABELS + FEATURES)
            w.writeheader()

            for s in sessions:
                if not s.harness and not args.include_unlabelled:
                    skipped_unlabelled += 1
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
                feats = featurise(rows)
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
                    **feats,
                })
                written += 1

    print(f"wrote {written} sessions -> {args.out}")
    print(f"skipped: {skipped_short} too-short, {skipped_unlabelled} unlabelled")


if __name__ == "__main__":
    main()
