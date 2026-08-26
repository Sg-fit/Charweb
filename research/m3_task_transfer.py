"""Can the task-transfer drop be fixed? Four candidate mitigations, tested.

The problem: train on free_explore, test on checklist, and harness accuracy
falls 0.930 -> 0.667. Some of what the classifier learned is the task, not
the agent.

The hypothesis being tested here is that the task mostly shifts the SCALE of
features (a checklist run simply does more of certain things) while leaving
the RELATIVE profile of an agent intact. If so, removing the per-task scale
should recover accuracy.

  M0  baseline            raw features, train free -> test checklist
  M1  per-task centering   z-score each feature WITHIN each task condition,
                           so only an agent's deviation from that task's own
                           average is used. Needs a batch of sessions from the
                           same task at test time -- realistic for a defender
                           watching a campaign, and stated as an assumption.
  M2  per-task ranks       replace each value by its rank within its task.
                           Same idea as M1 but immune to outliers/shape.
  M3  drop task-sensitive  rank features by how strongly the task alone moves
                           them, drop the worst k, keep the rest raw.
  M4  train on both        reference upper bound (not a fix -- it has seen the
                           target task) to show what's recoverable at all.

Usage: python research/m3_task_transfer.py m3_features.csv
"""
import sys
import warnings

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import balanced_accuracy_score, f1_score

warnings.filterwarnings("ignore")
RNG = 0

FEATS = None  # filled in main()


def rf():
    return RandomForestClassifier(n_estimators=500, random_state=RNG,
                                  class_weight="balanced", n_jobs=-1)


def per_task_z(df, cols):
    """Standardize each feature within its own instruction condition."""
    out = df.copy()
    # count columns arrive as int64 and cannot hold the standardized values
    out[cols] = out[cols].astype(float)
    for cond, idx in df.groupby("instruction_condition").groups.items():
        block = df.loc[idx, cols]
        sd = block.std(ddof=0).replace(0, 1.0)
        out.loc[idx, cols] = (block - block.mean()) / sd
    return out


def per_task_rank(df, cols):
    """Replace each value by its within-condition percentile rank."""
    out = df.copy()
    out[cols] = out[cols].astype(float)
    for cond, idx in df.groupby("instruction_condition").groups.items():
        out.loc[idx, cols] = df.loc[idx, cols].rank(pct=True)
    return out


def task_sensitivity(df, cols):
    """eta^2 of instruction_condition on each feature: how much of a feature's
    variance the task alone accounts for. High = the feature mostly tracks the
    job being done, not who is doing it."""
    s = {}
    for c in cols:
        g = df.groupby("instruction_condition")[c]
        gm, n, grand = g.mean(), g.size(), df[c].mean()
        ssb = (n * (gm - grand) ** 2).sum()
        sst = ((df[c] - grand) ** 2).sum()
        s[c] = ssb / sst if sst > 0 else 0.0
    return pd.Series(s).sort_values(ascending=False)


def transfer(train_df, test_df, target, cols):
    shared = sorted(set(train_df[target]) & set(test_df[target]))
    tr = train_df[train_df[target].isin(shared)]
    te = test_df[test_df[target].isin(shared)]
    if len(shared) < 2 or len(te) < 5:
        return None
    m = rf().fit(tr[cols].values, tr[target])
    p = m.predict(te[cols].values)
    return (balanced_accuracy_score(te[target], p),
            f1_score(te[target], p, average="macro"),
            len(shared), len(tr), len(te))


def report(tag, res, note=""):
    if res is None:
        print(f"  {tag:<34} n/a")
        return
    acc, f1, k, ntr, nte = res
    print(f"  {tag:<34} {acc:.3f}   (F1 {f1:.3f} | {k}-way, chance {1/k:.3f}"
          f" | train {ntr} test {nte}) {note}")


