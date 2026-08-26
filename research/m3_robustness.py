"""Robustness checks for the M3 headline numbers.

Each check attacks one specific way the result could be an artefact rather
than a finding:

  R1  seed/fold luck        -> repeated CV, mean +/- sd, not one lucky split
  R2  classifier choice     -> does it survive a linear model and a kNN?
  R3  class imbalance       -> downsample llm_driven (200) to the others' size
  R4  "it's just volume"    -> drop event-count / duration / rate features
  R5  small-n stability     -> bootstrap CI on the leave-one-model-out recall
  R6  thin-cell dependence  -> drop harnesses with n<15 and re-score

Usage: python research/m3_robustness.py m3_features.csv
"""
import sys
import warnings

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier, HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import balanced_accuracy_score
from sklearn.model_selection import RepeatedStratifiedKFold, StratifiedKFold, cross_val_predict
from sklearn.neighbors import KNeighborsClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore")

TIMING = ("timing_iv_mean timing_iv_cv timing_iv_median timing_iv_p90 timing_iv_min "
          "timing_kd_mean timing_kd_cv timing_rate").split()
ACTION = ("action_click_pct action_keydown_pct action_mousemove_pct action_scroll_pct "
          "action_pageload_pct action_other_pct action_n_types action_entropy").split()
STRUCT = ("struct_n_urls struct_events_per_url struct_revisit_rate struct_duration_s "
          "struct_n_events").split()
GEOM = "geom_vel_mean geom_vel_cv geom_vel_max geom_mousemove_n".split()
ALL = TIMING + ACTION + STRUCT + GEOM

# Anything that is essentially "how much happened" rather than "how it happened".
VOLUME = ["struct_n_events", "struct_duration_s", "timing_rate",
          "struct_events_per_url", "geom_mousemove_n", "struct_n_urls"]
SHAPE = [c for c in ALL if c not in VOLUME]


def models():
    return {
        "RandomForest": RandomForestClassifier(n_estimators=400, random_state=0,
                                               class_weight="balanced", n_jobs=-1),
        "LogisticReg": make_pipeline(StandardScaler(),
                                     LogisticRegression(max_iter=4000, class_weight="balanced")),
        "HistGradBoost": HistGradientBoostingClassifier(random_state=0),
        "kNN(k=5)": make_pipeline(StandardScaler(), KNeighborsClassifier(5)),
    }


def repeated_cv(df, target, cols, est=None, repeats=10, folds=5):
    """Mean +/- sd balanced accuracy over `repeats` independent CV splits."""
    y = df[target].values
    X = df[cols].values
    k = int(min(folds, pd.Series(y).value_counts().min()))
    if k < 2:
        return None
    est = est or models()["RandomForest"]
    rskf = RepeatedStratifiedKFold(n_splits=k, n_repeats=repeats, random_state=0)
    scores = []
    for tr, te in rskf.split(X, y):
        est.fit(X[tr], y[tr])
        scores.append(balanced_accuracy_score(y[te], est.predict(X[te])))
    s = np.array(scores)
    # scores from one repeat share folds, so report the spread across all
    # splits rather than a t-interval that would assume independence
    return s.mean(), s.std(), np.percentile(s, 2.5), np.percentile(s, 97.5), k


def one_cv(df, target, cols, est, folds=5):
    y = df[target].values
    k = int(min(folds, pd.Series(y).value_counts().min()))
    if k < 2:
        return np.nan
    p = cross_val_predict(est, df[cols].values, y,
                          cv=StratifiedKFold(k, shuffle=True, random_state=0))
    return balanced_accuracy_score(y, p)


