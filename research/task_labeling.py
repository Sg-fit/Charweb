"""
Task-type auto-labeling for tracked browsing/interaction events.

Tags each tracked event, and each contiguous run of same-task events (an
"episode"), with one of the task-type categories used in the
cross-architecture / task-type / adversarial-co-evolution study:

    signup_login   - registration and login forms
    search         - the search box
    feed_browse    - composing/scrolling/reading the home feed
    profile_edit   - editing about_me
    timed_dungeon  - the timed mission / daily dungeon hub (sign-in,
                     character creation, dungeon actions, shop, equip)
    chat           - the chat panel

(`chat` isn't one of the original five task types in the study design,
but its elements already carry stable ids in real data and were
otherwise silently swallowed into whichever task preceded them via
carry-forward -- included here so it doesn't quietly pollute the other
categories. Drop it from DIRECT_TARGET_RULES/PREFIXES below if you'd
rather it stay unlabeled.)

Two data sources, one shared labeling engine
----------------------------------------------
This runs against either:

  1. The live Charweb database (default) -- reads TrackedAction rows via
     Flask-SQLAlchemy, grouped by session_uid. Only works from inside the
     Charweb repo, against a real app.db/instance DB.

  2. An exported CSV (--csv PATH) -- e.g. ai_raw_combined.csv /
     human_raw_combined.csv, with columns
     [timestamp, username, action_type, target, details, session_label]
     and optionally `url`. No Flask/DB access needed. Use
     --csv-session-col / --csv-username-col / --csv-url-col if a given
     export names those columns differently.

Both paths funnel into the same RawEvent representation and the same
label_session_events()/build_episodes() logic below, so the two sources
can never silently drift apart in behavior.

Labeling strategy
------------------
A session mixes multiple tasks one after another, and several DOM
element ids are reused across forms (`username`/`password` appear on
both login and register -- fine, both map to signup_login here -- but
`submit` is reused by literally every form, and mousemove/scroll events
often carry no informative target at all). So labeling happens in three
passes per session, strongest evidence first:

  1. Direct target match: any event whose `target` uniquely identifies a
     task (`about_me` -> profile_edit, `q` -> search, ...) is labeled
     directly. Most specific evidence available, so it wins outright.
  2. URL match: failing that, the page the event happened on is consulted
     (`/edit_profile` -> profile_edit, `/daily/*` -> timed_dungeon, ...).
     Weaker than a distinctive element id -- a page hosts one task but an
     element identifies it -- yet far stronger than guessing, and unlike
     `target` it is present on *every* event including bare mousemove.
     Rows collected before URL capture existed have url=None and simply
     fall through to pass 3, so historical data labels exactly as before.
  3. Carry-forward: events with no usable target and no usable URL
     inherit the label of the closest *preceding* positively-labeled
     event in the same session, as long as the gap between them is under
     the carry-forward window. This reflects that a click/scroll/mousemove
     "belongs" to whatever task the user was just doing.

Anything that still can't be resolved (session opens with an ambiguous
event, or the gap since the last positive label is too large) is tagged
`unknown` rather than guessed.

Note that the carry-forward window is measured from the last *positively
labeled* event, not from the previous event -- it is an anchored window,
not a rolling one. A long run of ambiguous events all measure their gap
against the same anchor, so the run goes `unknown` once it passes the
window even if consecutive events are milliseconds apart.

Bidirectional fill (--bidirectional)
-------------------------------------
Pass 3 as described only looks backward, which throws away real evidence:
every event before a session's first anchor is unconditionally `unknown`,
and a run that outlives the window stays unknown even when the *next*
anchor, moments later, would have settled it. With --bidirectional an
ambiguous event also consults the nearest *following* anchor:

  - bracketed by two anchors carrying the SAME label -> 'bracketed',
    about as well-evidenced as a direct match
  - only a preceding anchor in range   -> 'carry_forward' (as before)
  - only a following anchor in range   -> 'carry_back'
  - bracketed by DIFFERING labels      -> resolved by --conflict-policy:
      nearest         (default) take the temporally closer anchor
      prefer_previous  always take the preceding anchor
      unknown          refuse to guess, leave it unknown

This matters most for pre-URL-capture data, where it is the only way to
raise coverage without re-collecting. It is off by default so existing
results reproduce; validate_carry.py measures how accurate each direction
and policy actually is on your data rather than assuming.

`resolved_via` on every labeled event records which pass produced its
label: 'direct' | 'url' | 'bracketed' | 'carry_forward' | 'carry_back' |
'unknown'. `labeling_regime` records whether the URL tier was in play at
all ('target_url') or not ('target_only') -- see below.

Episodes: consecutive events sharing the same resolved label, with no
gap larger than the episode gap between them, are grouped into one
"task episode" -- the unit Experiment 2 (task-type breakdown) computes
behavioral features and AUC over. A session can span multiple episodes;
episodes, not whole sessions, are the analysis unit here. Each episode
also carries a per-pass count of how its events were resolved
(n_direct/n_url/n_bracketed/n_carry_forward/n_carry_back/n_unknown), so
downstream analysis can
tell a solidly-anchored episode from one held together entirely by
carry-forward -- without this module having to take a position on how
those should be weighted.

Two windows, not one
---------------------
CARRY_FORWARD_SECONDS and EPISODE_GAP_SECONDS were a single constant
(TASK_GAP_SECONDS) until they were split apart, which meant one number
was silently answering two different questions: "is this event still
part of the task I last positively identified?" and "is this the same
continuous bout of activity?" They default to the same 60s so existing
results reproduce exactly, but they are independent and should be swept
independently -- see sweep_gap.py.

Adaptive windows (--adaptive)
------------------------------
A fixed 60s window encodes an assumption about interaction tempo that
holds for scripted agents and humans but not for agents whose natural
rhythm is hours (bursts of activity separated by long idle gaps). Rather
than hand-setting a window per architecture -- which would fit a labeling
parameter to the very architectures whose detection performance is the
dependent variable -- --adaptive derives the window from each session's
*own* inter-event interval distribution. The rule is identical for every
session; only the data differs, so a slow agent gets a long window
without anyone having chosen one for it. The window actually used is
recorded on every output row, so any result stays auditable.

Comparability across collection regimes (--ignore-url)
-------------------------------------------------------
URL capture landed partway through data collection, so `unknown` rate is
now partly a property of WHEN a session was recorded rather than of how
the agent behaved. Comparing a URL-era architecture's unknown rate
against a pre-URL one measures instrumentation, not behavior -- and the
resulting table looks perfectly reasonable.

Two guards. Every event and episode carries `labeling_regime`
('target_only' | 'target_url') so no analysis silently pools them. And
--ignore-url relabels URL-era data as if it were pre-URL, giving a
genuinely comparable column across the whole dataset. Report
cross-architecture claims under a uniform regime; use the URL tier for
forward-looking analysis.

Usage
-----
    # Against the live DB (run from inside the Charweb repo):
    python task_labeling.py [--events-out labeled_events.csv] [--episodes-out task_episodes.csv]

    # Against an exported CSV:
    python task_labeling.py --csv "C:/Coding/DATA/Summary/ai_raw_combined.csv" \\
        --events-out ai_labeled_events.csv --episodes-out ai_task_episodes.csv

    # Per-session adaptive windows instead of a fixed 60s:
    python task_labeling.py --csv ai_raw_combined.csv --adaptive

    # Recover coverage on a pre-URL-capture export:
    python task_labeling.py --csv human_raw_combined.csv --bidirectional

    # Label URL-era data in legacy mode, for comparison against old data:
    python task_labeling.py --csv new_export.csv --ignore-url

Requirements: pip install pandas (plus, for DB mode, this project's own Flask/SQLAlchemy deps)
"""
import argparse
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import NamedTuple, Optional
from urllib.parse import urlsplit

