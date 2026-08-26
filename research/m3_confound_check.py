"""Did interleaving actually kill the batch/temporal confound?

Two questions, asked directly of the data:

  C1  Can collection TIME ALONE predict the label?
      Feed the classifier nothing but each session's timestamp. On blocked
      data (15 of A, then 15 of B) this scores near-perfectly, because the
      clock IS the label. On interleaved data it should collapse to chance.
      This is the confound, measured.

  C2  Does the real result survive on interleaved data?
      Score the behavioural features on the interleaved batch alone and
      compare with the blocked batch. If accuracy holds while C1 collapses,
      the fingerprint was never the clock.

    python research/m3_confound_check.py blocked.csv interleaved.csv
    python research/m3_confound_check.py m3_features.csv        # C1 only
"""
import sys
import warnings

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import balanced_accuracy_score
from sklearn.model_selection import RepeatedStratifiedKFold

warnings.filterwarnings("ignore")
RNG = 0


TREES = 200
REPEATS = 5


def rf():
    # 200 trees is plenty here: the time-only model has 3 columns, and the
    # behavioural one is already saturated well below this. Keeps the run
    # tractable on a 2-core VPS.
    return RandomForestClassifier(n_estimators=TREES, random_state=RNG,
                                  class_weight="balanced", n_jobs=-1)


def load(path):
    df = pd.read_csv(path)
    if df.empty:
        sys.exit(f"{path} is empty -- re-export it. If you used --run-id, that "
                 "run matched no sessions.")
    if "first_seen" not in df.columns:
        sys.exit(f"{path} has no first_seen column -- re-export with the "
                 "updated research/export_research_features.py")
    df["first_seen"] = pd.to_datetime(df.first_seen, errors="coerce", utc=True)
    df = df.dropna(subset=["first_seen"]).copy()
    t0 = df.first_seen.min()
    # seconds since the batch started, plus a couple of clock-shaped views a
    # confounded classifier could exploit
    df["t_seconds"] = (df.first_seen - t0).dt.total_seconds()
    df["t_hour"] = df.first_seen.dt.hour + df.first_seen.dt.minute / 60.0
    df["t_order"] = df.t_seconds.rank(method="first")
    return df


def cv(df, cols, target, reps=REPEATS):
    sub = df[df[target].notna()]
    y = sub[target].values
    counts = pd.Series(y).value_counts()
    if len(counts) < 2:
        return None
    k = int(min(5, counts.min()))
    if k < 2:
        return None
    X = sub[cols].values
    s = []
    for tr, te in RepeatedStratifiedKFold(n_splits=k, n_repeats=reps,
                                          random_state=RNG).split(X, y):
        e = rf().fit(X[tr], y[tr])
        s.append(balanced_accuracy_score(y[te], e.predict(X[te])))
    return float(np.mean(s)), float(np.std(s)), 1.0 / len(counts), len(sub)


TIME = ["t_seconds", "t_hour", "t_order"]


def behav(df):
    return [c for c in df.columns
            if c.split("_")[0] in ("timing", "action", "struct", "geom")]


def verdict(time_res, feat_res):
    if not time_res or not feat_res:
        return "  (not enough data for a verdict)"
    t_lift = time_res[0] - time_res[2]
    f_lift = feat_res[0] - feat_res[2]
    if t_lift < 0.15:
        return ("  VERDICT: time carries almost no label information -- the "
                "confound is controlled.")
    if t_lift > 0.5 * f_lift:
        return ("  VERDICT: time alone explains much of the label. This batch "
                "is confounded; interleave it.")
    return ("  VERDICT: some residual time signal, well below the behavioural "
            "signal. Report it as a limitation.")


def analyse(path, tag):
    df = load(path)
    span = (df.first_seen.max() - df.first_seen.min())
    print(f"\n{'=' * 74}\n{tag}: {path}\n{'=' * 74}")
    print(f"  n={len(df)} sessions | collected over {span} | "
          f"{df.harness.nunique()} harnesses")

    out = {}
    for target in ("harness", "model"):
        scope = df if target == "harness" else df[df.harness == "llm_driven"]
        if scope[target].nunique() < 2:
            continue
        t = cv(scope, TIME, target)
        f = cv(scope, behav(df), target)
        if not t or not f:
            continue
        print(f"\n  --- {target} ---")
        print(f"    C1  TIME ONLY (clock features)   {t[0]:.3f} +/- {t[1]:.3f}"
              f"   chance {t[2]:.3f}   n={t[3]}")
        print(f"    C2  behavioural features         {f[0]:.3f} +/- {f[1]:.3f}"
              f"   chance {f[2]:.3f}")
        print(verdict(t, f))
        out[target] = (t, f)
    return out


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    blocked = analyse(sys.argv[1], "BLOCKED (existing collection)")
    if len(sys.argv) < 3:
        print("\nPass an interleaved export as a second argument to compare.")
        return
    inter = analyse(sys.argv[2], "INTERLEAVED (new collection)")

    print(f"\n{'=' * 74}\nSIDE BY SIDE\n{'=' * 74}")
    print(f"  {'target':<10}{'time-only blocked':>20}{'time-only inter':>18}"
          f"{'features inter':>17}")
    for target in ("harness", "model"):
        if target in blocked and target in inter:
            b, i = blocked[target], inter[target]
            print(f"  {target:<10}{b[0][0]:>20.3f}{i[0][0]:>18.3f}{i[1][0]:>17.3f}")
    print("\n  The result you want: time-only DROPS toward chance after "
          "interleaving,\n  while the behavioural score stays high. That is the "
          "confound being removed\n  without the finding moving.")


if __name__ == "__main__":
    main()
