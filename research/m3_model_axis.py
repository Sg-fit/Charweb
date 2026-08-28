"""Is the MODEL axis real? Significance, effect size, and where it comes from.

The harness axis scores 1.000 against a chance of 0.250, which needs no
defending. The model axis scores 0.626 against 0.333 -- clearly above chance
to the eye, but at n=73 with three classes that is exactly the regime where a
number looks convincing and is not. Everything here exists to decide that.

    permutation test   shuffle the model labels and refit, many times. This
                       builds the distribution of scores obtainable from THIS
                       dataset with no real signal in it. If the observed score
                       sits inside that distribution, there is nothing to report.

    LOTO permutation   the same, but for the harder claim -- that the
                       fingerprint transfers to a task never trained on. The
                       in-distribution result being significant does not make
                       the transfer result significant; they are separate
                       claims and get separate tests.

    per-model recall   an average of 0.626 can mean 'all three models are
                       moderately identifiable' or 'one model is obvious and
                       the other two are coin flips'. Those are different
                       findings and the mean hides which one you have.

    feature groups     if the signal is carried by timing alone, a reviewer
                       will ask whether it is really provider latency rather
                       than model behaviour. Reported per group so that is
                       answerable rather than arguable.

    python research/m3_model_axis.py models2.csv
    python research/m3_model_axis.py models2.csv --perms 500
"""
import argparse
import warnings

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import balanced_accuracy_score, confusion_matrix
from sklearn.model_selection import RepeatedStratifiedKFold, StratifiedKFold

warnings.filterwarnings("ignore")
RNG = 0
GROUPS = ("timing", "action", "struct", "geom")


def rf(seed=RNG):
    return RandomForestClassifier(n_estimators=500, random_state=seed,
                                  class_weight="balanced", n_jobs=-1)


def cols_of(df, groups=GROUPS):
    return [c for c in df.columns if c.split("_")[0] in groups]


def pooled_cv(X, y, reps=10, seed=RNG):
    """Balanced accuracy under repeated stratified CV."""
    k = int(min(5, pd.Series(y).value_counts().min()))
    if k < 2:
        return float("nan")
    s = [balanced_accuracy_score(y[te], rf(seed).fit(X[tr], y[tr]).predict(X[te]))
         for tr, te in RepeatedStratifiedKFold(
             n_splits=k, n_repeats=reps, random_state=seed).split(X, y)]
    return float(np.mean(s))


def loto_mean(df, cols, target="model"):
    """Mean balanced accuracy over held-out tasks."""
    scores = []
    for t in sorted(df.instruction_condition.unique()):
        te = df[df.instruction_condition == t]
        tr = df[df.instruction_condition != t]
        if len(te) < 6 or tr[target].nunique() < 2 or te[target].nunique() < 2:
            continue
        m = rf().fit(tr[cols].values, tr[target])
        scores.append(balanced_accuracy_score(te[target], m.predict(te[cols].values)))
    return float(np.mean(scores)) if scores else float("nan")


def permutation_p(observed, null_scores):
    """One-sided p with the +1 correction: with B permutations the smallest
    honest p is 1/(B+1), never 0. Reporting p=0 from a finite permutation test
    is a claim the test cannot support."""
    null_scores = np.asarray(null_scores)
    b = len(null_scores)
    return (1 + int((null_scores >= observed).sum())) / (b + 1)