import numpy as np
import pandas as pd

# How long an ambiguous event may sit after the last positively-labeled event
# and still inherit its label.
CARRY_FORWARD_SECONDS = 60

# How large a gap between consecutive events may be before they count as
# separate activity bouts (and therefore separate episodes), even when they
# carry the same task label.
EPISODE_GAP_SECONDS = 60

# Deprecated: kept so anything still importing the old single constant keeps
# working. Prefer the two above -- they are what the code actually reads.
TASK_GAP_SECONDS = 60

# --- adaptive window parameters (only used with --adaptive) -----------------
# The window is a quantile of the session's own inter-event gaps: high enough
# to span normal within-task pauses for that session, below its between-task
# pauses. The episode quantile is the looser of the two because ending an
# episode is the more destructive call -- a split episode can't be un-split
# downstream, whereas an over-long carry-forward is visible in n_carry_forward.
ADAPTIVE_CARRY_QUANTILE = 0.90
ADAPTIVE_EPISODE_QUANTILE = 0.95
# Clamp, so a session of near-simultaneous events doesn't collapse the window
# to zero and a session of two events an hour apart doesn't make it unbounded.
ADAPTIVE_MIN_SECONDS = 5
ADAPTIVE_MAX_SECONDS = 3600
# Below this many events the gap distribution is too thin to estimate from;
# such sessions fall back to the fixed constants.
ADAPTIVE_MIN_EVENTS = 10

