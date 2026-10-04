"""H1 — variance decomposition of the behavioural signature (Phase 1 headline).

`AI_only_reframe_ISEF.md` §6.1 calls this "the headline table": how much of the
behavioural variance is attributable to HARNESS, to MODEL, to INSTRUCTION, and
to the harness x model interaction. H2 (LOTO) answers "can we attribute?"; this
answers "what is the fingerprint MADE of?" -- and it is the only analysis that
speaks to decomposability directly.

Two decompositions, because the design is not fully crossed:

  FULL      every certified session. `model` is nested inside `harness` (the
            scripted arms have no model at all), so only harness and
            instruction are estimable here.
  CROSSED   the subset where the same models appear under two or more
            harnesses. Harness, model, instruction AND the harness x model
            interaction are all estimable. This is the design H1 actually
            asks for; the full set is reported for context.

Method: Type-II-style sums of squares. For each effect, refit the model without
that effect's columns; the rise in residual sum of squares is the variance that
effect uniquely explains, having adjusted for every other term. That is the
right choice for an unbalanced design -- sequential (Type I) SS would make the
answer depend on the order the factors are listed.

Response variables: every length-free feature individually (median share is
reported across them), plus PC1 of the standardised feature matrix as a single
scalar "behavioural signature" so there is one clean table for the poster.

CIs by resampling SESSIONS with replacement -- the session is the unit of
observation, so that is the level the uncertainty lives at.

    ./venv/bin/python research/h1_variance.py
"""
import sys
import numpy as np
import pandas as pd

DROP = {"struct_n_events", "struct_duration_s", "timing_rate"}
PREFIXES = ("timing_", "action_", "struct_", "geom_")
FILES = ["harness_M2.csv", "modelaxis_v5.csv", "percept_M2.csv", "crossed_cell.csv"]
BOOTS = 300
MIN_CELL = 5          # sessions needed before a (harness, model) cell counts


def load():
    frames = []
    for f in FILES:
        try:
            frames.append(pd.read_csv(f))
        except FileNotFoundError:
            print(f"  (missing {f} -- skipped)")
    if not frames:
        sys.exit("no input files found")
    d = pd.concat(frames, ignore_index=True)
    d = d.drop_duplicates(subset="session_uid").reset_index(drop=True)
    for c in ("harness", "model", "instruction_condition"):
        d[c] = d[c].fillna("none").astype(str)
    return d


def features(df):
    return [c for c in df.columns
            if c.startswith(PREFIXES) and c not in DROP
            and pd.api.types.is_numeric_dtype(df[c])]


def design(df, terms):
    """Dummy-coded design matrix. `terms` is a list of column-name tuples;
    a tuple of two names is their interaction."""
    parts = [np.ones((len(df), 1))]
    for t in terms:
        key = df[t[0]].astype(str) if len(t) == 1 else \
              df[t[0]].astype(str) + "|" + df[t[1]].astype(str)
        dm = pd.get_dummies(key, drop_first=True).astype(float).values
        if dm.shape[1]:
            parts.append(dm)
    return np.hstack(parts)


def rss(X, y):
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    r = y - X @ beta
    return float(r @ r)


def shares(df, y, terms):
    """Type-II variance share per term, plus residual."""
    ss_tot = float(((y - y.mean()) ** 2).sum())
    if ss_tot <= 0:
        return None
    full = rss(design(df, terms), y)
    out = {}
    for t in terms:
        reduced = [u for u in terms if u != t]
        # an interaction cannot be estimated once its parents are gone, so
        # dropping a main effect also drops interactions that contain it
        reduced = [u for u in reduced
                   if not (len(t) == 1 and len(u) == 2 and t[0] in u)]
        name = t[0] if len(t) == 1 else f"{t[0]} x {t[1]}"
        out[name] = max(0.0, (rss(design(df, reduced), y) - full)) / ss_tot
    out["residual"] = full / ss_tot
    return out


def pc1(df, feats):
    X = df[feats].fillna(0).values.astype(float)
    X = (X - X.mean(0)) / (X.std(0) + 1e-12)
    U, S, Vt = np.linalg.svd(X, full_matrices=False)
    var = (S ** 2 / (S ** 2).sum())[0]
    return U[:, 0] * S[0], var


