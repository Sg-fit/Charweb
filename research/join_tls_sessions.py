"""
Join captured TLS fingerprints to Charweb sessions.

extract_ja3.py gives one row per TLS handshake, keyed by (src_ip, src_port).
UserSession records the client address and port of each session's most recent
/api/track request. Because (ip, port) identifies exactly one TCP connection,
the join is exact rather than a fuzzy IP + time-window match.

Two honest caveats, both reported in the output rather than hidden:

  1. A browser opens several connections per session and reuses them via
     keep-alive, so a session's stored (ip, port) names only ONE of its
     connections. Handshakes on the others exist in the capture but have no
     matching session row. They are counted as `unmatched_handshakes`. A
     fallback IP+time match is offered (--fuzzy) for those, but it is
     genuinely ambiguous when several clients share a NAT address -- which
     includes agent runs launched from one machine.

  2. Only sessions that issued at least one /api/track POST have an address
     recorded at all. A session where the tracking script was blocked has no
     join key -- and that silence is itself worth reporting, since a client
     that loads pages but never reports events is anomalous.

Usage
-----
    # against the live DB
    python join_tls_sessions.py --fingerprints tls_fingerprints.csv

    # against an exported UserSession table
    python join_tls_sessions.py --fingerprints tls.csv --sessions sessions.csv

Requirements: pip install pandas (plus Flask/SQLAlchemy deps for DB mode)
"""
import argparse
import sys
from pathlib import Path

import pandas as pd


def load_sessions_from_db():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app import app, db
    from app.models import UserSession

    with app.app_context():
        rows = db.session.query(UserSession).filter(
            UserSession.remote_addr.isnot(None)).all()
        return pd.DataFrame([{
            'session_uid': r.session_uid,
            'user_id': r.user_id,
            'remote_addr': r.remote_addr,
            'remote_port': r.remote_port,
            'first_seen': r.first_seen,
            'last_seen': r.last_seen,
            'user_agent': r.user_agent,
            'ua_bot_flag': r.ua_bot_flag,
        } for r in rows])


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--fingerprints', required=True,
                    help='CSV from extract_ja3.py')
    ap.add_argument('--sessions', default=None,
                    help='CSV export of UserSession; omit to read the live DB')
    ap.add_argument('--fuzzy', action='store_true',
                    help='For handshakes with no exact port match, additionally '
                         'attempt an IP + time-window match (ambiguous under NAT).')
    ap.add_argument('--window-seconds', type=float, default=300)
    ap.add_argument('-o', '--out', default='session_tls.csv')
    args = ap.parse_args()

    fp = pd.read_csv(args.fingerprints)
    sess = (pd.read_csv(args.sessions) if args.sessions
            else load_sessions_from_db())

    if sess.empty:
        print('No sessions carry a client address. Either no traffic has been '
              'collected since migration c8e2f45b71ac, or nginx is not setting '
              'X-Forwarded-For / X-Client-Port (see deploy/charweb.nginx).')
        return 1

    sess['remote_port'] = pd.to_numeric(sess['remote_port'], errors='coerce')
    fp['src_port'] = pd.to_numeric(fp['src_port'], errors='coerce')

    exact = fp.merge(
        sess, left_on=['src_ip', 'src_port'], right_on=['remote_addr', 'remote_port'],
        how='left', suffixes=('', '_sess'))

    matched = exact[exact['session_uid'].notna()]
    unmatched = exact[exact['session_uid'].isna()]

    print(f'{len(fp)} handshakes, {len(sess)} sessions with an address')
    print(f'  exact (ip+port) matches : {len(matched)}')
    print(f'  unmatched handshakes    : {len(unmatched)}'
          '   <- other connections of the same sessions, mostly')

    if args.fuzzy and not unmatched.empty:
        sess2 = sess.copy()
        sess2['first_seen'] = pd.to_datetime(sess2['first_seen'], errors='coerce', utc=True)
        sess2['last_seen'] = pd.to_datetime(sess2['last_seen'], errors='coerce', utc=True)
        extra = []
        for _, hs in unmatched.iterrows():
            ts = pd.to_datetime(float(hs['timestamp']), unit='s', utc=True)
            cand = sess2[(sess2['remote_addr'] == hs['src_ip']) &
                         (sess2['first_seen'] - pd.Timedelta(seconds=args.window_seconds) <= ts) &
                         (ts <= sess2['last_seen'] + pd.Timedelta(seconds=args.window_seconds))]
            if len(cand) == 1:
                row = hs.copy()
                row['session_uid'] = cand.iloc[0]['session_uid']
                row['match_type'] = 'fuzzy_ip_time'
                extra.append(row)
        print(f'  fuzzy (ip+time) matches : {len(extra)} '
              f'(of {len(unmatched)}; the rest were ambiguous or had no candidate)')
        matched = pd.concat([matched.assign(match_type='exact_ip_port'),
                             pd.DataFrame(extra)], ignore_index=True)
    else:
        matched = matched.assign(match_type='exact_ip_port')

    matched.to_csv(args.out, index=False)

    # A session whose JA3 varies across its own connections is worth a look --
    # it should not happen for a single client, so it usually means the join
    # picked up someone else's handshake.
    if not matched.empty:
        per = matched.groupby('session_uid')['ja3_hash'].nunique()
        conflicted = per[per > 1]
        if len(conflicted):
            print(f'\nWARNING: {len(conflicted)} session(s) matched more than one '
                  f'distinct JA3. Treat those joins as unreliable:')
            print('  ' + ', '.join(map(str, conflicted.index[:10])))

        print(f'\nDistinct JA3 per user-agent family:')
        print(matched.groupby(matched['user_agent'].astype(str).str[:60])['ja3_hash']
              .nunique().sort_values(ascending=False).head(10).to_string())

    print(f'\nWrote {args.out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