# What to do with an ambiguous event bracketed by two anchors carrying
# DIFFERENT labels (bidirectional mode only). 'nearest' is the default
# because it is the one policy that uses both anchors' timing; run
# validate_carry.py to find out which actually wins on your data.
CONFLICT_POLICIES = ('nearest', 'prefer_previous', 'unknown')
DEFAULT_CONFLICT_POLICY = 'nearest'

# Every value `resolved_via` can take, strongest evidence first. Ordering is
# load-bearing for reporting only, not for labeling.
RESOLUTION_KINDS = ('direct', 'url', 'bracketed', 'carry_forward',
                    'carry_back', 'unknown')

# Target values that unambiguously identify one task type. Checked in
# this order; first match wins. Extend this table as new pages/fields
# are added -- just make sure a new entry doesn't overlap an existing one.
DIRECT_TARGET_RULES = [
    ('profile_edit', {'about_me'}),
    ('search', {'q'}),
    ('feed_browse', {'post'}),
    ('timed_dungeon', {
        'mission-timer', 'timer-display', 'start-timer', 'dungeon-screen',
        'daily-signin-btn', 'character-name-input', 'create-character-btn',
        'revive-character-btn', 'daily-shop-link', 'rankings-link',
        'dungeon-fight-btn', 'dungeon-flee-btn', 'dungeon-explore-btn',
        'dungeon-rest-btn', 'dungeon-descend-btn', 'dungeon-ascend-btn',
        'dungeon-cast-heal-btn', 'buy-magic-potion-btn', 'buy-spellbook-btn',
    }),
    ('signup_login', {'username', 'email', 'password', 'password2',
                       'accept_terms', 'remember_me'}),
    ('chat', {'message-input', 'send-btn', 'search-users', 'chat-header',
              'chat-messages'}),
]

# Targets generated per-loop-iteration (e.g. id="equip-item-{{ inv.id }}",
# or individual feed post elements id="post-{{ post.id }}") can't be
# listed exactly, so match by prefix instead. Confirmed against real
# collected data (ai_raw_combined.csv has post-144, post-150, ...).
DIRECT_TARGET_PREFIXES = [
    ('timed_dungeon', ('allocate-point-', 'equip-item-', 'buy-equipment-')),
    ('chat', ('user-item-',)),
    ('feed_browse', ('post-',)),
]

# Pages that host exactly one task. Paths are normalized (scheme/host/query
# stripped, trailing slash removed except for root) before matching, so a
# full href, a bare absolute path, and a path carrying a query string all
# resolve identically. Routes deliberately absent: /team*, /terms, /admin*
# -- those aren't part of the standardized task set and should stay
# unlabeled rather than be forced into a category.
DIRECT_URL_RULES = [
    ('profile_edit', {'/edit_profile'}),
    ('search', {'/explore'}),
    ('feed_browse', {'/', '/home', '/all_users'}),
    ('timed_dungeon', {'/daily', '/ranking'}),
    ('signup_login', {'/login', '/register', '/reset_password_request',
                       '/logout'}),
    ('chat', {'/chat'}),
]

