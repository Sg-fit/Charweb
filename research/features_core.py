"""Pure feature extraction for session event streams -- no Flask, no database.

Split out of export_research_features.py (2026-11) because that module runs
`from app import app, db` at import time, which initialises the application's
RotatingFileHandler. Imported by a script running as root, that recreates
logs/Charweb.log as root:root and crash-loops charweb.service -- the cause of the
10-day outage (2026-09-23). Anything that only needs the feature definitions
(offline replays, ablations, tests) should import THIS module instead.

Behaviour is byte-for-byte what export_research_features.featurise produced
before the split; research/test_features_core.py proves it on real event streams.

Feature groups are kept explicit because H3 ablates between them:

  timing_*    -- inter-event and keystroke timing (harness "style")
  action_*    -- action-type distribution (what the agent does)
  struct_*    -- episode structure: pages visited, revisits, events/page
  geom_*      -- mouse geometry (velocity/path)
"""
import json
import math
import statistics
from collections import Counter

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
