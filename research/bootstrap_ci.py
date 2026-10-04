"""Bootstrap confidence intervals for the axis results (M3 gate).

The permutation test answers "is this above chance?" It does not answer "how
precisely do we know 0.895?" -- and at n=86 that interval is wide enough that a
reviewer will ask.

  --mode predict  (default)  Cross-validate ONCE, then resample the held-out
      predictions with replacement. Sampling variability of the metric given
      this detector. Fast. Report this one.

  --mode refit    Resample SESSIONS with replacement and re-cross-validate. It
      LEAKS -- a resample repeats sessions, so the same session lands in train
      AND test -- and reads optimistically high (synthetic check: [0.907,0.971]
      against a 0.878 point estimate). Rough spread only; never report it.

Both resample WITHIN class. Class sizes are a design decision (how many
sessions we chose to collect per arm), not a random draw, so holding them fixed
is correct.
"""
import argparse, sys
import numpy as np, pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import balanced_accuracy_score, recall_score
from sklearn.model_selection import StratifiedKFold, cross_val_predict

DROP = {"struct_n_events", "struct_duration_s", "timing_rate"}
PREFIXES = ("timing_", "action_", "struct_", "geom_")

def features(df):
    return [c for c in df.columns if c.startswith(PREFIXES) and c not in DROP
            and pd.api.types.is_numeric_dtype(df[c])]

def cv_predict(X, y, seed=0, trees=400):
    clf = RandomForestClassifier(n_estimators=trees, random_state=seed, n_jobs=-1)
    cv = StratifiedKFold(5, shuffle=True, random_state=seed)
    return cross_val_predict(clf, X, y, cv=cv, n_jobs=1)

def strat_idx(y, rng):
    out = []
    for lab in np.unique(y):
        pool = np.flatnonzero(y == lab)
        out.append(rng.choice(pool, size=len(pool), replace=True))
    return np.concatenate(out)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv"); ap.add_argument("--label", default="harness")
    ap.add_argument("--classes", default=None)
    ap.add_argument("--mode", choices=("predict","refit"), default="predict")
    ap.add_argument("--boots", type=int, default=2000)
    ap.add_argument("--trees", type=int, default=400)
    ap.add_argument("--alpha", type=float, default=0.05)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()

    df = pd.read_csv(a.csv)
    if a.classes:
        df = df[df[a.label].isin([c.strip() for c in a.classes.split(",")])]
    feats = features(df)
    X = df[feats].fillna(0).values; y = df[a.label].values
    labs = sorted(set(y)); k = len(labs)
    if k < 2 or len(y) < 10:
        sys.exit(f"need >=2 classes and >=10 rows; got k={k}, n={len(y)}")

    print(f"{a.csv}  n={len(y)}  classes={k}  features={len(feats)}  chance={1/k:.3f}")
    for lab in labs:
        print(f"    {lab:<34} {int((y==lab).sum())}")

    pred = cv_predict(X, y, a.seed, a.trees)
    point = balanced_accuracy_score(y, pred)
    print(f"\npoint estimate   {point:.3f}")

    rng = np.random.default_rng(a.seed)
    lo_q, hi_q = 100*a.alpha/2, 100*(1-a.alpha/2)
    boots = np.empty(a.boots)
    if a.mode == "predict":
        print(f"bootstrapping {a.boots} resamples of held-out predictions...")
        for b in range(a.boots):
            i = strat_idx(y, rng)
            boots[b] = balanced_accuracy_score(y[i], pred[i])
    else:
        print(f"bootstrapping {a.boots} refits (slow, reads high)...")
        for b in range(a.boots):
            i = strat_idx(y, rng)
            boots[b] = balanced_accuracy_score(
                y[i], cv_predict(X[i], y[i], seed=b, trees=a.trees))
            if (b+1) % 25 == 0: print(f"  {b+1}/{a.boots}", flush=True)

    lo, hi = np.percentile(boots, [lo_q, hi_q])
    print(f"\n{int(100*(1-a.alpha))}% CI ({a.mode})   [{lo:.3f}, {hi:.3f}]   width {hi-lo:.3f}")
    print(f"  bootstrap mean {boots.mean():.3f}  sd {boots.std():.3f}")
    if lo <= 1/k:
        print(f"  ** interval includes chance ({1/k:.3f}) **")

    print(f"\nper-class recall ({int(100*(1-a.alpha))}% CI):")
    for lab in labs:
        m = y == lab
        rec = recall_score(y, pred, labels=[lab], average="macro", zero_division=0)
        pool = np.flatnonzero(m); rb = np.empty(a.boots)
        for b in range(a.boots):
            i = rng.choice(pool, size=len(pool), replace=True)
            rb[b] = (pred[i] == lab).mean()
        rlo, rhi = np.percentile(rb, [lo_q, hi_q])
        print(f"  {lab:<34} {rec:.3f}   [{rlo:.3f}, {rhi:.3f}]   n={m.sum()}")

if __name__ == "__main__":
    main()