DIRECT_URL_PREFIXES = [
    ('timed_dungeon', ('/daily/',)),
    ('chat', ('/chat/',)),
    ('signup_login', ('/login/quick/', '/reset_password/')),
    ('feed_browse', ('/user/', '/comment/', '/like/', '/follow/', '/unfollow/')),
]


def _direct_label(target: Optional[str]) -> Optional[str]:
    if not target:
        return None
    for label, targets in DIRECT_TARGET_RULES:
        if target in targets:
            return label
    for label, prefixes in DIRECT_TARGET_PREFIXES:
        if target.startswith(prefixes):
            return label
    return None


def _normalize_path(url: Optional[str]) -> Optional[str]:
    """Reduce a URL of any shape (full href, absolute path, path+query) to a
    bare normalized path. Returns None for anything unusable, including the
    float NaN pandas produces for an empty CSV cell."""
    if not isinstance(url, str) or not url.strip():
        return None
    path = urlsplit(url).path or '/'
    if len(path) > 1:
        path = path.rstrip('/')
    return path or '/'


def _url_label(url: Optional[str]) -> Optional[str]:
    """Task implied by the page an event happened on. Weaker evidence than a
    distinctive element id -- hence checked only after _direct_label -- but
    available on every event, including the mousemove/scroll traffic that
    carries no target at all."""
    path = _normalize_path(url)
    if path is None:
        return None

    # A `q=` query parameter means a search was actually submitted, which is
    # more specific than whatever page it landed on.
    query = urlsplit(url).query
    if query and 'q=' in query:
        return 'search'

    for label, paths in DIRECT_URL_RULES:
        if path in paths:
            return label
    for label, prefixes in DIRECT_URL_PREFIXES:
        if path.startswith(prefixes):
            return label
    return None


class RawEvent(NamedTuple):
    """Source-agnostic event representation -- both the DB path (TrackedAction
    rows) and the CSV path (dataframe rows) get adapted into this before
    hitting the shared labeling logic below."""
    session_id: str
    owner: Optional[str]  # username, or whatever identifies "who" for the per-owner breakdown
    timestamp: datetime
    action_type: str
    target: Optional[str]
    url: Optional[str] = None  # defaulted: rows predating URL capture have none


@dataclass
class LabeledEvent:
    session_id: str
    owner: Optional[str]
    timestamp: datetime
    action_type: str
    target: Optional[str]
    url: Optional[str]
    task_type: str
    # 'direct' | 'url' | 'bracketed' | 'carry_forward' | 'carry_back' | 'unknown'
    resolved_via: str
    carry_window_s: float  # window in force for this session, for auditability
    labeling_regime: str   # 'target_only' | 'target_url'


def session_windows(events, adaptive=False,
                    carry_seconds=None, episode_seconds=None):
    """Return (carry_forward_seconds, episode_gap_seconds) for one session.

    In fixed mode these are the module constants (or whatever was passed in).
    In adaptive mode they are quantiles of this session's own inter-event gap
    distribution -- the same rule for every session, so no per-architecture
    hand-tuning is involved, but a session whose natural rhythm is hours gets
    an hours-long window automatically.
    """
    carry = CARRY_FORWARD_SECONDS if carry_seconds is None else carry_seconds
    episode = EPISODE_GAP_SECONDS if episode_seconds is None else episode_seconds
    if not adaptive or len(events) < ADAPTIVE_MIN_EVENTS:
        return float(carry), float(episode)

    ts = np.array([e.timestamp.timestamp() for e in events], dtype=float)
    gaps = np.diff(ts)
    gaps = gaps[np.isfinite(gaps) & (gaps >= 0)]
    if gaps.size == 0:
        return float(carry), float(episode)

    def clamp(v):
        return float(np.clip(v, ADAPTIVE_MIN_SECONDS, ADAPTIVE_MAX_SECONDS))

    return (clamp(np.quantile(gaps, ADAPTIVE_CARRY_QUANTILE)),
            clamp(np.quantile(gaps, ADAPTIVE_EPISODE_QUANTILE)))


