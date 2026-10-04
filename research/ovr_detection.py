"""One-vs-rest detection metrics: AUPRC and TPR at a fixed low FPR.

Balanced accuracy and AUROC are threshold-free, but a system whose job is to
block is nothing but a threshold. This reports what that threshold would
actually deliver, per class, next to the prevalence that makes AUPRC readable
(AUPRC 0.30 is excellent at 2% prevalence and poor at 50%).

Sits beside bootstrap_ci.py rather than inside it so there is one canonical
copy of the feature set and the length-free exclusions: it imports `features`
from there instead of redefining them.

    ./venv/bin/python research/ovr_detection.py harness_M2.csv --label harness
    ./venv/bin/python research/ovr_detection.py modelaxis_v5.csv --label model \
        --fpr 0.05,0.10

TPR@FPR is the highest TPR reachable with FPR <= the ceiling, read straight
off the ROC step function (no interpolation), so it corresponds to a threshold
you could really deploy. The CI resamples positives and negatives separately.
When the ceiling is below one false positive for that n (1/n_neg) the number is
not estimable and the script says so instead of printing a confident 1.000.

Predict-mode only: scores are out-of-fold, from a single cross-validation. A
refit bootstrap would leak (a resample repeats sessions across train and test).
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import average_precision_score, roc_curve
from sklearn.model_selection import StratifiedKFold, cross_val_predict

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bootstrap_ci import features  # noqa: E402  (shared feature set)


def cv_proba(X, y, seed=0, trees=400):
    """Out-of-fold class probabilities; columns follow sorted(set(y))."""
    clf = RandomForestClassifier(n_estimators=trees, random_state=seed, n_jobs=-1)
    cv = StratifiedKFold(5, shuffle=True, random_state=seed)
    return cross_val_predict(clf, X, y, cv=cv, n_jobs=1, method="predict_proba")


def tpr_at_fpr(is_pos, score, fpr_max):
    fpr, tpr, _ = roc_curve(is_pos, score)
    ok = fpr <= fpr_max
    return float(tpr[ok].max()) if ok.any() else 0.0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("--label", default="harness")
    ap.add_argument("--classes", default=None)
    ap.add_argument("--fpr", default="0.01,0.05",
                    help="comma list of FPR ceilings; the first gets a CI")
    ap.add_argument("--boots", type=int, default=1000)
    ap.add_argument("--trees", type=int, default=400)
    ap.add_argument("--alpha", type=float, default=0.05)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()

    df = pd.read_csv(a.csv)
    if a.classes:
        df = df[df[a.label].isin([c.strip() for c in a.classes.split(",")])]
    feats = features(df)
    X = df[feats].fillna(0).values
    y = df[a.label].values
    labs = sorted(set(y))
    if len(labs) < 2 or len(y) < 10:
        sys.exit(f"need >=2 classes and >=10 rows; got k={len(labs)}, n={len(y)}")

    fprs = [float(x) for x in a.fpr.split(",")]
    proba = cv_proba(X, y, a.seed, a.trees)
    rng = np.random.default_rng(a.seed)
    lo_q, hi_q = 100 * a.alpha / 2, 100 * (1 - a.alpha / 2)
    pct = int(100 * (1 - a.alpha))

    print(f"{a.csv}  n={len(y)}  classes={len(labs)}  features={len(feats)}")
    hdr = f"  {'class':<26}{'prev':>6}{'AUPRC':>8}" + "".join(
        f"{'TPR@' + format(f, 'g'):>12}" for f in fprs)
    print("\none-vs-rest, out-of-fold:")
    print(hdr)

    for j, lab in enumerate(labs):
        pos = (y == lab)
        sc = proba[:, j]
        row = (f"  {lab:<26}{pos.mean():>6.3f}"
               f"{average_precision_score(pos, sc):>8.3f}")
        for f in fprs:
            row += f"{tpr_at_fpr(pos, sc, f):>12.3f}"
        print(row)

        f0 = fprs[0]
        pi, ni = np.flatnonzero(pos), np.flatnonzero(~pos)
        bs = np.empty(a.boots)
        for b in range(a.boots):
            idx = np.concatenate([rng.choice(pi, len(pi)), rng.choice(ni, len(ni))])
            bs[b] = tpr_at_fpr(pos[idx], sc[idx], f0)
        lo, hi = np.percentile(bs, [lo_q, hi_q])
        res = 1.0 / len(ni)
        print(f"  {'':<26}  TPR@{f0:g} {pct}% CI [{lo:.3f}, {hi:.3f}]   "
              f"(n_pos={len(pi)}, n_neg={len(ni)}; FPR resolution {res:.3f})")
        if f0 < res:
            print(f"  {'':<26}  ** FPR {f0:g} is below one false positive "
                  f"({res:.3f}): TPR there is not estimable at this n **")


if __name__ == "__main__":
    main()
