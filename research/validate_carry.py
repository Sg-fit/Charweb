"""
Holdout validation for carry-forward / carry-back / bracketed fill.

Every fill rule in task_labeling.py is an assumption: that an event with
no usable target and no usable URL belongs to whatever task the nearest
anchor belongs to. That assumption has never been measured. The 60s
window, the decision to look only backward, the conflict policy -- all
were picked by judgement, and swapping one judgement for another
(exponential decay, a per-architecture window) would just be a different
guess wearing more math.

It can be measured, though, on data you already have and with no hand
labeling. Events resolved by pass 1 or 2 carry ground truth *by
construction*: their label came from a distinctive element id or an
unambiguous page. So: take each such anchor, hide it, ask what fill
would have inferred from the *remaining* anchors, and check the answer.
Bin by the gap to the anchor actually used and you get an empirical
accuracy-vs-gap curve for each fill direction -- which tells you where
the real window is instead of asserting one, and gives a principled
basis for confidence weights later.

Read the results with two caveats in mind.

Anchors are a biased sample of the events fill actually runs on. They
are, by definition, events that touched a distinctive element or a
recognizable page, which is not a random draw from the ambiguous
population -- a click on `about_me` sits in a different part of a session
than a stray mousemove. The curve is still far better evidence than a
guessed constant, but it is an estimate on adjacent data, not on the
target population.

And the method degrades exactly where it is needed most. An architecture
with almost no anchors -- the Fenris case -- yields almost no holdout
samples, so its curve is the least estimable of any. The architecture
most in need of a tuned window is the one whose window can least be
tuned from data. That is worth reporting as a finding rather than
discovering in review.

Usage
-----
    python validate_carry.py --csv human_raw_combined.csv
    python validate_carry.py --csv ai_raw_combined.csv --ignore-url
    python validate_carry.py --by-owner --out carry_validation.csv

Requirements: pip install pandas
"""
import argparse
from datetime import timedelta

import numpy as np
import pandas as pd

import task_labeling as tl

# Upper edges, in seconds, of the gap bins the accuracy curve is reported over.
DEFAULT_BINS = [1, 5, 15, 30, 60, 120, 300, 900, 3600, float('inf')]


def _bin_label(gap_s, bins):
    lo = 0
    for hi in bins:
        if gap_s <= hi:
            return f'<={hi:g}s' if hi != float('inf') else f'>{lo:g}s'
        lo = hi
    return 'inf'


def holdout_session(events, base, bins, max_gap_s):
    """One row per held-out anchor: what each fill direction would have
    inferred for it, from the other anchors only, and whether that was right.

    Removing the anchor from `base` is what makes this a fair test -- an
    anchor left in place would trivially resolve itself.
    """
    n = len(events)
    anchor_idx = [i for i in range(n) if base[i][0] is not None]
    rows = []

    for i in anchor_idx:
        truth = base[i][0]
        held = list(base)
        held[i] = (None, None)          # hide it
        prev_idx, next_idx = tl._neighbor_anchors(held)
        p, q = prev_idx[i], next_idx[i]

        dp = (events[i].timestamp - events[p].timestamp).total_seconds() if p is not None else None
        dq = (events[q].timestamp - events[i].timestamp).total_seconds() if q is not None else None
        lp = held[p][0] if p is not None else None
        lq = held[q][0] if q is not None else None

        # Direction-by-direction, ignoring any window -- the window is what
        # we're trying to estimate, so applying one here would beg the question.
        row = {
            'owner': events[i].owner,
            'session_id': events[i].session_id,
            'truth': truth,
            'gap_prev_s': dp, 'gap_next_s': dq,
            'forward_pred': lp, 'backward_pred': lq,
            'forward_ok': (lp == truth) if lp is not None else None,
            'backward_ok': (lq == truth) if lq is not None else None,
            'bracketed': (lp is not None and lq is not None and lp == lq),
        }
        row['bracketed_ok'] = (lp == truth) if row['bracketed'] else None

        # Conflict policies only differ when both anchors exist and disagree.
        if lp is not None and lq is not None and lp != lq:
            row['conflict'] = True
            row['nearest_ok'] = ((lp if dp <= dq else lq) == truth)
            row['prefer_previous_ok'] = (lp == truth)
        else:
            row['conflict'] = False
            row['nearest_ok'] = None
            row['prefer_previous_ok'] = None

        # Bin on the gap to whichever anchor the default (nearest) rule uses.
        near = min([g for g in (dp, dq) if g is not None], default=None)
        row['gap_s'] = near
        row['gap_bin'] = _bin_label(near, bins) if near is not None else 'none'
        if max_gap_s is not None and near is not None and near > max_gap_s:
            continue
        rows.append(row)

    return rows