def anchor_labels(events, use_url=True):
    """Passes 1 and 2 only: the positively-evidenced label for each event, or
    (None, None) where neither the element id nor the page settles it.

    Split out from carry-forward so validate_carry.py can hold an anchor out
    and ask whether fill would have recovered it.
    """
    out = []
    for ev in events:
        # Pass 1: distinctive element id. Pass 2: page the event happened on.
        label, via = _direct_label(ev.target), 'direct'
        if label is None and use_url:
            label, via = _url_label(ev.url), 'url'
        out.append((label, via) if label is not None else (None, None))
    return out


def _neighbor_anchors(base):
    """For each position, the index of the nearest anchor strictly before it
    and strictly after it (None where there is none)."""
    n = len(base)
    prev_idx, next_idx = [None] * n, [None] * n

    last = None
    for i in range(n):
        prev_idx[i] = last
        if base[i][0] is not None:
            last = i

    nxt = None
    for i in range(n - 1, -1, -1):
        next_idx[i] = nxt
        if base[i][0] is not None:
            nxt = i

    return prev_idx, next_idx


def fill_from_anchors(events, base, carry_window_s, bidirectional=False,
                      conflict_policy=DEFAULT_CONFLICT_POLICY):
    """Pass 3. Given per-event anchor labels from anchor_labels(), resolve the
    gaps. Returns a list of (label, resolved_via) for every event.

    Forward-only (the default) reproduces the original behavior exactly: the
    window is measured from the last anchor, not from the previous event, so a
    run of ambiguous events all measure against the same point and the run
    goes unknown once it passes the window.
    """
    window = timedelta(seconds=carry_window_s)
    prev_idx, next_idx = _neighbor_anchors(base)
    out = []

    for i, ev in enumerate(events):
        if base[i][0] is not None:          # already positively labeled
            out.append(base[i])
            continue

        p = prev_idx[i]
        q = next_idx[i] if bidirectional else None
        p_ok = p is not None and (ev.timestamp - events[p].timestamp) <= window
        q_ok = q is not None and (events[q].timestamp - ev.timestamp) <= window

        if p_ok and q_ok:
            lp, lq = base[p][0], base[q][0]
            if lp == lq:
                # Bracketed by agreeing anchors -- the strongest inference
                # available short of a positive match.
                out.append((lp, 'bracketed'))
            elif conflict_policy == 'unknown':
                out.append(('unknown', 'unknown'))
            elif conflict_policy == 'prefer_previous':
                out.append((lp, 'carry_forward'))
            else:  # 'nearest'
                dp = ev.timestamp - events[p].timestamp
                dq = events[q].timestamp - ev.timestamp
                out.append((lp, 'carry_forward') if dp <= dq else (lq, 'carry_back'))
        elif p_ok:
            out.append((base[p][0], 'carry_forward'))
        elif q_ok:
            out.append((base[q][0], 'carry_back'))
        else:
            out.append(('unknown', 'unknown'))

    return out


def label_session_events(events, carry_window_s=None, use_url=True,
                         bidirectional=False,
                         conflict_policy=DEFAULT_CONFLICT_POLICY):
    """events: RawEvent tuples for ONE session, already ordered by timestamp."""
    if carry_window_s is None:
        carry_window_s = CARRY_FORWARD_SECONDS

    base = anchor_labels(events, use_url=use_url)
    filled = fill_from_anchors(events, base, carry_window_s,
                               bidirectional=bidirectional,
                               conflict_policy=conflict_policy)

    # A session only counts as URL-era if the tier was enabled AND the rows
    # actually carry URLs -- an export without the column is target_only no
    # matter how the labeler was invoked.
    url_seen = any(isinstance(ev.url, str) and ev.url.strip() for ev in events)
    regime = 'target_url' if (use_url and url_seen) else 'target_only'

    return [LabeledEvent(ev.session_id, ev.owner, ev.timestamp, ev.action_type,
                          ev.target, ev.url, label, via, carry_window_s, regime)
            for ev, (label, via) in zip(events, filled)]


