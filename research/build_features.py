"""
Reproducible feature engineering for Phase I (LOAO, harness-vs-model split).

Gap this fills: engineered_features.csv existed as an artifact with no
script on disk that produced it -- the logic only lived in notebooks outside
this repo, so Phase I wasn't reproducible from the repo alone.

Import, don't copy. The 9 behavioral features
(iv_mean, iv_cv, kd_mean, kd_cv, click_pct, keydown_pct, mousemove_pct,
scroll_pct, vel_mean) are computed by app.ai_defense.features_from_rows --
the SAME function the live AI-defense system calls to score a real session
(app.ai_defense.compute_session_features is a two-line wrapper around it).
This script never recomputes them independently. If it did, and the two
implementations ever drifted, research/loao_eval.py would be evaluating a
model against features it was never actually scored on in production --
exactly the failure mode documented across this project (see
C:\\Coding\\ESAP\\SYSTEM's core/features.py docstring for the sibling
project's version of the same rule).

Two modes:

    DB mode (default) -- reads every session in the live Charweb DB.
        python research/build_features.py -o engineered_features.csv

    CSV mode -- reads an export with session_uid/username/action_type/
    timestamp/details/url columns (the shape research/routes.py's
    /admin/tracking/export produces as of the session_uid+url fix).
        python research/build_features.py --csv export.csv -o engineered_features.csv

Output columns:
    session_uid, username, arch, family, harness, task, label,
    n_events, duration_s,
    iv_mean, iv_cv, kd_mean, kd_cv, click_pct, keydown_pct, mousemove_pct,
    scroll_pct, vel_mean

Sessions with fewer than 5 events get NaN feature columns (matching
features_from_rows' own threshold) but are still written out, with
n_events, so research/loao_eval.py can decide how to handle them rather
than having them silently disappear here. Sessions with no resolvable
username (anonymous traffic) are written with arch="unknown_anonymous" and
reported separately -- they cannot be placed on either axis of the
model/harness decomposition without one.
"""
import argparse
import csv as csv_module
import json
import sys
from collections import namedtuple
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from label_architecture import parse_identity

FEATURE_COLUMNS = ['iv_mean', 'iv_cv', 'kd_mean', 'kd_cv', 'click_pct',
                   'keydown_pct', 'mousemove_pct', 'scroll_pct', 'vel_mean']
OUTPUT_COLUMNS = (['session_uid', 'username', 'arch', 'family', 'harness', 'task', 'label',
                   'n_events', 'duration_s'] + FEATURE_COLUMNS)

Row = namedtuple('Row', ['timestamp', 'action_type', 'details'])


def _session_row(session_uid, username, rows, features_from_rows):
    """rows: ordered list of Row (or TrackedAction) for one session.
    Returns one output dict, always -- feature columns are blank if the
    session is under the 5-event floor, never silently dropped."""
    ident = (parse_identity(username) if username
            else {'arch': 'unknown_anonymous', 'family': 'unknown',
                  'harness': 'unknown', 'task': 'unknown', 'label': 'unknown'})

    timestamps = [r.timestamp for r in rows]
    duration_s = (max(timestamps) - min(timestamps)).total_seconds() if len(timestamps) > 1 else 0.0

    feats = features_from_rows(rows) or {}
    out = {
        'session_uid': session_uid,
        'username': username or '',
        'arch': ident['arch'], 'family': ident['family'],
        'harness': ident['harness'], 'task': ident['task'], 'label': ident['label'],
        'n_events': len(rows),
        'duration_s': round(duration_s, 3),
    }
    for col in FEATURE_COLUMNS:
        out[col] = feats.get(col, '')
    return out


