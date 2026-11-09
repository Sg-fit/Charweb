#!/usr/bin/env python3
"""
"What about a harness you never trained on?"

Two experiments, both on data already collected.

  A. CROSS-HARNESS TRANSFER of model attribution.
     Train model-classification on one harness, test on the other.
     H1 (interaction 0.7%, CI [0.0, 4.3]) PREDICTS this transfers. This is
     that prediction tested, which is stronger evidence than the
     decomposition alone.

  B. LEAVE-ONE-HARNESS-OUT NOVELTY DETECTION (open set).
     Train on k-1 harness classes. Score novelty as 1 - max predicted
     probability. Measure how well it separates held-out-class sessions
     from in-distribution ones. This is the literal answer to the question:
     can the detector tell that a stack is one it has never seen?

Closed-set classification INTO an unseen harness is impossible by
construction and is not attempted.

Usage:
  ./venv/bin/python research/unseen_harness.py
"""
import itertools
import sys

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.metrics import balanced_accuracy_score, recall_score, roc_auc_score, roc_curve

EXCLUDE = {"struct_n_events", "struct_duration_s", "timing_rate"}
PREFIX = ("timing_", "action_", "struct_", "geom_")
FILES = ["harness_M2.csv", "modelaxis_v5.csv", "percept_M2.csv", "crossed_cell.csv"]
SEED = 0


def feats(df):
    return [c for c in df.columns
            if c.startswith(PREFIX) and c not in EXCLUDE
            and pd.api.types.is_numeric_dtype(df[c])]


def load_all():
    """Reassemble the same pool h1_variance.py builds from."""
    parts = []
    for f in FILES:
        try:
            parts.append(pd.read_csv(f))
        except FileNotFoundError:
            print(f"  [warn] {f} not found, skipping")
    if not parts:
        sys.exit("no input files found -- run from /srv/charweb")
    df = pd.concat(parts, ignore_index=True)
    before = len(df)
    df = df.drop_duplicates("session_uid", keep="first").reset_index(drop=True)
    print(f"pooled {before} rows -> {len(df)} unique sessions")
    return df


def rf():
    return RandomForestClassifier(n_estimators=500, random_state=SEED, n_jobs=-1)


def boot_ba(y, yhat, B=2000):
    rng = np.random.default_rng(SEED)
    out = []
    n = len(y)
    for _ in range(B):
        i = rng.integers(0, n, n)
        if len(set(y[i])) < 2:
            continue
        out.append(balanced_accuracy_score(y[i], yhat[i]))
    return (float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5))) if out else (np.nan, np.nan)


# ===================================================================== A
def experiment_a(df):
    print("\n" + "=" * 74)
    print("A. CROSS-HARNESS TRANSFER OF MODEL ATTRIBUTION")
    print("=" * 74)

    HARN = ["llm_driven", "percept_dom"]
    d = df[df.harness.isin(HARN) & df.model.notna()].copy()
    models = sorted(d.model.unique())
    d = d[d.model.isin(models)]
    print("\ncells:")
    print(d.groupby(["harness", "model"]).size().to_string())
    if len(models) != 2:
        print(f"\n  need exactly 2 models, found {models} -- skipping A")
        return

    F = feats(d)
    chance = 1.0 / len(models)
    print(f"\n{len(F)} features, chance = {chance:.3f}")

    # in-distribution baseline: pooled CV ignoring harness
    X = np.nan_to_num(d[F].to_numpy(float))
    y = d.model.astype(str).to_numpy()
    k = int(min(5, pd.Series(y).value_counts().min()))
    cv = StratifiedKFold(k, shuffle=True, random_state=SEED)
    yhat = cross_val_predict(rf(), X, y, cv=cv)
    ba = balanced_accuracy_score(y, yhat)
    lo, hi = boot_ba(y, yhat)
    print(f"\n  in-distribution (pooled {k}-fold CV, harness ignored)")
    print(f"    balanced accuracy = {ba:.3f}  [{lo:.3f}, {hi:.3f}]   n={len(y)}")

    rows = []
    for test_h in HARN:
        tr = d[d.harness != test_h]
        te = d[d.harness == test_h]
        if te.model.nunique() < 2 or tr.model.nunique() < 2:
            print(f"\n  train->{test_h}: skipped (a side lacks both models)")
            continue
        m = rf().fit(np.nan_to_num(tr[F].to_numpy(float)), tr.model.astype(str))
        yt = te.model.astype(str).to_numpy()
        yp = m.predict(np.nan_to_num(te[F].to_numpy(float)))
        b = balanced_accuracy_score(yt, yp)
        lo2, hi2 = boot_ba(yt, yp)
        rec = recall_score(yt, yp, labels=models, average=None, zero_division=0)
        print(f"\n  train on {tr.harness.unique().tolist()} -> test on {test_h}")
        print(f"    balanced accuracy = {b:.3f}  [{lo2:.3f}, {hi2:.3f}]   n={len(yt)}")
        for mm, r in zip(models, rec):
            print(f"      recall {mm:<40} {r:.3f}")
        rows.append((test_h, b, len(yt)))

    if rows:
        mean_t = float(np.mean([r[1] for r in rows]))
        print(f"\n  mean cross-harness transfer = {mean_t:.3f}")
        print(f"  in-distribution              = {ba:.3f}")
        print(f"  COST OF AN UNSEEN HARNESS    = {ba - mean_t:+.3f}")
        print("\n  Reading: H1 put the harness x model interaction at 0.007")
        print("  (95% CI [0.000, 0.043]) and so predicts a SMALL cost here.")
        print("  A small cost confirms H1 by prediction, not by decomposition.")
        print("  A large cost means the model signature is harness-specific and")
        print("  the additivity claim needs qualifying -- report either way.")