def build_episodes(labeled_events, episode_gap_s=None):
    """Collapse consecutive same-label events (within the episode gap) into
    episodes -- the unit Experiment 2 computes per-task features over."""
    if episode_gap_s is None:
        episode_gap_s = EPISODE_GAP_SECONDS

    episodes = []
    current = None

    def _new(ev):
        ep = {'session_id': ev.session_id, 'owner': ev.owner,
              'task_type': ev.task_type, 'start': ev.timestamp,
              'end': ev.timestamp, 'n_events': 1,
              'episode_gap_s': episode_gap_s,
              'labeling_regime': ev.labeling_regime}
        for kind in RESOLUTION_KINDS:
            ep['n_' + kind] = 0
        ep['n_' + ev.resolved_via] = 1
        return ep

    for ev in labeled_events:
        if current is None:
            current = _new(ev)
            continue

        gap = (ev.timestamp - current['end']).total_seconds()
        if ev.task_type == current['task_type'] and gap <= episode_gap_s:
            current['end'] = ev.timestamp
            current['n_events'] += 1
            current['n_' + ev.resolved_via] += 1
        else:
            episodes.append(current)
            current = _new(ev)

    if current is not None:
        episodes.append(current)

    return pd.DataFrame(episodes)


def _label_grouped_events(raw_events_by_session, adaptive=False,
                          carry_seconds=None, episode_seconds=None,
                          use_url=True, bidirectional=False,
                          conflict_policy=DEFAULT_CONFLICT_POLICY):
    """raw_events_by_session: dict[session_id] -> list[RawEvent] (unordered ok).
    Returns (per_event_df, per_episode_df)."""
    all_events = []
    all_episode_frames = []
    for session_id, events in raw_events_by_session.items():
        events = sorted(events, key=lambda e: e.timestamp)
        carry_s, episode_s = session_windows(events, adaptive=adaptive,
                                             carry_seconds=carry_seconds,
                                             episode_seconds=episode_seconds)
        labeled = label_session_events(events, carry_window_s=carry_s,
                                       use_url=use_url,
                                       bidirectional=bidirectional,
                                       conflict_policy=conflict_policy)
        all_events.extend(labeled)
        all_episode_frames.append(build_episodes(labeled, episode_gap_s=episode_s))

    events_df = pd.DataFrame([e.__dict__ for e in all_events])
    episodes_df = pd.concat(all_episode_frames, ignore_index=True) if all_episode_frames else pd.DataFrame()
    return events_df, episodes_df


def read_db_events():
    """dict[session_uid] -> list[RawEvent] straight from the live DB, unlabeled.
    Split out so validate_carry.py can reuse the loader without labeling."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app import app, db
    from app.models import TrackedAction

    with app.app_context():
        session_uids = [row[0] for row in db.session.query(TrackedAction.session_uid)
                         .filter(TrackedAction.session_uid.isnot(None))
                         .distinct().all()]

        raw_events_by_session = {}
        for uid in session_uids:
            rows = db.session.query(TrackedAction) \
                .filter(TrackedAction.session_uid == uid) \
                .order_by(TrackedAction.timestamp.asc()).all()
            if not rows:
                continue
            owner = rows[0].user.username if rows[0].user else None
            # getattr guard so this still runs against a DB that predates the
            # url column / migration b3f7c21a90de.
            raw_events_by_session[uid] = [
                RawEvent(uid, owner, r.timestamp, r.action_type, r.target,
                         getattr(r, 'url', None))
                for r in rows
            ]

    return raw_events_by_session


def label_from_db(**opts):
    """Returns (per_event_df, per_episode_df) across every session in the live DB."""
    return _label_grouped_events(read_db_events(), **opts)


def _read_csv_tolerant(path):
    """Try the fast C parser first; fall back to the slower but more forgiving
    python engine (skipping unparseable rows) if the file has a malformed
    row -- e.g. a truncated last line -- and warn loudly either way so a
    corrupt export doesn't get silently treated as complete data."""
    try:
        return pd.read_csv(path)
    except pd.errors.ParserError as exc:
        print(f"WARNING: {path} failed to parse with the standard CSV engine "
              f"({exc}). Retrying with the tolerant parser, which will skip "
              f"any malformed rows -- check the file for truncation/corruption.")
        df = pd.read_csv(path, engine='python', on_bad_lines='warn')
        print(f"WARNING: loaded {len(df)} rows from {path} after skipping bad lines. "
              f"This dataset may be incomplete.")
        return df