def build_from_db():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app import app, db
    from app.models import TrackedAction, User
    from app.ai_defense import features_from_rows
    import sqlalchemy as sa

    with app.app_context():
        # session_uid -> the user_id with the most events under that session
        # (there should only ever be one; this is defensive, not expected).
        counts = db.session.execute(
            sa.select(TrackedAction.session_uid, TrackedAction.user_id, sa.func.count())
            .where(TrackedAction.session_uid.isnot(None))
            .group_by(TrackedAction.session_uid, TrackedAction.user_id)
        ).all()
        best_user = {}
        for session_uid, user_id, cnt in counts:
            prev = best_user.get(session_uid)
            if prev is None or cnt > prev[1]:
                best_user[session_uid] = (user_id, cnt)

        user_ids = {uid for uid, _ in best_user.values() if uid is not None}
        usernames = {}
        if user_ids:
            for uid, uname in db.session.execute(
                    sa.select(User.id, User.username).where(User.id.in_(user_ids))):
                usernames[uid] = uname

        rows_out = []
        for session_uid, (user_id, _) in best_user.items():
            events = db.session.scalars(
                sa.select(TrackedAction)
                .where(TrackedAction.session_uid == session_uid)
                .order_by(TrackedAction.timestamp.asc())
            ).all()
            if not events:
                continue
            username = usernames.get(user_id)
            rows_out.append(_session_row(session_uid, username, events, features_from_rows))

        return rows_out


def build_from_csv(path):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app import app
    from app.ai_defense import features_from_rows
    from datetime import datetime

    with app.app_context():
        by_session = {}
        with open(path, newline='', encoding='utf-8') as f:
            reader = csv_module.DictReader(f)
            required = {'session_uid', 'username', 'action_type', 'timestamp'}
            missing = required - set(reader.fieldnames or [])
            if missing:
                raise SystemExit(f'{path}: missing required column(s) {sorted(missing)}. '
                                 f'Found: {reader.fieldnames}')
            for r in reader:
                sid = r.get('session_uid')
                if not sid:
                    continue
                ts_raw = r['timestamp']
                try:
                    ts = datetime.fromisoformat(ts_raw.replace('Z', '+00:00'))
                except ValueError:
                    continue  # unparseable timestamp -- drop the row, don't crash the run
                by_session.setdefault(sid, {'username': r.get('username') or None, 'rows': []})
                by_session[sid]['rows'].append(
                    Row(timestamp=ts, action_type=r.get('action_type', ''),
                        details=r.get('details') or None))

        rows_out = []
        for session_uid, data in by_session.items():
            events = sorted(data['rows'], key=lambda r: r.timestamp)
            rows_out.append(_session_row(session_uid, data['username'], events, features_from_rows))
        return rows_out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--csv', default=None,
                    help='Exported CSV (session_uid/username/action_type/timestamp/details). '
                         'Omit to read the live DB instead.')
    ap.add_argument('-o', '--out', default='engineered_features.csv')
    args = ap.parse_args()

    rows = build_from_csv(args.csv) if args.csv else build_from_db()
    if not rows:
        print('No sessions found.', file=sys.stderr)
        return 1

    with open(args.out, 'w', newline='') as f:
        w = csv_module.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        w.writeheader()
        w.writerows(rows)

    n_total = len(rows)
    n_scored = sum(1 for r in rows if r['iv_mean'] != '')
    n_short = n_total - n_scored
    n_anon = sum(1 for r in rows if r['arch'] == 'unknown_anonymous')
    print(f'{n_total} sessions -> {args.out}')
    print(f'  {n_scored} with >=5 events (feature columns populated)')
    print(f'  {n_short} under the 5-event floor (feature columns blank, kept anyway)')
    if n_anon:
        print(f'  {n_anon} anonymous (no resolvable username -> arch=unknown_anonymous, '
              f'excluded from the model/harness axes until identified)')

    by_arch = {}
    for r in rows:
        by_arch.setdefault(r['arch'], 0)
        by_arch[r['arch']] += 1
    print('\nsessions per arch:')
    for arch, n in sorted(by_arch.items(), key=lambda kv: -kv[1]):
        print(f'  {arch:24s} {n}')

    return 0


if __name__ == '__main__':
    sys.exit(main())
