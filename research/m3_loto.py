"""Leave-one-task-out: does the fingerprint generalise to an UNSEEN task?

With two instruction conditions you can only train on one and test on the
other, which conflates "a different task" with "the only other task". With
three or more you can hold one out and train on the rest -- the same logic as
leave-one-model-out, applied to the task axis. That converts the study's
weakest claim ("the fingerprint is partly task-dependent") into a testable
one ("it survives a task it has never seen").

Reported per held-out task and averaged, against three references:

    in-distribution   pooled CV over the same classes (the ceiling)
    LOTO              train on all other tasks, test on the held-out one
    single-task       train on ONE other task only (the old, weaker setup)

If LOTO lands near in-distribution and clearly above single-task, then task
DIVERSITY -- not task identity -- was the missing ingredient, which is the
claim prior work (arXiv 2605.14786) makes when it reports that pooling traces
from multiple tasks recovers strong attribution.

    python research/m3_loto.py m3_features.csv
"""
import sys
import warnings
from itertools import combinations

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import balanced_accuracy_score, f1_score
from sklearn.model_selection import RepeatedStratifiedKFold

warnings.filterwarnings("ignore")
RNG = 0
MIN_PER_CLASS = 3          # a class needs this many sessions in a task to count
MIN_TEST = 6               # a held-out task needs this many sessions to score


def rf():
    return RandomForestClassifier(n_estimators=500, random_state=RNG,
                                  class_weight="balanced", n_jobs=-1)


def feats(df):
    return [c for c in df.columns
            if c.split("_")[0] in ("timing", "action", "struct", "geom")]


def shared_classes(df, target, tasks):
    """Classes present with enough sessions in EVERY task. Without this, a
    'drop' can just mean the held-out task contains a class the training tasks
    never had -- unlearnable by construction, and nothing to do with transfer."""
    ok = None
    for t in tasks:
        counts = df[df.instruction_condition == t][target].value_counts()
        present = set(counts[counts >= MIN_PER_CLASS].index)
        ok = present if ok is None else (ok & present)
    return sorted(ok or [])


def pooled_cv(sub, target, cols, reps=10):
    X, y = sub[cols].values, sub[target].values
    counts = pd.Series(y).value_counts()
    if len(counts) < 2:
        return None
    k = int(min(5, counts.min()))
    if k < 2:
        return None
    s = []
    for tr, te in RepeatedStratifiedKFold(n_splits=k, n_repeats=reps,
                                          random_state=RNG).split(X, y):
        e = rf().fit(X[tr], y[tr])
        s.append(balanced_accuracy_score(y[te], e.predict(X[te])))
    return float(np.mean(s)), float(np.std(s))


def train_test(tr, te, target, cols):
    if len(te) < MIN_TEST or tr[target].nunique() < 2:
        return None
    m = rf().fit(tr[cols].values, tr[target])
    p = m.predict(te[cols].values)
    return (balanced_accuracy_score(te[target], p),
            f1_score(te[target], p, average="macro"))


def run_axis(df, target, cols, label):
    tasks = sorted(df.instruction_condition.unique())
    print("\n" + "=" * 76)
    print(f"{label}   ({len(tasks)} tasks: {', '.join(tasks)})")
    print("=" * 76)
    if len(tasks) < 3:
        print(f"  Only {len(tasks)} task(s) present -- leave-one-task-out needs 3+.")
        print("  With 2 you can only do the single-task transfer in "
              "research/m3_task_transfer.py.")
        return

    classes = shared_classes(df, target, tasks)
    if len(classes) < 2:
        print(f"  Fewer than 2 {target} classes appear in every task "
              f"(with >={MIN_PER_CLASS} sessions each) -- cannot compare fairly.")
        return
    sub = df[df[target].isin(classes)].copy()
    chance = 1.0 / len(classes)
    print(f"  comparing {len(classes)} classes present in all tasks: "
          f"{', '.join(classes)}")
    print(f"  n={len(sub)}   chance={chance:.3f}")

    ref = pooled_cv(sub, target, cols)
    if ref:
        print(f"\n  in-distribution (pooled CV)      {ref[0]:.3f} +/- {ref[1]:.3f}")

    print(f"\n  {'held-out task':<20}{'n':>5}{'LOTO':>10}{'single-task':>14}")
    loto, single = [], []
    for t in tasks:
        te = sub[sub.instruction_condition == t]
        tr = sub[sub.instruction_condition != t]
        r = train_test(tr, te, target, cols)
        if r is None:
            print(f"  {t:<20}{len(te):>5}       n/a          n/a")
            continue
        loto.append(r[0])

        # Same held-out task, but trained on only ONE other task at a time --
        # the old two-condition setup. Averaged so it isn't luck of the pairing.
        singles = []
        for o in tasks:
            if o == t:
                continue
            r1 = train_test(sub[sub.instruction_condition == o], te, target, cols)
            if r1:
                singles.append(r1[0])
        s_mean = float(np.mean(singles)) if singles else float("nan")
        if singles:
            single.append(s_mean)
        print(f"  {t:<20}{len(te):>5}{r[0]:>10.3f}{s_mean:>14.3f}")

    if loto:
        print(f"\n  MEAN over held-out tasks         LOTO {np.mean(loto):.3f}"
              + (f"   single-task {np.mean(single):.3f}" if single else ""))
        if ref:
            print(f"  cost of an unseen task vs in-distribution: "
                  f"{ref[0] - np.mean(loto):+.3f}")
        if single:
            print(f"  gain from task diversity (LOTO - single-task): "
                  f"{np.mean(loto) - np.mean(single):+.3f}")


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "m3_features.csv"
    df = pd.read_csv(path)
    if df.empty:
        sys.exit(f"{path} is empty.")
    cols = feats(df)

    # Drop conditions and models too thin to train or test on.
    tc = df.instruction_condition.value_counts()
    df = df[df.instruction_condition.isin(tc[tc >= MIN_TEST].index)].copy()
    mc = df.model.value_counts()
    df = df[df.model.isin(mc[mc >= 5].index) | (df.model == "none_scripted")].copy()

    print(f"n={len(df)} sessions | {len(cols)} features")
    print("sessions per task:")
    for t, n in df.instruction_condition.value_counts().items():
        print(f"    {t:<20}{n:>5}")

    run_axis(df, "harness", cols, "LEAVE-ONE-TASK-OUT  --  harness axis")
    llm = df[df.harness == "llm_driven"]
    if len(llm) > MIN_TEST:
        run_axis(llm, "model", cols, "LEAVE-ONE-TASK-OUT  --  model axis "
                                     "(within llm_driven)")


if __name__ == "__main__":
    main()