def read_csv_events(csv_path, session_col='session_label',
                    username_col='username', url_col='url'):
    """dict[session_label] -> list[RawEvent] from an exported CSV, unlabeled.
    Split out so validate_carry.py can reuse the loader without labeling."""
    df = _read_csv_tolerant(csv_path)

    missing = {session_col, username_col, 'timestamp', 'action_type', 'target'} - set(df.columns)
    if missing:
        raise ValueError(f"{csv_path} is missing expected column(s): {sorted(missing)}. "
                          f"Actual columns: {list(df.columns)}")

    # Not all rows carry microseconds (e.g. "...12:00:30" vs "...12:31:40.339000"),
    # so a fixed format inferred from the first rows breaks on the rest.
    df['timestamp'] = pd.to_datetime(df['timestamp'], format='mixed')
    df['target'] = df['target'].astype(object).where(df['target'].notna(), None)

    # URL is optional: exports predating URL capture don't have it, and every
    # event then falls through to target-matching + carry-forward exactly as
    # it did before.
    has_url = url_col in df.columns
    if has_url:
        df[url_col] = df[url_col].astype(object).where(df[url_col].notna(), None)
    else:
        print(f"NOTE: {csv_path} has no '{url_col}' column -- labeling with "
              f"target + carry-forward only (pre-URL-capture dataset).")

    raw_events_by_session = {}
    for session_id, group in df.groupby(session_col):
        owner = group[username_col].iloc[0] if username_col in group.columns else None
        raw_events_by_session[session_id] = [
            RawEvent(session_id, owner, row.timestamp, row.action_type, row.target,
                     getattr(row, url_col, None) if has_url else None)
            for row in group.itertuples()
        ]

    return raw_events_by_session


def label_from_csv(csv_path, session_col='session_label', username_col='username',
                   url_col='url', **opts):
    """Returns (per_event_df, per_episode_df) from an exported CSV such as
    ai_raw_combined.csv / human_raw_combined.csv."""
    return _label_grouped_events(
        read_csv_events(csv_path, session_col, username_col, url_col), **opts)


