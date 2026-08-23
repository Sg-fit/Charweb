"""
Leave-one-architecture-out (LOAO) evaluation, plus the naive-vs-grouped CV
comparison that motivates why LOAO is the honest number.

Gap this fills: nothing in the repo did grouped CV. Defense System/
train_lr_model.py does a plain train_test_split on SYNTHETIC data (its own
docstring says so) -- it would reproduce a leaky, inflated number on real
data, not a corrected one, because a plain split lets two sessions from the
same architecture (e.g. two gemini_run* sessions) land on opposite sides of
the split. The model then partly memorizes "what gemini_run* sessions look
like" rather than learning what AI vs human looks like in general, and the
held-out score is inflated by exactly that leakage.

Three things this script produces, all as RAW numbers -- bootstrap CIs and
formatting are research/report_recall.py's job, not this script's, so the
two stay independently rerunnable:

  1. naive_vs_grouped  -- the same model, same data, scored two ways:
     plain StratifiedKFold (sessions shuffled freely -- what you'd get if
     you didn't think about grouping) vs StratifiedGroupKFold grouped by
     `arch` (no architecture split across train/test). The gap between
     these two numbers on THIS dataset is the leakage estimate.

  2. per_arch_recall  -- true leave-one-architecture-out via sklearn's
     LeaveOneGroupOut: train on every architecture except one, test only on
     the held-out one, for every architecture in turn. This is "would the
     model catch an architecture it never trained on" -- the question a
     real deployed defense system actually faces.

  3. arch comparison (--compare-archs) -- harness-vs-model isolation. Two
     architectures can run the same underlying model through different
     automation (e.g. arch=fenris via its own native harness vs
     arch=llm_claude via the standard Playwright harness driving the same
     model) -- these are recorded as two DIFFERENT arch values (see
     research/label_architecture.py), not one arch with two harnesses,
     because they're genuinely different data-generating processes even
     when the underlying model is identical. Comparing their per-arch
     recall side by side is what isolates "did detection come from the
     harness or the model" (the M4 risk item).

Small-sample behavior is deliberate, not an afterthought: this is meant to
run usefully on 1-20 sessions per architecture, not just at scale. Anything
that can't be computed honestly at the current sample size (a single class
present, a fold with only one class in training, fewer groups than
requested splits) is SKIPPED WITH A PRINTED REASON, never silently dropped
or crashed past -- see the "with n=1/n=3" comparison point in the collection
plan this supports; the first runs of this script are expected to skip most
of section 1 and most of section 2, and that's the correct behavior, not a
bug.

Usage
-----
    python research/loao_eval.py --features engineered_features.csv
    python research/loao_eval.py --features engineered_features.csv \\
        --compare-archs fenris,llm_claude
"""
import argparse
import sys
import warnings

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.metrics import recall_score, roc_auc_score
from sklearn.model_selection import (LeaveOneGroupOut, StratifiedGroupKFold,
                                     StratifiedKFold, cross_val_predict)
from sklearn.pipeline import Pipeline

FEATURE_COLUMNS = ['iv_mean', 'iv_cv', 'kd_mean', 'kd_cv', 'click_pct',
                   'keydown_pct', 'mousemove_pct', 'scroll_pct', 'vel_mean']


def load_usable(features_path):
    """Read engineered_features.csv, keep only rows the model can use.

    Drops: unknown_anonymous (no group to assign -- can't sit on either
    axis of the model/harness decomposition), rows with blank feature
    columns (under build_features.py's 5-event floor), and label=unknown.
    Every drop is counted and reported, never silent.
    """
    df = pd.read_csv(features_path)
    n0 = len(df)

    df = df[df['arch'] != 'unknown_anonymous']
    n1 = len(df)

    df = df[df['label'].isin(['human', 'ai'])]
    n2 = len(df)

    df = df.dropna(subset=FEATURE_COLUMNS)
    n3 = len(df)

    print(f'{features_path}: {n0} sessions total')
    if n0 != n1:
        print(f'  -{n0 - n1} unknown_anonymous (no architecture label)')
    if n1 != n2:
        print(f'  -{n1 - n2} unlabeled (label not human/ai)')
    if n2 != n3:
        print(f'  -{n2 - n3} under the 5-event feature floor')
    print(f'  {n3} usable for evaluation\n')

    return df.reset_index(drop=True)


def _pipeline():
    return Pipeline([
        ('imp', SimpleImputer(strategy='median')),
        ('clf', RandomForestClassifier(n_estimators=300, class_weight='balanced',
                                       random_state=0)),
    ])