def _curve(df, col, bins):
    """Accuracy and support per gap bin for one prediction column."""
    sub = df[df[col].notna()]
    if sub.empty:
        return pd.DataFrame()
    order = [f'<={b:g}s' for b in bins if b != float('inf')]
    g = sub.groupby('gap_bin')[col].agg(['mean', 'size'])
    g = g.reindex([b for b in order if b in g.index] +
                  [b for b in g.index if b not in order])
    g.columns = [f'{col}_acc', f'{col}_n']
    g[f'{col}_acc'] = (100 * g[f'{col}_acc']).round(1)
    return g


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--csv', default=None)
    parser.add_argument('--csv-session-col', default='session_label')
    parser.add_argument('--csv-username-col', default='username')
    parser.add_argument('--csv-url-col', default='url')
    parser.add_argument('--ignore-url', action='store_true',
                         help='Validate using only element-id anchors, as pre-URL data does.')
    parser.add_argument('--max-gap-seconds', type=float, default=None,
                         help='Drop holdout samples whose nearest anchor is further than this.')
    parser.add_argument('--by-owner', action='store_true',
                         help='Also print a per-owner summary (the closest thing to '
                              'per-architecture available without an architecture column).')
    parser.add_argument('--out', default='carry_validation.csv')
    args = parser.parse_args()

    if args.csv:
        by_session = tl.read_csv_events(args.csv, args.csv_session_col,
                                        args.csv_username_col, args.csv_url_col)
    else:
        by_session = tl.read_db_events()

    bins = DEFAULT_BINS
    rows = []
    for session_id, events in by_session.items():
        events = sorted(events, key=lambda e: e.timestamp)
        base = tl.anchor_labels(events, use_url=not args.ignore_url)
        rows.extend(holdout_session(events, base, bins, args.max_gap_seconds))

    if not rows:
        print("No anchors to hold out -- this dataset has no positively-labeled "
              "events at all, so fill accuracy cannot be estimated from it. That "
              "is itself the finding: there is nothing here to carry forward FROM.")
        return

    df = pd.DataFrame(rows)
    df.to_csv(args.out, index=False)

    n_anchor = len(df)
    print(f"{n_anchor} held-out anchors across {df['session_id'].nunique()} sessions "
          f"({'element-id only' if args.ignore_url else 'element-id + URL'})\n")

    curve = pd.concat([_curve(df, c, bins) for c in
                       ('forward_ok', 'backward_ok', 'bracketed_ok')], axis=1)
    print("Fill accuracy (%) by gap to nearest anchor:")
    print(curve.to_string())

    print("\nOverall by direction:")
    for c in ('forward_ok', 'backward_ok', 'bracketed_ok'):
        sub = df[df[c].notna()]
        if not sub.empty:
            print(f"  {c[:-3]:<12} {100 * sub[c].mean():5.1f}%  (n={len(sub)})")

    conf = df[df['conflict']]
    print(f"\nConflicting brackets: {len(conf)} of {n_anchor} "
          f"({100 * len(conf) / n_anchor:.1f}%)")
    if not conf.empty:
        print("  Conflict policy accuracy on exactly those cases:")
        for c in ('nearest_ok', 'prefer_previous_ok'):
            print(f"    {c[:-3]:<18} {100 * conf[c].mean():5.1f}%")
        print("    unknown            n/a  (declines to answer; trades recall for precision)")

    if args.by_owner:
        print("\nPer-owner (few anchors == unreliable estimate for that owner):")
        agg = df.groupby('owner').agg(
            anchors=('truth', 'size'),
            forward_acc=('forward_ok', lambda s: round(100 * s.mean(), 1) if s.notna().any() else np.nan),
            backward_acc=('backward_ok', lambda s: round(100 * s.mean(), 1) if s.notna().any() else np.nan),
            median_gap_s=('gap_s', 'median'),
        ).sort_values('anchors')
        print(agg.to_string())
        print("\n  Owners at the top of this table are the ones whose fill window "
              "cannot be estimated from their own data. Say so explicitly rather "
              "than quietly applying the pooled window to them.")

    print(f"\nWrote {args.out}")
    print("\nThe gap bin where accuracy falls to chance is the empirically "
          "supported window -- use it to justify CARRY_FORWARD_SECONDS instead "
          "of defending 60.")


if __name__ == '__main__':
    main()