def report(df, terms, title, feats):
    print(f"\n{'='*74}\n{title}\n{'='*74}")
    print(f"n = {len(df)} sessions, {len(feats)} features")
    print(df.groupby(["harness", "model"]).size().to_string())

    y, explained = pc1(df, feats)
    print(f"\nPC1 explains {explained:.1%} of feature variance "
          f"-- used as the single 'behavioural signature' response\n")

    s = shares(df, y, terms)
    if s is None:
        print("  degenerate response; skipped"); return

    rng = np.random.default_rng(0)
    boot = {k: [] for k in s}
    for _ in range(BOOTS):
        idx = rng.integers(0, len(df), len(df))
        sub = df.iloc[idx].reset_index(drop=True)
        yb, _ = pc1(sub, feats)
        sb = shares(sub, yb, terms)
        if sb:
            for k in boot:
                boot[k].append(sb.get(k, 0.0))

    print(f"{'effect':<26}{'share of PC1':>14}{'95% CI':>22}")
    for k, v in sorted(s.items(), key=lambda kv: -kv[1]):
        b = np.array(boot[k])
        lo, hi = np.percentile(b, [2.5, 97.5]) if len(b) else (np.nan, np.nan)
        print(f"{k:<26}{v:>14.3f}   [{lo:.3f}, {hi:.3f}]")

    # per-feature medians: PC1 can be dominated by one feature family, so the
    # median across features is the check that the story is not an artefact of
    # the projection.
    per = [shares(df, df[f].fillna(0).values.astype(float), terms)
           for f in feats]
    per = [p for p in per if p]
    print(f"\nmedian share across the {len(per)} features individually:")
    for k in s:
        vals = [p.get(k, 0.0) for p in per]
        print(f"  {k:<26}{np.median(vals):>8.3f}")


def main():
    d = load()
    feats = features(d)
    print(f"loaded {len(d)} unique certified sessions")

    report(d, [("harness",), ("instruction_condition",)],
           "FULL SET -- harness nested model, so model is not estimable", feats)

    # crossed subset: harnesses where >=2 models each clear MIN_CELL
    cnt = d.groupby(["harness", "model"]).size()
    ok_h = [h for h in d.harness.unique()
            if sum(1 for (hh, mm), n in cnt.items()
                   if hh == h and n >= MIN_CELL and mm != "none") >= 2]
    sub = d[d.harness.isin(ok_h) & (d.model != "none")].reset_index(drop=True)
    if sub.harness.nunique() < 2 or sub.model.nunique() < 2:
        print("\n\n** no crossed (harness x model) region with >=2 models under "
              ">=2 harnesses -- the interaction term is not estimable. **")
        print("   harnesses with >=2 models:", ok_h)
        controlled(d, feats)
        return
    report(sub, [("harness",), ("model",), ("instruction_condition",),
                 ("harness", "model")],
           "CROSSED SUBSET -- harness x model estimable (this is H1)", feats)


def controlled(d, feats):
    """H1 without a crossed design: two comparisons, each holding the other
    factor constant. The interaction is unidentifiable here (only llm_driven
    carries two models), but both MAIN effects are cleanly estimable -- and
    that is what the decomposability claim actually needs.

    A: model held at the one model that appears under several harnesses.
    B: harness held at the one harness that carries several models.

    Reported side by side, these are directly comparable variance shares for
    the two axes, free of the scripted-vs-LLM gap that inflates any pooled
    harness estimate."""
    cnt = d.groupby(["model", "harness"]).size()
    m_multi = max((m for m in d.model.unique() if m != "none_scripted"),
                  key=lambda m: sum(1 for (mm, _), n in cnt.items()
                                    if mm == m and n >= MIN_CELL), default=None)
    h_multi = max((h for h in d.harness.unique()),
                  key=lambda h: len({mm for (mm, hh), n in cnt.items()
                                     if hh == h and n >= MIN_CELL
                                     and mm != "none_scripted"}), default=None)

    a = d[d.model == m_multi]
    if a.harness.nunique() >= 2:
        report(a, [("harness",), ("instruction_condition",)],
               f"A: HARNESS effect, model held constant = {m_multi}", feats)

    b = d[(d.harness == h_multi) & (d.model != "none_scripted")]
    if b.model.nunique() >= 2:
        report(b, [("model",), ("instruction_condition",)],
               f"B: MODEL effect, harness held constant = {h_multi}", feats)

    print(f"\n{'='*74}\nH1 VERDICT\n{'='*74}")
    print("Compare the harness share in (A) against the model share in (B).")
    print("Both are single-factor shares with the other factor fixed, so they")
    print("are on the same scale. The interaction term is NOT estimable in")
    print("this dataset and must be reported as a design limitation, not")
    print("as a null result.")


if __name__ == "__main__":
    main()