def main():
    ap = argparse.ArgumentParser(description="Model-axis significance testing")
    ap.add_argument("csv", nargs="?", default="models2.csv")
    ap.add_argument("--perms", type=int, default=200,
                    help="label shuffles. 200 resolves p down to ~0.005; "
                         "raise it only if the p you get is near your threshold")
    ap.add_argument("--reps", type=int, default=10, help="CV repeats")
    args = ap.parse_args()

    df = pd.read_csv(args.csv)
    df = df[df.harness == "llm_driven"] if "llm_driven" in set(df.harness) else df
    cols = cols_of(df)
    X, y = df[cols].values, df.model.values
    classes = sorted(pd.unique(y))
    chance = 1.0 / len(classes)

    print(f"n={len(df)}  models={len(classes)}  features={len(cols)}")
    print(f"chance = {chance:.3f}\n")
    for m, n in df.model.value_counts().items():
        print(f"    {m:<40}{n:>4}")

    # ---------- 1. in-distribution, with a permutation null ----------
    print("\n" + "=" * 70)
    print("1. IN-DISTRIBUTION  (pooled CV, model identity)")
    print("=" * 70)
    obs = pooled_cv(X, y, reps=args.reps)
    print(f"  observed balanced accuracy   {obs:.3f}")
    print(f"  running {args.perms} label shuffles...", flush=True)
    rng = np.random.default_rng(RNG)
    null = []
    for i in range(args.perms):
        null.append(pooled_cv(X, rng.permutation(y), reps=2, seed=i))
    null = np.array(null)
    p = permutation_p(obs, null)
    print(f"  null (shuffled labels)       {null.mean():.3f} +/- {null.std():.3f}"
          f"   95th pct {np.percentile(null, 95):.3f}")
    print(f"  p = {p:.4f}" + ("" if p >= 1.0 / (args.perms + 1) else " (floor)"))
    print(f"  lift over chance             {obs - chance:+.3f}")
    verdict_in = p < 0.05
    print("  => " + ("model identity IS recoverable above chance."
                     if verdict_in else
                     "NOT significant -- do not claim a model axis."))

    # ---------- 2. LOTO, with its own permutation null ----------
    print("\n" + "=" * 70)
    print("2. TRANSFER TO AN UNSEEN TASK  (leave-one-task-out)")
    print("=" * 70)
    obs_l = loto_mean(df, cols)
    print(f"  observed LOTO mean           {obs_l:.3f}")
    print(f"  running {args.perms} label shuffles...", flush=True)
    null_l = []
    for i in range(args.perms):
        d2 = df.copy()
        # Shuffle WITHIN task, so the null keeps each task's class balance and
        # only destroys the session->model link. Shuffling globally would also
        # scramble the task composition and make the null too easy to beat.
        d2["model"] = (d2.groupby("instruction_condition")["model"]
                       .transform(lambda s: rng.permutation(s.values)))
        null_l.append(loto_mean(d2, cols))
    null_l = np.array(null_l)
    p_l = permutation_p(obs_l, null_l)
    print(f"  null                         {null_l.mean():.3f} +/- {null_l.std():.3f}"
          f"   95th pct {np.percentile(null_l, 95):.3f}")
    print(f"  p = {p_l:.4f}")
    print("  => " + ("the fingerprint SURVIVES an unseen task."
                     if p_l < 0.05 else
                     "transfer to an unseen task is NOT established."))

    # ---------- 3. which models are actually identifiable ----------
    print("\n" + "=" * 70)
    print("3. PER-MODEL RECALL  (is the mean hiding one easy class?)")
    print("=" * 70)
    k = int(min(5, pd.Series(y).value_counts().min()))
    pred = np.empty_like(y)
    for tr, te in StratifiedKFold(n_splits=k, shuffle=True,
                                  random_state=RNG).split(X, y):
        pred[te] = rf().fit(X[tr], y[tr]).predict(X[te])
    cm = confusion_matrix(y, pred, labels=classes)
    for i, c in enumerate(classes):
        rec = cm[i, i] / cm[i].sum() if cm[i].sum() else float("nan")
        conf = classes[int(np.argmax(np.where(np.arange(len(classes)) == i,
                                              -1, cm[i])))]
        print(f"  {c:<40}{rec:>7.3f}   most confused with: {conf}")
    print("\n  confusion matrix (rows = truth):")
    print(pd.DataFrame(cm, index=[c[:22] for c in classes],
                       columns=[c[:10] for c in classes]).to_string())

    # ---------- 4. where the signal lives ----------
    print("\n" + "=" * 70)
    print("4. FEATURE GROUPS  (is this behaviour, or provider latency?)")
    print("=" * 70)
    for g in GROUPS:
        gc = cols_of(df, (g,))
        if not gc:
            continue
        print(f"  {g+' only':<20}{pooled_cv(df[gc].values, y, reps=5):>8.3f}"
              f"   ({len(gc)} features)")
    no_t = [c for c in cols if not c.startswith("timing_")]
    print(f"  {'ALL minus timing':<20}{pooled_cv(df[no_t].values, y, reps=5):>8.3f}")
    print(f"  {'ALL':<20}{obs:>8.3f}")
    print("\n  If 'ALL minus timing' stays well above chance, the model axis is "
          "not\n  reducible to how fast the provider answered.")

    # ---------- summary ----------
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  in-distribution   {obs:.3f}  (chance {chance:.3f})  p={p:.4f}")
    print(f"  unseen task       {obs_l:.3f}  (chance {chance:.3f})  p={p_l:.4f}")
    print("\n  Claim supported: " + (
        "model identity is recoverable, and transfers to unseen tasks."
        if verdict_in and p_l < 0.05 else
        "model identity is recoverable in-distribution only."
        if verdict_in else
        "none -- the model axis is not distinguishable from noise here."))


if __name__ == "__main__":
    main()
