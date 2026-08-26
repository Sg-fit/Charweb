"""M3 analysis: H1 variance decomposition, H2 generalization, H3 ablation.

Design note that shapes every test below: **model is nested inside harness**,
not crossed with it. Only `llm_driven` runs multiple models; the scripted
harnesses have no model at all and `fenris` has exactly one. So a single
crossed model x harness ANOVA is not identifiable. Instead:

  * the HARNESS effect is measured across the whole dataset, and
  * the MODEL effect is measured WITHIN llm_driven, where harness is held
    constant by construction.

That split is the honest version of the claim and is also the stronger one:
each factor is estimated with the other one fixed rather than confounded.

Usage:  python research/m3_analysis.py m3_features.csv
"""
import sys
import warnings

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LinearRegression
from sklearn.metrics import balanced_accuracy_score, f1_score, confusion_matrix
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.preprocessing import OneHotEncoder, StandardScaler

warnings.filterwarnings("ignore")
RNG = 0

TIMING = [c for c in
          "timing_iv_mean timing_iv_cv timing_iv_median timing_iv_p90 "
          "timing_iv_min timing_kd_mean timing_kd_cv timing_rate".split()]
ACTION = ("action_click_pct action_keydown_pct action_mousemove_pct "
          "action_scroll_pct action_pageload_pct action_other_pct "
          "action_n_types action_entropy").split()
STRUCT = ("struct_n_urls struct_events_per_url struct_revisit_rate "
          "struct_duration_s struct_n_events").split()
GEOM = "geom_vel_mean geom_vel_cv geom_vel_max geom_mousemove_n".split()
ALL = TIMING + ACTION + STRUCT + GEOM

GROUPS = {
    "all (25)": ALL,
    "timing (8)": TIMING,
    "action (8)": ACTION,
    "structure (5)": STRUCT,
    "geometry (4)": GEOM,
    "DOM-only (timing+action+structure, 21)": TIMING + ACTION + STRUCT,
    "no-timing (action+structure+geom, 17)": ACTION + STRUCT + GEOM,
}


def rf():
    return RandomForestClassifier(n_estimators=500, random_state=RNG,
                                  class_weight="balanced", n_jobs=-1)


def hr(t):
    print("\n" + "=" * 72)
    print(t)
    print("=" * 72)


def cv_score(X, y, cols, folds=5):
    """Stratified CV. Folds are capped by the rarest class so a thin cell
    (e.g. a model with 9 sessions) can't create an empty test fold.

    Reported against BALANCED accuracy, whose chance level is 1/n_classes --
    not the majority-class rate. A majority-class predictor scores 1/k here,
    so on 6 harnesses 'always guess llm_driven' earns 0.167, not 0.725.
    """
    counts = pd.Series(y).value_counts()
    k = int(min(folds, counts.min()))
    if k < 2 or len(counts) < 2:
        return None
    cvp = cross_val_predict(rf(), X[cols].values, y,
                            cv=StratifiedKFold(k, shuffle=True, random_state=RNG))
    return {"balanced_acc": balanced_accuracy_score(y, cvp),
            "macro_f1": f1_score(y, cvp, average="macro"),
            "chance": 1.0 / len(counts), "n_classes": len(counts),
            "k": k, "pred": cvp}


def perm_test(X, y, cols, observed, n=100):
    """Label-permutation test: how often does shuffled data beat the real
    score? Gives an empirical p-value that doesn't assume normality --
    appropriate for small, unbalanced cells. Uses a lighter forest (the null
    distribution doesn't need 500 trees) and 3 folds to stay tractable."""
    rs = np.random.RandomState(RNG)
    y = np.asarray(y)
    light = RandomForestClassifier(n_estimators=60, random_state=RNG,
                                   class_weight="balanced", n_jobs=-1)
    counts = pd.Series(y).value_counts()
    k = int(min(3, counts.min()))
    if k < 2:
        return float("nan")
    Xv = X[cols].values
    wins = 0
    for _ in range(n):
        yp = rs.permutation(y)
        cvp = cross_val_predict(light, Xv, yp,
                                cv=StratifiedKFold(k, shuffle=True, random_state=RNG))
        if balanced_accuracy_score(yp, cvp) >= observed:
            wins += 1
    return (wins + 1) / (n + 1)