def main():
    global FEATS
    path = sys.argv[1] if len(sys.argv) > 1 else "m3_features.csv"
    df = pd.read_csv(path)
    df = df[df.instruction_condition.isin(["free_explore", "checklist"])].copy()
    c = df.model.value_counts()
    df = df[~df.model.isin(c[c < 5].index)].copy()
    FEATS = [x for x in df.columns
             if x.split("_")[0] in ("timing", "action", "struct", "geom")]

    print(f"n={len(df)} | features={len(FEATS)}")

    sens = task_sensitivity(df, FEATS)
    print("\nMost task-driven features (eta^2 of instruction alone):")
    for k, v in sens.head(6).items():
        print(f"  {k:<26} {v:.3f}")
    print("Least task-driven:")
    for k, v in sens.tail(4).items():
        print(f"  {k:<26} {v:.3f}")

    for target, scope in (("harness", df),
                          ("model", df[df.harness == "llm_driven"])):
        print("\n" + "=" * 74)
        print(f"TASK TRANSFER -- target = {target}   (train free_explore -> test checklist)")
        print("=" * 74)

        raw_tr = scope[scope.instruction_condition == "free_explore"]
        raw_te = scope[scope.instruction_condition == "checklist"]

        # M0 baseline
        report("M0  raw features (baseline)", transfer(raw_tr, raw_te, target, FEATS))

        # M1 per-task z-score
        z = per_task_z(scope, FEATS)
        report("M1  per-task centering",
               transfer(z[z.instruction_condition == "free_explore"],
                        z[z.instruction_condition == "checklist"], target, FEATS))

        # M2 per-task rank
        r = per_task_rank(scope, FEATS)
        report("M2  per-task ranks",
               transfer(r[r.instruction_condition == "free_explore"],
                        r[r.instruction_condition == "checklist"], target, FEATS))

        # M3 drop the most task-sensitive features
        s_local = task_sensitivity(scope, FEATS)
        for k in (4, 8, 12):
            keep = list(s_local.index[k:])
            report(f"M3  drop {k} most task-driven ({len(keep)} left)",
                   transfer(raw_tr, raw_te, target, keep))

        # M4 upper-bound reference: half the checklist data joins training
        te_half = raw_te.sample(frac=0.5, random_state=RNG)
        rest = raw_te.drop(te_half.index)
        report("M4  + half of target task (ref)",
               transfer(pd.concat([raw_tr, te_half]), rest, target, FEATS),
               "<- not a fix, upper bound")

    # ---- apples-to-apples ----
    # The headline "0.930 -> 0.667" compares different problems: the 6-way
    # in-distribution score includes the scripted profiles, which separate
    # perfectly and which only ever ran ONE task, so they cannot appear in a
    # transfer test at all. Scoring the SAME classes both ways is the only
    # fair measure of how much the task change actually costs.
    print("\n" + "=" * 74)
    print("APPLES-TO-APPLES: same classes, in-distribution vs transfer")
    print("=" * 74)
    from sklearn.model_selection import RepeatedStratifiedKFold

    def pooled_cv(sub, tgt, reps=10):
        X, y = sub[FEATS].values, sub[tgt].values
        k = int(min(5, pd.Series(y).value_counts().min()))
        if k < 2 or len(set(y)) < 2:
            return None
        s = []
        for tr, te in RepeatedStratifiedKFold(n_splits=k, n_repeats=reps,
                                              random_state=RNG).split(X, y):
            e = rf().fit(X[tr], y[tr])
            s.append(balanced_accuracy_score(y[te], e.predict(X[te])))
        return float(np.mean(s)), float(np.std(s))

    for target, scope in (("harness", df),
                          ("model", df[df.harness == "llm_driven"])):
        ct = pd.crosstab(scope[target], scope.instruction_condition)
        both = [x for x in ct.index if (ct.loc[x] > 0).all()]
        sub = scope[scope[target].isin(both)]
        m, s = pooled_cv(sub, target)
        tr_res = transfer(sub[sub.instruction_condition == "free_explore"],
                          sub[sub.instruction_condition == "checklist"],
                          target, FEATS)
        print(f"\n  {target}: {len(both)} classes present in BOTH tasks -> {both}")
        print(f"    same classes, in-distribution : {m:.3f} +/- {s:.3f}")
        print(f"    same classes, task transfer   : {tr_res[0]:.3f}")
        print(f"    true cost of changing task    : {m - tr_res[0]:.3f}")

    print("\n" + "=" * 74)
    print("READING THIS")
    print("=" * 74)
    print("M1/M2 beating M0 means the task mostly shifts feature SCALE, and")
    print("removing that scale recovers the agent's identity -- a real fix, with")
    print("the caveat that it needs several sessions from the same task to")
    print("normalise against. M3 beating M0 means a few task-driven features were")
    print("doing the damage and can simply be dropped, which needs no assumption")
    print("at all. If neither beats M0, the task genuinely changes the agent's")
    print("behaviour and the only honest fix is training across more tasks.")


if __name__ == "__main__":
    main()
