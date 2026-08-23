"""
Bootstrap CIs and a formatted per-architecture recall report.

Reads loao_eval.py's raw per-session predictions (loao_predictions.csv),
kept as a separate script on purpose: loao_eval.py's job is to produce
honest raw numbers once; this script's job is to summarize them for a
write-up, and can be re-run (different bootstrap sample counts, different
formatting) without re-fitting any model.

Two bootstrap views, because architectures are not independent samples --
this project's core methodological point -- so "resample sessions" is only
valid within one architecture, never pooled across several:

  1. Per-arch bootstrap CI -- resample THAT architecture's own sessions
     with replacement, recompute recall, repeat. Valid on its own because
     all of an architecture's sessions came from the same generating
     process; this just asks "how much would this arch's recall estimate
     move around if we'd happened to collect a slightly different sample
     of its sessions." Matches the "27 human sessions from 7 people...
     treat it as an order of magnitude" framing in the sibling ESAP/SYSTEM
     project's threshold_report.txt.

  2. Per-family CLUSTER bootstrap CI -- resample ARCHITECTURES (not raw
     sessions) with replacement within a family, pool the resampled
     architectures' sessions, recompute recall. This is the statistically
     correct way to pool e.g. "all llm_scripted architectures" into one
     number: session-level resampling would treat 30 sessions from one
     lucky/unlucky architecture as 30 independent data points, when they
     are not -- the whole reason this project groups CV by arch at all.

Usage
-----
    python research/loao_eval.py --features engineered_features.csv
    python research/report_recall.py                       # reads loao_predictions.csv
    python research/report_recall.py --n-boot 5000 --seed 1
"""
import argparse
import sys

import numpy as np
import pandas as pd

MIN_SESSIONS_FOR_CI = 3  # below this, a bootstrap CI is mostly noise -- still
                         # computed, but flagged, not hidden.


def _percentile_ci(values, alpha=0.05):
    lo = np.percentile(values, 100 * alpha / 2)
    hi = np.percentile(values, 100 * (1 - alpha / 2))
    return lo, hi


def per_arch_ci(predictions, n_boot=2000, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for arch, g in predictions.groupby('arch'):
        correct = g['correct'].values
        n = len(correct)
        point = correct.mean()

        boot = np.empty(n_boot)
        for b in range(n_boot):
            sample = rng.choice(correct, size=n, replace=True)
            boot[b] = sample.mean()
        lo, hi = _percentile_ci(boot)

        rows.append({
            'arch': arch, 'family': g['family'].iloc[0], 'harness': g['harness'].iloc[0],
            'n_sessions': n, 'recall': round(point, 3),
            'ci_low': round(lo, 3), 'ci_high': round(hi, 3),
            'note': '' if n >= MIN_SESSIONS_FOR_CI
                   else f'n={n}: CI is not meaningful yet, treat as order-of-magnitude only',
        })
    return pd.DataFrame(rows).sort_values('n_sessions', ascending=False)


def family_cluster_ci(predictions, n_boot=2000, seed=0):
    """Resample ARCHITECTURES within a family, not sessions -- see module
    docstring for why session-level resampling would be wrong here."""
    rng = np.random.default_rng(seed)
    rows = []
    for family, g in predictions.groupby('family'):
        archs = g['arch'].unique()
        n_archs = len(archs)
        point = g['correct'].mean()

        # Pre-split into per-arch correctness arrays once, so each bootstrap
        # draw is just picking whole arrays, not re-filtering the dataframe.
        by_arch = [g.loc[g['arch'] == a, 'correct'].values for a in archs]

        boot = np.empty(n_boot)
        for b in range(n_boot):
            picks = rng.integers(0, n_archs, size=n_archs)
            pooled = np.concatenate([by_arch[i] for i in picks])
            boot[b] = pooled.mean()
        lo, hi = _percentile_ci(boot)

        rows.append({
            'family': family, 'n_archs': n_archs, 'n_sessions': len(g),
            'recall': round(point, 3), 'ci_low': round(lo, 3), 'ci_high': round(hi, 3),
            'note': '' if n_archs >= MIN_SESSIONS_FOR_CI
                   else f'{n_archs} architecture(s): CI is not meaningful yet',
        })
    return pd.DataFrame(rows).sort_values('n_sessions', ascending=False)


def to_markdown(per_arch_df, family_df):
    lines = ['# Per-architecture recall report\n']
    lines.append('## By architecture (bootstrap over that architecture\'s own sessions)\n')
    lines.append('| arch | family | harness | n | recall | 95% CI |')
    lines.append('|---|---|---|---:|---:|---|')
    for _, r in per_arch_df.iterrows():
        ci = f'[{r.ci_low}, {r.ci_high}]' + (f' — {r.note}' if r.note else '')
        lines.append(f'| {r.arch} | {r.family} | {r.harness} | {r.n_sessions} | {r.recall} | {ci} |')

    lines.append('\n## By family (cluster bootstrap over architectures, not sessions)\n')
    lines.append('| family | n archs | n sessions | recall | 95% CI |')
    lines.append('|---|---:|---:|---:|---|')
    for _, r in family_df.iterrows():
        ci = f'[{r.ci_low}, {r.ci_high}]' + (f' — {r.note}' if r.note else '')
        lines.append(f'| {r.family} | {r.n_archs} | {r.n_sessions} | {r.recall} | {ci} |')

    return '\n'.join(lines) + '\n'


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--predictions', default='loao_predictions.csv',
                    help='Output of research/loao_eval.py')
    ap.add_argument('--n-boot', type=int, default=2000)
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--per-arch-out', default='recall_ci_by_arch.csv')
    ap.add_argument('--family-out', default='recall_ci_by_family.csv')
    ap.add_argument('--markdown-out', default='recall_report.md')
    args = ap.parse_args()

    try:
        predictions = pd.read_csv(args.predictions)
    except FileNotFoundError:
        print(f'{args.predictions} not found -- run research/loao_eval.py first '
              f'(it writes this file as part of --per-arch-out).', file=sys.stderr)
        return 1

    if predictions.empty:
        print(f'{args.predictions} is empty -- nothing to report on (this happens '
              f'when loao_eval.py skipped per_arch_recall, e.g. only one class '
              f'present so far).', file=sys.stderr)
        return 1

    per_arch_df = per_arch_ci(predictions, n_boot=args.n_boot, seed=args.seed)
    family_df = family_cluster_ci(predictions, n_boot=args.n_boot, seed=args.seed)

    per_arch_df.to_csv(args.per_arch_out, index=False)
    family_df.to_csv(args.family_out, index=False)
    with open(args.markdown_out, 'w', encoding='utf-8') as f:
        f.write(to_markdown(per_arch_df, family_df))

    print('=== recall by architecture ===')
    print(per_arch_df.to_string(index=False))
    print('\n=== recall by family (cluster bootstrap over architectures) ===')
    print(family_df.to_string(index=False))
    print(f'\nWrote {args.per_arch_out}, {args.family_out}, {args.markdown_out}')

    return 0


if __name__ == '__main__':
    sys.exit(main())