def _class_check(y, context):
    """Return True if y has both classes; otherwise print why and return False."""
    classes = sorted(set(y))
    if len(classes) < 2:
        only = classes[0] if classes else 'nothing'
        print(f'  SKIPPED ({context}): only one class present ({only!r}). '
              f'Need at least one human and one ai session.')
        return False
    return True


def naive_vs_grouped(df, n_splits=5):
    print('=== naive (ungrouped) vs grouped (by arch) CV ===')
    X = df[FEATURE_COLUMNS]
    y = (df['label'] == 'ai').astype(int)
    groups = df['arch']

    if not _class_check(y, 'naive_vs_grouped'):
        return None

    n_splits_eff = min(n_splits, y.value_counts().min())
    if n_splits_eff < 2:
        print(f'  SKIPPED: smallest class has {y.value_counts().min()} sample(s), '
              f'need >=2 per class for even a 2-fold split.')
        return None
    if n_splits_eff < n_splits:
        print(f'  (using {n_splits_eff}-fold, not {n_splits}, per the smallest class size)')

    naive_cv = StratifiedKFold(n_splits=n_splits_eff, shuffle=True, random_state=0)
    naive_proba = cross_val_predict(_pipeline(), X, y, cv=naive_cv,
                                    method='predict_proba')[:, 1]
    naive_auc = roc_auc_score(y, naive_proba)

    n_groups = groups.nunique()
    n_splits_grouped = min(n_splits_eff, n_groups)
    result = {'naive_auc': naive_auc, 'naive_n_splits': n_splits_eff,
             'grouped_auc': None, 'grouped_n_splits': None}

    if n_splits_grouped < 2:
        print(f'  naive AUC: {naive_auc:.3f} ({n_splits_eff}-fold)')
        print(f'  grouped SKIPPED: only {n_groups} distinct architecture(s) present, '
              f'need >=2 to hold any out.')
        return result

    try:
        grouped_cv = StratifiedGroupKFold(n_splits=n_splits_grouped, shuffle=True, random_state=0)
        grouped_proba = cross_val_predict(_pipeline(), X, y, groups=groups, cv=grouped_cv,
                                          method='predict_proba')[:, 1]
        grouped_auc = roc_auc_score(y, grouped_proba)
        result['grouped_auc'] = grouped_auc
        result['grouped_n_splits'] = n_splits_grouped
        gap = naive_auc - grouped_auc
        print(f'  naive AUC:   {naive_auc:.3f} ({n_splits_eff}-fold, sessions shuffled freely)')
        print(f'  grouped AUC: {grouped_auc:.3f} ({n_splits_grouped}-fold, grouped by arch)')
        print(f'  gap: {gap:+.3f} {"(leakage-sized -- naive is optimistic)" if gap > 0.02 else ""}')
    except ValueError as exc:
        print(f'  naive AUC: {naive_auc:.3f} ({n_splits_eff}-fold)')
        print(f'  grouped SKIPPED: {exc}')

    return result


def per_arch_recall(df):
    """Returns (summary_df, predictions_df).

    predictions_df is one row per session -- session_uid, arch, family,
    harness, y_true, y_pred, correct -- kept separate from the aggregated
    summary specifically so research/report_recall.py can bootstrap over
    individual sessions (and over architectures, for family-level CIs)
    instead of only having n_correct/n_sessions to work with.
    """
    print('\n=== leave-one-architecture-out: per-arch recall ===')
    X = df[FEATURE_COLUMNS]
    y = (df['label'] == 'ai').astype(int)
    groups = df['arch']

    if not _class_check(y, 'per_arch_recall'):
        return pd.DataFrame(), pd.DataFrame()

    logo = LeaveOneGroupOut()
    rows = []
    pred_rows = []
    for train_idx, test_idx in logo.split(X, y, groups):
        arch = groups.iloc[test_idx[0]]
        y_train, y_test = y.iloc[train_idx], y.iloc[test_idx]

        if len(set(y_train)) < 2:
            rows.append({'arch': arch, 'n_sessions': len(test_idx), 'n_correct': None,
                        'recall': None, 'skipped_reason': 'training fold has only one class'})
            continue

        pipe = _pipeline()
        pipe.fit(X.iloc[train_idx], y_train)
        pred = pipe.predict(X.iloc[test_idx])
        n_correct = int((pred == y_test.values).sum())
        recall = n_correct / len(test_idx)
        rows.append({'arch': arch, 'n_sessions': len(test_idx), 'n_correct': n_correct,
                    'recall': round(recall, 3), 'skipped_reason': None})

        for i, idx in enumerate(test_idx):
            pred_rows.append({
                'session_uid': df['session_uid'].iloc[idx], 'arch': arch,
                'family': df['family'].iloc[idx], 'harness': df['harness'].iloc[idx],
                'y_true': int(y_test.values[i]), 'y_pred': int(pred[i]),
                'correct': int(pred[i] == y_test.values[i]),
            })

    out = pd.DataFrame(rows).sort_values('n_sessions', ascending=False)
    predictions = pd.DataFrame(pred_rows)

    scored = out[out['recall'].notna()]
    skipped = out[out['recall'].isna()]
    if not scored.empty:
        print(scored[['arch', 'n_sessions', 'n_correct', 'recall']].to_string(index=False))
    if not skipped.empty:
        print('\nskipped:')
        print(skipped[['arch', 'n_sessions', 'skipped_reason']].to_string(index=False))

    return out, predictions