def hr(t):
    print("\n" + "=" * 74 + f"\n{t}\n" + "=" * 74)


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "m3_features.csv"
    df = pd.read_csv(path)
    df = df[df.instruction_condition.isin(["free_explore", "checklist"])].copy()
    cnt = df.model.value_counts()
    df = df[~df.model.isin(cnt[cnt < 5].index)].copy()
    llm = df[df.harness == "llm_driven"].copy()
    print(f"n={len(df)} sessions | harness chance={1/df.harness.nunique():.3f} | "
          f"model chance={1/llm.model.nunique():.3f}")

    # ---- R1 ----
    hr("R1  Repeated CV (10 x 5-fold) -- is one lucky split doing the work?")
    for name, sub, tgt in (("harness (6-way)", df, "harness"),
                           ("model (7-way, llm_driven)", llm, "model")):
        m, sd, lo, hi, k = repeated_cv(sub, tgt, ALL)
        print(f"  {name:<28} {m:.3f} +/- {sd:.3f}   95% of splits [{lo:.3f}, {hi:.3f}]")

    # ---- R2 ----
    hr("R2  Classifier choice -- does the finding depend on RandomForest?")
    print(f"  {'estimator':<16}{'harness':>12}{'model':>12}")
    for name, est in models().items():
        h = one_cv(df, "harness", ALL, est)
        m = one_cv(llm, "model", ALL, est)
        print(f"  {name:<16}{h:>12.3f}{m:>12.3f}")

    # ---- R3 ----
    hr("R3  Class imbalance -- cap every harness at the same n (llm_driven is 200 vs 8)")
    for cap in (17, 25, 40):
        parts = [g.sample(min(len(g), cap), random_state=0)
                 for _, g in df.groupby("harness")]
        bal = pd.concat(parts)
        m, sd, lo, hi, k = repeated_cv(bal, "harness", ALL, repeats=10)
        sizes = bal.harness.value_counts().to_dict()
        print(f"  cap={cap:<4} n={len(bal):<4} {m:.3f} +/- {sd:.3f}   "
              f"min-class={min(sizes.values())}")

    # ---- R4 ----
    hr("R4  'It's just event volume' -- drop count/duration/rate features")
    print(f"  {'feature set':<34}{'harness':>12}{'model':>12}")
    for label, cols in (("all (25)", ALL),
                        ("SHAPE only (volume dropped, 19)", SHAPE),
                        ("VOLUME only (6)", VOLUME)):
        mh, sdh, *_ = repeated_cv(df, "harness", cols, repeats=6)
        mm, sdm, *_ = repeated_cv(llm, "model", cols, repeats=6)
        print(f"  {label:<34}{mh:>7.3f}+/-{sdh:.2f}{mm:>7.3f}+/-{sdm:.2f}")

    # ---- R5 ----
    hr("R5  Leave-one-model-out recall -- bootstrap CI per held-out model")
    rs = np.random.RandomState(0)
    means = []
    for m_ in sorted(llm.model.unique()):
        tr = df[~((df.harness == "llm_driven") & (df.model == m_))]
        te = df[(df.harness == "llm_driven") & (df.model == m_)]
        if len(te) < 5:
            continue
        clf = models()["RandomForest"].fit(tr[ALL].values, tr.harness)
        hits = (clf.predict(te[ALL].values) == "llm_driven").astype(int)
        boot = [rs.choice(hits, len(hits), replace=True).mean() for _ in range(2000)]
        lo, hi = np.percentile(boot, [2.5, 97.5])
        means.append(hits.mean())
        print(f"  {m_:<40} n={len(te):>3}  recall {hits.mean():.3f}  "
              f"95% CI [{lo:.3f}, {hi:.3f}]")
    print(f"  {'MEAN across held-out models':<40}      {np.mean(means):.3f}")

    # ---- R6 ----
    hr("R6  Thin-cell dependence -- drop harnesses with n<15, re-score")
    big = df.harness.value_counts()
    keep = big[big >= 15].index
    sub = df[df.harness.isin(keep)]
    m, sd, lo, hi, k = repeated_cv(sub, "harness", ALL)
    print(f"  kept {len(keep)} harnesses ({', '.join(sorted(keep))})")
    print(f"  n={len(sub)}  balanced acc {m:.3f} +/- {sd:.3f}  (chance {1/len(keep):.3f})")

    hr("NOT TESTABLE FROM THIS DATA")
    print("  Batch/temporal confound: each harness was collected in contiguous runs on one")
    print("  host, so collection time is partly aliased with harness identity. No split of")
    print("  the existing CSV can separate them -- it needs interleaved re-collection.")


if __name__ == "__main__":
    main()
