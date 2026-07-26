"""
Sensitivity sweep for the task-labeling gap windows.

The point of this script is NOT to find the "best" window. It is to show
whether the conclusions drawn from labeled data survive the choice of
window at all. `TASK_GAP_SECONDS = 60` was a reasonable default picked
without evidence; a single tuned replacement would be equally
indefensible. A curve is defensible: if unknown-rate ordering and
downstream conclusions hold across two orders of magnitude of window,
the arbitrariness objection is answered. If they don't hold, that
instability is itself the finding and needs reporting rather than hiding
behind whichever value happened to be in the constant.

Sweeps the carry-forward window (and, with --sweep-episode, the episode
gap) across a grid, re-labels the whole dataset at each point, and
reports per-owner unknown/carry-forward rates plus episode counts. Also
runs the adaptive per-session rule as an extra row so it can be compared
against the fixed grid on the same axes.

Usage
-----
    # Fixed grid over the carry-forward window:
    python sweep_gap.py --csv ai_raw_combined.csv --out sweep_carry.csv

    # Include the episode gap as a second swept axis (grid is the cross
    # product -- this is len(grid)^2 relabels, so keep the grid small):
    python sweep_gap.py --csv ai_raw_combined.csv --sweep-episode

    # Custom grid:
    python sweep_gap.py --csv ai_raw_combined.csv --grid 30,60,300,3600

Reads the live DB instead when --csv is omitted, exactly like
task_labeling.py.

Requirements: pip install pandas
"""
import argparse

import pandas as pd

import task_labeling as tl

DEFAULT_GRID = [15, 30, 60, 120, 300, 900, 3600]


def _label(args, carry, episode, adaptive=False):
    common = dict(adaptive=adaptive, carry_seconds=carry, episode_seconds=episode)
    if args.csv:
        return tl.label_from_csv(args.csv, args.csv_session_col,
                                 args.csv_username_col, args.csv_url_col,
                                 **common)
    return tl.label_from_db(**common)


def _summarize(events_df, episodes_df, carry, episode, adaptive):
    """One row per (owner, grid point). Rates are fractions of that owner's
    events, so owners with wildly different event counts stay comparable."""
    rows = []
    total = events_df.groupby('owner').size()
    via = events_df.groupby(['owner', 'resolved_via']).size().unstack(fill_value=0)
    for kind in tl.RESOLUTION_KINDS:
        if kind not in via.columns:
            via[kind] = 0

    n_episodes = episodes_df.groupby('owner').size() if not episodes_df.empty else pd.Series(dtype=int)
    # Episodes with no positively-labeled event at all are held together
    # purely by carry-forward or are unknown runs -- worth watching, because
    # a window wide enough to drive unknown-rate down can manufacture these.
    if not episodes_df.empty:
        unanchored = episodes_df.assign(
            anchored=(episodes_df['n_direct'] + episodes_df['n_url']) > 0
        ).groupby('owner')['anchored'].apply(lambda s: int((~s).sum()))
    else:
        unanchored = pd.Series(dtype=int)

    for owner in total.index:
        n = int(total.loc[owner])
        rows.append({
            'owner': owner,
            'carry_forward_s': 'adaptive' if adaptive else carry,
            'episode_gap_s': 'adaptive' if adaptive else episode,
            'n_events': n,
            'unknown_pct': round(100 * via.loc[owner, 'unknown'] / n, 2),
            'carry_forward_pct': round(100 * via.loc[owner, 'carry_forward'] / n, 2),
            'direct_pct': round(100 * via.loc[owner, 'direct'] / n, 2),
            'url_pct': round(100 * via.loc[owner, 'url'] / n, 2),
            'n_episodes': int(n_episodes.get(owner, 0)),
            'n_unanchored_episodes': int(unanchored.get(owner, 0)),
        })
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--csv', default=None)
    parser.add_argument('--csv-session-col', default='session_label')
    parser.add_argument('--csv-username-col', default='username')
    parser.add_argument('--csv-url-col', default='url')
    parser.add_argument('--grid', default=None,
                         help='Comma-separated seconds. Default: '
                              + ','.join(str(g) for g in DEFAULT_GRID))
    parser.add_argument('--sweep-episode', action='store_true',
                         help='Also sweep the episode gap (cross product with the '
                              'carry-forward grid). Without this the episode gap is '
                              'held at its default so the carry-forward effect is '
                              'isolated.')
    parser.add_argument('--no-adaptive-row', action='store_true',
                         help='Skip the adaptive per-session comparison row.')
    parser.add_argument('--out', default='gap_sweep.csv')
    args = parser.parse_args()

    grid = [float(g) for g in args.grid.split(',')] if args.grid else DEFAULT_GRID

    points = ([(c, e) for c in grid for e in grid] if args.sweep_episode
              else [(c, tl.EPISODE_GAP_SECONDS) for c in grid])

    rows = []
    for carry, episode in points:
        events_df, episodes_df = _label(args, carry, episode)
        if events_df.empty:
            print("No events found -- nothing to sweep.")
            return
        rows.extend(_summarize(events_df, episodes_df, carry, episode, adaptive=False))
        print(f"  carry={carry:>7.0f}s episode={episode:>7.0f}s -> "
              f"{100 * (events_df['resolved_via'] == 'unknown').mean():5.2f}% unknown overall, "
              f"{len(episodes_df)} episodes")

    if not args.no_adaptive_row:
        events_df, episodes_df = _label(args, None, None, adaptive=True)
        rows.extend(_summarize(events_df, episodes_df, None, None, adaptive=True))
        print(f"  {'adaptive':>16} -> "
              f"{100 * (events_df['resolved_via'] == 'unknown').mean():5.2f}% unknown overall, "
              f"{len(episodes_df)} episodes")

    out = pd.DataFrame(rows)
    out.to_csv(args.out, index=False)

    print(f"\nUnknown % by owner across the carry-forward grid:")
    pivot = out.pivot_table(index='owner', columns='carry_forward_s',
                            values='unknown_pct', aggfunc='min')
    print(pivot.to_string())
    print(f"\nWrote {args.out}")
    print("\nRead this as a robustness check, not a tuning run: report the "
          "curve, and if the ordering of owners/architectures is stable across "
          "it, say so explicitly rather than defending any single value.")


if __name__ == '__main__':
    main()