# ===================================================================== B
def experiment_b(df):
    print("\n" + "=" * 74)
    print("B. LEAVE-ONE-HARNESS-OUT NOVELTY DETECTION (open set)")
    print("=" * 74)

    try:
        d = pd.read_csv("harness_M2.csv")
    except FileNotFoundError:
        print("  harness_M2.csv not found -- skipping B")
        return
    F = feats(d)
    classes = sorted(d.harness.unique())
    print(f"\n{len(d)} sessions, {len(classes)} classes, {len(F)} features")
    print(d.harness.value_counts().to_string())

    aucs = []
    for held in classes:
        tr = d[d.harness != held]
        te = d[d.harness == held]
        ytr = tr.harness.astype(str).to_numpy()
        Xtr = np.nan_to_num(tr[F].to_numpy(float))

        k = int(min(5, pd.Series(ytr).value_counts().min()))
        cv = StratifiedKFold(k, shuffle=True, random_state=SEED)
        # out-of-fold confidence on KNOWN classes -- no leakage
        P_known = cross_val_predict(rf(), Xtr, ytr, cv=cv, method="predict_proba")
        nov_known = 1.0 - P_known.max(axis=1)

        m = rf().fit(Xtr, ytr)
        nov_novel = 1.0 - m.predict_proba(np.nan_to_num(te[F].to_numpy(float))).max(axis=1)

        yy = np.r_[np.zeros(len(nov_known)), np.ones(len(nov_novel))]
        ss = np.r_[nov_known, nov_novel]
        auc = roc_auc_score(yy, ss)
        fpr, tpr, _ = roc_curve(yy, ss)
        ok = fpr <= 0.05 + 1e-12
        t5 = float(tpr[ok].max()) if ok.any() else float("nan")
        aucs.append(auc)
        print(f"\n  held out {held:<22} n_novel={len(te):<4} n_known={len(tr)}")
        print(f"    novelty AUC            = {auc:.3f}")
        print(f"    TPR @ FPR<=5%          = {t5:.3f}")
        print(f"    median novelty  known={np.median(nov_known):.3f}  "
              f"novel={np.median(nov_novel):.3f}")

    print(f"\n  mean novelty AUC across held-out harnesses = {np.mean(aucs):.3f}")
    print("\n  CAVEAT, state it: 3 of the 4 classes are scripted profiles. Holding")
    print("  out a scripted class still leaves 2 scripted classes in training, so")
    print("  that 'novel' harness looks familiar and the AUC is optimistic.")
    print("  The llm_driven row is the honest one -- it is the only hold-out that")
    print("  removes a genuinely distinct kind of harness. Report PER-CLASS.")


if __name__ == "__main__":
    pool = load_all()
    experiment_a(pool)
    experiment_b(pool)
    print()
