"""
Per-architecture batch verification -- run this after every collection batch,
before moving on to the next one.

Fills a gap task_labeling.py's own resolution_breakdown_by_owner() flags in
its docstring: "there is no explicit architecture field in the schema
today... If you want a true per-architecture unknown-rate for the Fenris
coverage spot-check, that needs an actual architecture column added
upstream." label_architecture.py is that column (derived, not stored); this
script is the report built on top of it.

Reads task_labeling.py's own output (labeled_events.csv, one row per event,
columns include owner/session_id/task_type/resolved_via -- see LabeledEvent).
Run task_labeling.py first:

    python research/task_labeling.py                      # from the live DB
    python research/task_labeling.py --csv export.csv      # from an export
    python research/batch_report.py                        # this script

Produces three views, matching the three checks in the "after each batch"
step of the collection plan:

  1. architecture_summary.csv   -- one row per arch: session count, event
     count, resolution breakdown (% direct/url/bracketed/carry/unknown).
     This is "confirm Gemini/GPT sessions look reasonably classifiable
     (should be much better than Fenris's 80%)."

  2. architecture_task_mix.csv  -- arch x task_type crosstab, row-normalized.
     This is "does the task_mix roughly match what you told the agent to do
     that run?" -- read a row, compare it to what you asked for.

  3. architecture_session_index.csv -- one row per session: arch, family,
     harness, n_events, unknown_pct, dominant task_type. Sort by arch to see
     progress toward the 15-20-per-architecture target, and to pick which
     2-3 sessions per architecture to spot-check manually.

A username that resolves to family=human but LOOKS like a bot run (matches
label_architecture.py's own sanity check) is printed as a warning here too,
since that's exactly the kind of naming-discipline slip the collection plan
warns about ("every new session must follow strict, consistent naming").
"""
import argparse
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from label_architecture import parse_identity

RESOLUTION_KINDS = ['direct', 'url', 'bracketed', 'carry_forward', 'carry_back', 'unknown']

_LOOKS_LIKE_BOT = re.compile(r'^(ai_l\d|gpt|gemini|grok|copila?t|claude|fenris)', re.I)


def _label_owners(events_df):
    """owner -> parsed identity dict, computed once per distinct owner."""
    owners = events_df['owner'].fillna('').unique()
    return {o: parse_identity(o) for o in owners}


def architecture_summary(events_df, identities):
    """Per-arch resolution breakdown -- the unknown-rate check."""
    df = events_df.copy()
    df['arch'] = df['owner'].map(lambda o: identities[o]['arch'])
    df['family'] = df['owner'].map(lambda o: identities[o]['family'])
    df['harness'] = df['owner'].map(lambda o: identities[o]['harness'])

    n_sessions = df.groupby('arch')['session_id'].nunique().rename('n_sessions')

    counts = df.groupby(['arch', 'resolved_via']).size().unstack(fill_value=0)
    for col in RESOLUTION_KINDS:
        if col not in counts.columns:
            counts[col] = 0
    counts = counts[RESOLUTION_KINDS]
    totals = counts.sum(axis=1).rename('total_events')
    pct = counts.div(totals, axis=0) * 100
    pct = pct.rename(columns={c: f'{c}_pct' for c in pct.columns}).round(1)

    family = df.groupby('arch')['family'].first()
    harness = df.groupby('arch')['harness'].first()

    out = pd.concat([family, harness, n_sessions, totals, counts, pct], axis=1)
    return out.sort_values('unknown_pct', ascending=False)


def architecture_task_mix(events_df, identities):
    """arch x task_type crosstab, row-normalized -- the 'did it do the task
    I asked for' check."""
    df = events_df.copy()
    df['arch'] = df['owner'].map(lambda o: identities[o]['arch'])
    counts = pd.crosstab(df['arch'], df['task_type'])
    pct = counts.div(counts.sum(axis=1), axis=0) * 100
    return pct.round(1)


def architecture_session_index(events_df, identities):
    """One row per session -- for tracking progress toward the per-arch
    target and picking which sessions to spot-check."""
    df = events_df.copy()
    df['arch'] = df['owner'].map(lambda o: identities[o]['arch'])
    df['family'] = df['owner'].map(lambda o: identities[o]['family'])
    df['harness'] = df['owner'].map(lambda o: identities[o]['harness'])
    df['is_unknown'] = (df['resolved_via'] == 'unknown')

    rows = []
    for (session_id, owner), g in df.groupby(['session_id', 'owner']):
        dominant = g['task_type'].value_counts().idxmax()
        rows.append({
            'session_id': session_id,
            'owner': owner,
            'arch': g['arch'].iloc[0],
            'family': g['family'].iloc[0],
            'harness': g['harness'].iloc[0],
            'n_events': len(g),
            'unknown_pct': round(100 * g['is_unknown'].mean(), 1),
            'dominant_task_type': dominant,
            'n_distinct_task_types': g['task_type'].nunique(),
        })
    idx = pd.DataFrame(rows).sort_values(['arch', 'session_id'])

    print('\nSessions per architecture (progress toward the 15-20 target):')
    print(idx.groupby('arch')['session_id'].nunique().sort_values(ascending=False).to_string())

    return idx


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--events', default='labeled_events.csv',
                    help='Output of task_labeling.py (default: labeled_events.csv)')
    ap.add_argument('--summary-out', default='architecture_summary.csv')
    ap.add_argument('--task-mix-out', default='architecture_task_mix.csv')
    ap.add_argument('--session-index-out', default='architecture_session_index.csv')
    args = ap.parse_args()

    events_df = pd.read_csv(args.events)
    if events_df.empty:
        print(f'{args.events}: no rows.', file=sys.stderr)
        return 1
    if 'owner' not in events_df.columns:
        print(f"{args.events}: no 'owner' column -- is this really "
              f"task_labeling.py's output?", file=sys.stderr)
        return 1

    events_df['owner'] = events_df['owner'].fillna('')
    identities = _label_owners(events_df)

    summary = architecture_summary(events_df, identities)
    summary.to_csv(args.summary_out)
    print(f'=== architecture_summary ({args.summary_out}) ===')
    print(summary.to_string())

    task_mix = architecture_task_mix(events_df, identities)
    task_mix.to_csv(args.task_mix_out)
    print(f'\n=== architecture_task_mix ({args.task_mix_out}) ===')
    print(task_mix.to_string())

    idx = architecture_session_index(events_df, identities)
    idx.to_csv(args.session_index_out, index=False)
    print(f'\nWrote {args.session_index_out} ({len(idx)} sessions)')

    suspects = sorted({o for o, ident in identities.items()
                       if ident['family'] == 'human' and _LOOKS_LIKE_BOT.match(o)})
    if suspects:
        print(f'\nWARNING: {len(suspects)} username(s) look like a bot run but were '
              f'labeled human -- check naming discipline (see the collection plan\'s '
              f'"strict, consistent naming" note):')
        print('  ' + ', '.join(suspects[:10]))

    return 0


if __name__ == '__main__':
    sys.exit(main())