def compare_archs(df, arch_names):
    """Side-by-side recall for a specific pair (or set) of architectures --
    the harness-vs-model isolation view. Fits on everything NOT in
    arch_names, scores each named arch separately so their recall numbers
    are directly comparable (same trained model, different held-out arch)."""
    print(f'\n=== arch comparison: {", ".join(arch_names)} ===')
    present = [a for a in arch_names if a in set(df['arch'])]
    missing = [a for a in arch_names if a not in present]
    if missing:
        print(f'  (not in data yet: {", ".join(missing)})')
    if len(present) < 1:
        print('  SKIPPED: none of the named architectures are in the data.')
        return pd.DataFrame()

    train_df = df[~df['arch'].isin(present)]
    X_train, y_train = train_df[FEATURE_COLUMNS], (train_df['label'] == 'ai').astype(int)
    if not _class_check(y_train, 'compare_archs training set'):
        return pd.DataFrame()

    pipe = _pipeline()
    pipe.fit(X_train, y_train)

    rows = []
    for arch in present:
        sub = df[df['arch'] == arch]
        X_test = sub[FEATURE_COLUMNS]
        y_test = (sub['label'] == 'ai').astype(int)
        pred = pipe.predict(X_test)
        proba = pipe.predict_proba(X_test)[:, 1]
        rows.append({'arch': arch, 'harness': sub['harness'].iloc[0],
                    'n_sessions': len(sub),
                    'recall': round(recall_score(y_test, pred, zero_division=0), 3),
                    'mean_score': round(float(proba.mean()), 3)})
    out = pd.DataFrame(rows)
    print(out.to_string(index=False))
    if len(out) == 2:
        gap = out['recall'].iloc[0] - out['recall'].iloc[1]
        print(f'\nrecall gap ({out["arch"].iloc[0]} - {out["arch"].iloc[1]}): {gap:+.3f}')
        print('A large gap here, for the same underlying model under two harnesses,')
        print('points at the HARNESS as the detection driver, not the model.')
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--features', default='engineered_features.csv',
                    help='Output of research/build_features.py')
    ap.add_argument('--compare-archs', default=None,
                    help='Comma-separated arch names for the harness-vs-model '
                         'isolation view, e.g. fenris,llm_claude')
    ap.add_argument('--per-arch-out', default='per_arch_recall.csv')
    ap.add_argument('--predictions-out', default='loao_predictions.csv',
                    help='Per-session raw predictions, for research/report_recall.py '
                         'to bootstrap over.')
    ap.add_argument('--naive-vs-grouped-out', default='naive_vs_grouped.csv')
    args = ap.parse_args()

    df = load_usable(args.features)
    if df.empty:
        print('Nothing usable -- nothing to evaluate.', file=sys.stderr)
        return 1

    with warnings.catch_warnings():
        warnings.simplefilter('ignore', category=UserWarning)

        ng = naive_vs_grouped(df)
        if ng:
            pd.DataFrame([ng]).to_csv(args.naive_vs_grouped_out, index=False)

        per_arch, predictions = per_arch_recall(df)
        if not per_arch.empty:
            per_arch.to_csv(args.per_arch_out, index=False)
            print(f'\nWrote {args.per_arch_out}')
        if not predictions.empty:
            predictions.to_csv(args.predictions_out, index=False)
            print(f'Wrote {args.predictions_out} ({len(predictions)} session predictions)')

        if args.compare_archs:
            compare_archs(df, [a.strip() for a in args.compare_archs.split(',')])

    return 0


if __name__ == '__main__':
    sys.exit(main())
