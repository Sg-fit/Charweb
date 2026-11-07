#!/usr/bin/env python3
"""
TPR at fixed low FPR for each attribution axis.

Balanced accuracy answers "how often is it right?". A deployed detector runs on
a false-positive budget instead, so this reports ROC AUC with a bootstrap CI and
TPR at a set of FPR targets -- and, crucially, states the smallest FPR the
sample size can actually resolve, instead of quietly reporting 0.000 for an
FPR target below the granularity of the data.

Usage
-----
  # model-based, one-vs-rest per class of the label column
  python tpr_at_fpr.py harness_M2.csv harness
  python tpr_at_fpr.py modelaxis_v5.csv model
  python tpr_at_fpr.py percept_M2.csv perception_mode

  # single-feature defense ROC (e.g. order-violation rate as a detector)
  python tpr_at_fpr.py e3_all.csv adversarial_condition --score-col order_violations

Options
-------
  --score-col COL   skip the model; use COL directly as the detection score.
                    The label is then binarised as (label != --negative).
  --negative VAL    which label value counts as negative. Default: auto --
                    "clean" if present, else the majority class.
  --boot N          bootstrap replicates for the AUC CI (default 2000).
  --out PREFIX      write PREFIX_roc.csv and PREFIX_tpr.csv for figures.
"""
import argparse
import sys

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.metrics import roc_auc_score, roc_curve

# Length proxies are excluded by construction everywhere else in this project;
# keep that identical here or the numbers are not comparable to M3.
EXCLUDE = {"struct_n_events", "struct_duration_s", "timing_rate"}
PREFIX = ("timing_", "action_", "struct_", "geom_")
FPR_TARGETS = [0.001, 0.01, 0.05, 0.10]


def feature_cols(df):
    return [c for c in df.columns
            if c.startswith(PREFIX)
            and c not in EXCLUDE
            and pd.api.types.is_numeric_dtype(df[c])]


def tpr_at(fpr, tpr, target):
    """Conservative TPR at FPR <= target. None if target is unreachable."""
    ok = fpr <= target + 1e-12
    if not ok.any():
        return None
    return float(tpr[ok].max())


def boot_auc(y, s, B, rng):
    out = []
    n = len(y)
    for _ in range(B):
        idx = rng.integers(0, n, n)
        yb = y[idx]
        if yb.min() == yb.max():
            continue
        out.append(roc_auc_score(yb, s[idx]))
    if not out:
        return (float("nan"), float("nan"))
    return (float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5)))


def report(name, y_bin, score, B, rng, rows, roc_rows):
    n_pos = int(y_bin.sum())
    n_neg = int((~y_bin).sum())
    if n_pos == 0 or n_neg == 0:
        print(f"  {name:<28} skipped (n_pos={n_pos}, n_neg={n_neg})")
        return
    min_fpr = 1.0 / n_neg
    auc = roc_auc_score(y_bin, score)
    lo, hi = boot_auc(y_bin, score, B, rng)
    fpr, tpr, thr = roc_curve(y_bin, score)

    print(f"  {name:<28} n_pos={n_pos:<4} n_neg={n_neg:<4} "
          f"AUC={auc:.3f} [{lo:.3f}, {hi:.3f}]")
    print(f"    smallest resolvable FPR = 1/{n_neg} = {min_fpr:.4f}")
    for t in FPR_TARGETS:
        v = tpr_at(fpr, tpr, t)
        if t < min_fpr:
            note = f"  <-- BELOW RESOLUTION (needs n_neg >= {int(np.ceil(1/t))})"
        else:
            note = ""
        shown = "n/a" if v is None else f"{v:.3f}"
        print(f"    TPR @ FPR<={t:<6.3f} = {shown}{note}")
        rows.append(dict(task=name, n_pos=n_pos, n_neg=n_neg, auc=auc,
                         auc_lo=lo, auc_hi=hi, fpr_target=t,
                         tpr=v, resolvable=bool(t >= min_fpr),
                         min_fpr=min_fpr))
    # operating point actually achievable at the data's own granularity
    v = tpr_at(fpr, tpr, min_fpr)
    print(f"    TPR @ FPR<={min_fpr:.4f} (achievable) = "
          f"{'n/a' if v is None else f'{v:.3f}'}")
    rows.append(dict(task=name, n_pos=n_pos, n_neg=n_neg, auc=auc,
                     auc_lo=lo, auc_hi=hi, fpr_target=min_fpr,
                     tpr=v, resolvable=True, min_fpr=min_fpr))
    for f, t in zip(fpr, tpr):
        roc_rows.append(dict(task=name, fpr=float(f), tpr=float(t)))
    print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("label")
    ap.add_argument("--score-col", default=None)
    ap.add_argument("--negative", default=None)
    ap.add_argument("--boot", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    df = pd.read_csv(a.csv)
    if a.label not in df.columns:
        sys.exit(f"label column '{a.label}' not in {a.csv}.\n"
                 f"columns: {list(df.columns)}")
    df = df[df[a.label].notna()].reset_index(drop=True)
    y = df[a.label].astype(str).to_numpy()
    rng = np.random.default_rng(a.seed)
    rows, roc_rows = [], []

    print(f"\n{a.csv}  label={a.label}  n={len(df)}")
    print(f"class counts: {dict(pd.Series(y).value_counts())}\n")

    if a.score_col:
        if a.score_col not in df.columns:
            sys.exit(f"--score-col '{a.score_col}' not in {a.csv}.\n"
                     f"columns: {list(df.columns)}")
        neg = a.negative
        if neg is None:
            neg = "clean" if "clean" in set(y) else pd.Series(y).mode()[0]
        print(f"single-feature ROC on '{a.score_col}', negative class = '{neg}'\n")
        score = pd.to_numeric(df[a.score_col], errors="coerce").fillna(0).to_numpy(float)
        report(f"{a.score_col} vs {neg}", y != neg, score, a.boot, rng, rows, roc_rows)
    else:
        F = feature_cols(df)
        if not F:
            sys.exit(f"no feature columns found in {a.csv}.\n"
                     f"columns: {list(df.columns)}")
        print(f"{len(F)} features (length proxies excluded)\n")
        X = np.nan_to_num(df[F].to_numpy(float))
        counts = pd.Series(y).value_counts()
        k = int(min(5, counts.min()))
        if k < 2:
            sys.exit(f"a class has <2 members; cannot cross-validate: {dict(counts)}")
        cv = StratifiedKFold(k, shuffle=True, random_state=a.seed)
        clf = RandomForestClassifier(n_estimators=500, random_state=a.seed, n_jobs=-1)
        # predict_proba out of fold -> no leakage, same discipline as --mode predict
        P = cross_val_predict(clf, X, y, cv=cv, method="predict_proba")
        classes = list(np.unique(y))
        print(f"{k}-fold out-of-fold probabilities; one-vs-rest per class\n")
        for i, c in enumerate(classes):
            report(f"{c} (one-vs-rest)", y == c, P[:, i], a.boot, rng, rows, roc_rows)
        if len(classes) == 2:
            print("  (2-class: the two rows above are mirror images; report either)\n")

    if a.out:
        pd.DataFrame(rows).to_csv(f"{a.out}_tpr.csv", index=False)
        pd.DataFrame(roc_rows).to_csv(f"{a.out}_roc.csv", index=False)
        print(f"wrote {a.out}_tpr.csv and {a.out}_roc.csv")


if __name__ == "__main__":
    main()