def resolution_breakdown_by_owner(events_df):
    """% of events resolved via each pass in RESOLUTION_KINDS, broken out per
    `owner` (username -- the finest-grained grouping actually available;
    there is no explicit "architecture" field in the schema today, so this
    is a proxy at best. If you want a true per-architecture unknown-rate
    for the Fenris coverage spot-check, that needs an actual `architecture`
    column added upstream -- this can't reconstruct it after the fact)."""
    if events_df.empty or 'owner' not in events_df.columns:
        return pd.DataFrame()
    counts = events_df.groupby(['owner', 'resolved_via']).size().unstack(fill_value=0)
    for col in RESOLUTION_KINDS:
        if col not in counts.columns:
            counts[col] = 0
    totals = counts.sum(axis=1)
    pct = counts.div(totals, axis=0) * 100
    pct = pct.rename(columns={c: f'{c}_pct' for c in pct.columns})
    return pd.concat([counts, pct.round(1), totals.rename('total_events')], axis=1) \
        .sort_values('unknown_pct', ascending=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--csv', default=None,
                         help='Path to an exported CSV (e.g. ai_raw_combined.csv). '
                              'If omitted, reads from the live Charweb DB instead.')
    parser.add_argument('--csv-session-col', default='session_label')
    parser.add_argument('--csv-username-col', default='username')
    parser.add_argument('--csv-url-col', default='url',
                         help='Optional; absent in pre-URL-capture exports.')
    parser.add_argument('--carry-forward-seconds', type=float, default=None,
                         help=f'Carry-forward window (default {CARRY_FORWARD_SECONDS}).')
    parser.add_argument('--episode-gap-seconds', type=float, default=None,
                         help=f'Episode boundary gap (default {EPISODE_GAP_SECONDS}).')
    parser.add_argument('--adaptive', action='store_true',
                         help="Derive both windows per session from that session's own "
                              'inter-event gap distribution instead of using fixed values.')
    parser.add_argument('--bidirectional', action='store_true',
                         help='Also fill from the nearest FOLLOWING anchor, not just the '
                              'preceding one. Recovers session-opening events and runs '
                              'that outlived the backward window. Off by default so '
                              'existing results reproduce.')
    parser.add_argument('--conflict-policy', default=DEFAULT_CONFLICT_POLICY,
                         choices=CONFLICT_POLICIES,
                         help='Bidirectional only: what to do when the two bracketing '
                              f'anchors disagree (default {DEFAULT_CONFLICT_POLICY}).')
    parser.add_argument('--ignore-url', action='store_true',
                         help='Skip the URL tier even when URLs are present, so URL-era '
                              'data can be labeled the same way pre-URL data was. Use '
                              'this for any comparison that spans both collection eras.')
    parser.add_argument('--events-out', default='labeled_events.csv')
    parser.add_argument('--episodes-out', default='task_episodes.csv')
    parser.add_argument('--owner-breakdown-out', default='resolution_by_owner.csv')
    args = parser.parse_args()

    common = dict(adaptive=args.adaptive,
                  carry_seconds=args.carry_forward_seconds,
                  episode_seconds=args.episode_gap_seconds,
                  use_url=not args.ignore_url,
                  bidirectional=args.bidirectional,
                  conflict_policy=args.conflict_policy)

    if args.csv:
        events_df, episodes_df = label_from_csv(
            args.csv, args.csv_session_col, args.csv_username_col,
            args.csv_url_col, **common)
    else:
        events_df, episodes_df = label_from_db(**common)

    if events_df.empty:
        print("No events found -- nothing to label.")
        return

    events_df.to_csv(args.events_out, index=False)
    episodes_df.to_csv(args.episodes_out, index=False)

    if args.adaptive:
        w = events_df['carry_window_s']
        print(f"Window mode: adaptive per-session "
              f"(carry-forward min {w.min():.0f}s / median {w.median():.0f}s / max {w.max():.0f}s)")
    else:
        print(f"Window mode: fixed "
              f"carry={args.carry_forward_seconds or CARRY_FORWARD_SECONDS}s "
              f"episode={args.episode_gap_seconds or EPISODE_GAP_SECONDS}s")

    fill = (f"bidirectional (conflicts: {args.conflict_policy})"
            if args.bidirectional else 'forward-only')
    print(f"Fill mode:   {fill}")
    regimes = events_df['labeling_regime'].value_counts()
    print(f"Regime:      {', '.join(f'{k} {v}' for k, v in regimes.items())}"
          + ('   [URL tier disabled via --ignore-url]' if args.ignore_url else ''))
    if len(regimes) > 1:
        print("  WARNING: this dataset mixes collection regimes. Do not compare "
              "unknown-rates across them -- rerun with --ignore-url for a "
              "uniformly-labeled column.")

    print(f"Labeled {len(events_df)} events across {events_df['session_id'].nunique()} sessions")
    print(events_df['task_type'].value_counts().to_string())
    print(f"\nResolution breakdown (overall):")
    print(events_df['resolved_via'].value_counts().to_string())

    owner_breakdown = resolution_breakdown_by_owner(events_df)
    if not owner_breakdown.empty:
        owner_breakdown.to_csv(args.owner_breakdown_out)
        print(f"\nResolution breakdown by owner (top 10 by unknown %):")
        print(owner_breakdown.head(10).to_string())
        print(f"Wrote {args.owner_breakdown_out}")

    print(f"\n{len(episodes_df)} task episodes")
    print(f"Wrote {args.events_out} and {args.episodes_out}")


if __name__ == '__main__':
    main()
