"""Balanced-accuracy + permutation test for any label column.

m3_analysis.py assumes the full study shape (harness axis, then models nested
inside llm_driven) and dies on a perception-only export, which has no
llm_driven rows. This does the one thing needed for a 2- or 3-class contrast
on an arbitrary label, with the same conventions used everywhere else in the
project: balanced accuracy, chance = 1/k, permutation p with the +1
correction, and the length-free feature set.
"""
import argparse, sys
import numpy as np, pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.metrics import balanced_accuracy_score, confusion_matrix

# Excluded everywhere in this project: session length is shaped by rate
# limiting and step budgets, not only by behaviour.
DROP = {"struct_n_events", "struct_duration_s", "timing_rate"}
PREFIXES = ("timing_", "action_", "struct_", "geom_")

ap = argparse.ArgumentParser()
ap.add_argument("csv")
ap.add_argument("--label", default="harness")
ap.add_argument("--classes", default=None, help="comma-separated subset")
ap.add_argument("--perms", type=int, default=200)
ap.add_argument("--seed", type=int, default=0)
a = ap.parse_args()

df = pd.read_csv(a.csv)
if a.classes:
    keep = [c.strip() for c in a.classes.split(",")]
    df = df[df[a.label].isin(keep)]
feats = [c for c in df.columns
         if c.startswith(PREFIXES) and c not in DROP
         and pd.api.types.is_numeric_dtype(df[c])]
X = df[feats].fillna(0).values
y = df[a.label].values
k = len(set(y))
if k < 2 or len(y) < 10:
    sys.exit(f"need >=2 classes and >=10 rows; got k={k}, n={len(y)}")

print(f"n={len(y)}  classes={k}  features={len(feats)}  chance={1/k:.3f}")
for c, n in pd.Series(y).value_counts().items():
    print(f"    {c:<28} {n}")

def score(labels, seed=0):
    clf = RandomForestClassifier(n_estimators=400, random_state=seed, n_jobs=-1)
    cv = StratifiedKFold(5, shuffle=True, random_state=seed)
    pred = cross_val_predict(clf, X, labels, cv=cv, n_jobs=1)
    return balanced_accuracy_score(labels, pred), pred

obs, pred = score(y)
print(f"\nobserved balanced accuracy   {obs:.3f}")

rng = np.random.default_rng(a.seed)
null = np.array([score(rng.permutation(y), s)[0] for s in range(a.perms)])
p = (1 + (null >= obs).sum()) / (a.perms + 1)
print(f"null (shuffled)              {null.mean():.3f} +/- {null.std():.3f}"
      f"   95th pct {np.percentile(null, 95):.3f}")
print(f"p = {p:.4f}   lift over chance +{obs - 1/k:.3f}")

labs = sorted(set(y))
print("\nconfusion (rows = truth):")
cm = confusion_matrix(y, pred, labels=labs)
for lab, row in zip(labs, cm):
    rec = row[labs.index(lab)] / row.sum() if row.sum() else float("nan")
    print(f"  {lab:<24} recall {rec:.3f}   {row}")

print("\nfeature groups:")
for g in ("timing_", "action_", "struct_", "geom_"):
    sub = [f for f in feats if f.startswith(g)]
    if not sub:
        continue
    Xf, Xall = X, X
    X = df[sub].fillna(0).values
    s, _ = score(y)
    X = Xall
    print(f"  {g[:-1]:<8} only        {s:.3f}   ({len(sub)} features)")
notim = [f for f in feats if not f.startswith("timing_")]
Xall = X; X = df[notim].fillna(0).values
s, _ = score(y); X = Xall
print(f"  ALL minus timing      {s:.3f}")
print(f"  ALL                   {obs:.3f}")