def semipartial_r2(df, factors, cols):
    """Variance in the standardized features uniquely explained by each factor.

    Fits one-hot factor -> features by least squares and asks how much R^2
    is LOST when a factor is dropped. That drop (semi-partial R^2) is the
    share only that factor can explain, so shared variance is never
    double-counted -- the right thing for an unbalanced design like this one.
    """
    Y = StandardScaler().fit_transform(df[cols].values)

    def r2(fs):
        if not fs:
            return 0.0
        X = OneHotEncoder(drop="first", sparse_output=False,
                          handle_unknown="ignore").fit_transform(df[fs].astype(str))
        if X.shape[1] == 0:
            return 0.0
        pred = LinearRegression().fit(X, Y).predict(X)
        ss_res = ((Y - pred) ** 2).sum()
        ss_tot = (Y ** 2).sum()
        return 1 - ss_res / ss_tot

    full = r2(factors)
    out = {}
    for f in factors:
        out[f] = full - r2([x for x in factors if x != f])
    out["_full"] = full
    return out


def eta_sq(df, factor, cols):
    """Per-feature one-way eta^2 (between-group share of variance)."""
    rows = {}
    for c in cols:
        g = df.groupby(factor)[c]
        gm, n, grand = g.mean(), g.size(), df[c].mean()
        ssb = (n * (gm - grand) ** 2).sum()
        sst = ((df[c] - grand) ** 2).sum()
        rows[c] = ssb / sst if sst > 0 else 0.0
    return pd.Series(rows).sort_values(ascending=False)


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "m3_features.csv"
    df = pd.read_csv(path)

    # gemini has a single session and 'matched' is a stray label from two
    # pilot runs; neither can support a train/test split.
    df = df[df.instruction_condition.isin(["free_explore", "checklist"])].copy()
    thin = df.model.value_counts()
    df = df[~df.model.isin(thin[thin < 5].index)].copy()
    llm = df[df.harness == "llm_driven"].copy()

    print(f"analysed sessions: {len(df)}  (llm_driven subset: {len(llm)})")
    print(f"harnesses: {df.harness.nunique()}   models in llm_driven: {llm.model.nunique()}")

    # ---------------- H1 ----------------
    hr("H1  VARIANCE DECOMPOSITION  (semi-partial R^2 on 25 standardized features)")
    a = semipartial_r2(df, ["harness", "instruction_condition"], ALL)
    print("Across ALL sessions (model nested, so not entered here):")
    print(f"  harness      unique R^2 = {a['harness']:.3f}")
    print(f"  instruction  unique R^2 = {a['instruction_condition']:.3f}")
    print(f"  full model         R^2 = {a['_full']:.3f}")

    b = semipartial_r2(llm, ["model", "instruction_condition"], ALL)
    print("\nWithin llm_driven only (harness held constant -> model is identifiable):")
    print(f"  model        unique R^2 = {b['model']:.3f}")
    print(f"  instruction  unique R^2 = {b['instruction_condition']:.3f}")
    print(f"  full model         R^2 = {b['_full']:.3f}")

    print("\nTop 6 features by harness eta^2:")
    for k, v in eta_sq(df, "harness", ALL).head(6).items():
        print(f"    {k:<26} {v:.3f}")
    print("Top 6 features by model eta^2 (within llm_driven):")
    for k, v in eta_sq(llm, "model", ALL).head(6).items():
        print(f"    {k:<26} {v:.3f}")

    # ---------------- H2 ----------------
    hr("H2  ATTRIBUTION & GENERALIZATION")

    r = cv_score(df, df.harness.values, ALL)
    p = perm_test(df, df.harness.values, ALL, r["balanced_acc"])
    print(f"[H2-1] Harness attribution, {df.harness.nunique()}-way, {r['k']}-fold CV")
    print(f"       balanced acc {r['balanced_acc']:.3f} | macro-F1 {r['macro_f1']:.3f}"
          f" | chance {r['chance']:.3f} | permutation p {p:.4f}")
    labs = sorted(df.harness.unique())
    cm = confusion_matrix(df.harness, r["pred"], labels=labs)
    print("\n       confusion (rows=true):")
    print("       " + " ".join(f"{l[:9]:>10}" for l in labs))
    for l, row in zip(labs, cm):
        print(f"       {l[:18]:<18}" + " ".join(f"{v:>10}" for v in row))

    rm = cv_score(llm, llm.model.values, ALL)
    pm = perm_test(llm, llm.model.values, ALL, rm["balanced_acc"])
    print(f"\n[H2-2] Model attribution within llm_driven, {llm.model.nunique()}-way,"
          f" {rm['k']}-fold CV")
    print(f"       balanced acc {rm['balanced_acc']:.3f} | macro-F1 {rm['macro_f1']:.3f}"
          f" | chance {rm['chance']:.3f} | permutation p {pm:.4f}")
    print("       per-model recall:")
    for m_ in sorted(llm.model.unique()):
        mask = llm.model.values == m_
        rec = (rm["pred"][mask] == m_).mean()
        print(f"         {m_:<40} n={mask.sum():>3}  recall {rec:.3f}")

    # Cross-instruction: train on one task framing, test on the other. This is
    # the "does the fingerprint survive a different task?" test.
    print("\n[H2-3] Cross-instruction generalization (train free_explore -> test checklist)")
    for name, sub, target in (("harness", df, "harness"), ("model", llm, "model")):
        tr = sub[sub.instruction_condition == "free_explore"]
        te = sub[sub.instruction_condition == "checklist"]
        shared = sorted(set(tr[target]) & set(te[target]))
        tr2, te2 = tr[tr[target].isin(shared)], te[te[target].isin(shared)]
        if len(shared) < 2 or len(te2) < 5:
            print(f"       {name}: not enough overlap ({len(shared)} shared classes)")
            continue
        m = rf().fit(tr2[ALL].values, tr2[target])
        pr = m.predict(te2[ALL].values)
        print(f"       {name:<8} {len(shared)}-way | train {len(tr2)} test {len(te2)}"
              f" | balanced acc {balanced_accuracy_score(te2[target], pr):.3f}"
              f" | macro-F1 {f1_score(te2[target], pr, average='macro'):.3f}"
              f" | chance {1/len(shared):.3f}")

    # Leave-one-model-out: can the harness be recognised on a model the
    # classifier has never seen? Tests that the harness signature is not just
    # a proxy for "which model wrote the actions".
    print("\n[H2-4] Leave-one-model-out: harness recall on an UNSEEN model")
    accs = []
    for m_ in sorted(llm.model.unique()):
        tr = df[~((df.harness == "llm_driven") & (df.model == m_))]
        te = df[(df.harness == "llm_driven") & (df.model == m_)]
        if len(te) < 5:
            continue
        clf = rf().fit(tr[ALL].values, tr.harness)
        acc = (clf.predict(te[ALL].values) == "llm_driven").mean()
        accs.append(acc)
        print(f"       held out {m_:<40} n={len(te):>3}  recall {acc:.3f}")
    if accs:
        print(f"       MEAN recall on unseen models: {np.mean(accs):.3f}")

    # Leave-one-harness-out for the scripted family: the three matched-task
    # profiles differ ONLY in behavioural policy, so separating them is the
    # cleanest evidence of a style fingerprint (task coverage is identical).
    scr = df[df.harness.str.startswith("scripted_")]
    if scr.harness.nunique() > 1:
        rs = cv_score(scr, scr.harness.values, ALL)
        ps = perm_test(scr, scr.harness.values, ALL, rs["balanced_acc"])
        print(f"\n[H2-5] Matched-task scripted profiles only ({scr.harness.nunique()}-way,"
              f" n={len(scr)}): balanced acc {rs['balanced_acc']:.3f}"
              f" | macro-F1 {rs['macro_f1']:.3f} | chance {rs['chance']:.3f}"
              f" | permutation p {ps:.4f}")
        print("       (identical task list -> this is pure timing/mouse/typing style)")

    # ---------------- H3 ----------------
    hr("H3  FEATURE-GROUP ABLATION  (DOM/behavioural vs mouse geometry)")
    print(f"{'feature group':<42}{'harness bal-acc':>17}{'model bal-acc':>15}")
    for gname, cols in GROUPS.items():
        rh = cv_score(df, df.harness.values, cols)
        rmm = cv_score(llm, llm.model.values, cols)
        print(f"{gname:<42}{rh['balanced_acc']:>17.3f}{rmm['balanced_acc']:>15.3f}")

    gm = df.groupby("harness").geom_mousemove_n.mean()
    print("\nMean mousemove events per session, by harness:")
    for k, v in gm.sort_values(ascending=False).items():
        print(f"    {k:<22}{v:>10.1f}")
    print("\nDOM-driven harnesses emit essentially no mouse geometry, so geometry")
    print("features cannot carry their attribution -- H3's stated blind spot.")


if __name__ == "__main__":
    main()
