"""Events-to-attribution: how many events does a site need before it can tell?

Every result so far is end-of-session. A deployed site needs a verdict early, so
this replays each session's event stream truncated to its first k events,
recomputes the SAME features on the prefix, and measures out-of-fold balanced
accuracy as a function of k. The curve answers "after N events, how well can the
site attribute the harness / model / perception mode?", and `events-to-target`
(the smallest k reaching a fraction of the full-session score) is the headline.

It also gives the weak single-step cells a mechanism to test: a session that ends
before it has produced k events carries less evidence than the curve needs.
Whether that is WHY `single_action` scores 0.819 is a hypothesis this script can
inform (see the `done@k` column), not something it assumes.

What this is NOT
----------------
It is not a sequential probability ratio test and carries no sequential
false-positive guarantee. A verdict checked at many prefix lengths accumulates
false alarms; a real SPRT / e-value test at a fixed FPR budget is future work.
The classifier is also retrained at each k (out-of-fold), rather than one model
scoring any prefix as a deployed system would. Both limits belong next to the
figure.

Cohort. Only sessions with >= min(--ks) events are used, the SAME sessions at every
k and the same CV folds, so differences across k are the prefix and nothing else.
Sessions shorter than k are scored on their whole stream and counted in `done@k`.

Data. Reads exported CSVs; it does NOT import the Flask app or the exporter (both
would run `from app import app`, the line behind the 2026-09 outage). Features
come from features_core.py, the same code that built the certified tables.

    # labels: any certified table with session_uid + the label column
    ./venv/bin/python research/prefix_attribution.py \\
        --events events.csv --labels harness_M2.csv --label harness --clock server

Export events.csv on the VPS (Postgres), without loading the app:

    sudo -u postgres psql -d charweb -c "\\copy (SELECT id,user_id,session_uid,\\
      action_type,target,url,timestamp,details,server_ts,seq FROM tracked_action) \\
      TO '/tmp/events.csv' CSV HEADER"

Predict-mode only (out-of-fold, one cross-validation). Report numbers from the
certified tables; the Aug-2026 local snapshot is for testing the script.
"""
import argparse
import collections
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import balanced_accuracy_score
from sklearn.model_selection import StratifiedKFold, cross_val_predict

sys.path.insert(0, str(Path(__file__).resolve().parent))
from features_core import FEATURES, featurise  # noqa: E402  (pure; no app import)

# Session length is shaped by rate limits and step budgets; excluded everywhere
# in this project (and meaningless on a truncated prefix, where it equals k).
EXCLUDE = {"struct_n_events", "struct_duration_s", "timing_rate"}
USE = [f for f in FEATURES if f not in EXCLUDE]

Row = collections.namedtuple("Row", "timestamp server_ts action_type url target details seq")


def load_streams(events_csv, uids):
    ev = pd.read_csv(events_csv)
    need = {"session_uid", "action_type", "timestamp", "seq"}
    miss = need - set(ev.columns)
    if miss:
        sys.exit(f"{events_csv}: missing columns {sorted(miss)}")
    ev = ev[ev["session_uid"].isin(uids)].copy()
    ev["timestamp"] = pd.to_datetime(ev["timestamp"], format="mixed")
    if "server_ts" in ev.columns:
        ev["server_ts"] = pd.to_datetime(ev["server_ts"], format="mixed")
    else:
        ev["server_ts"] = pd.NaT
    for c in ("url", "target", "details"):
        if c not in ev.columns:
            ev[c] = None
    ev = ev.sort_values(["session_uid", "seq", "timestamp"])

    def s(v):
        return v if isinstance(v, str) else None

    streams = {}
    for uid, g in ev.groupby("session_uid"):
        streams[uid] = [
            Row(t.to_pydatetime(), (sv.to_pydatetime() if pd.notna(sv) else None),
                a, s(u), s(tg), s(d), sq)
            for t, sv, a, u, tg, d, sq in zip(g.timestamp, g.server_ts, g.action_type,
                                              g.url, g.target, g.details, g.seq)]
    return streams


def feature_matrix(streams, uids, k, clock):
    rows = []
    for uid in uids:
        s = streams[uid]
        f = featurise(s if k is None else s[:k], clock)
        rows.append([f[c] for c in USE] if f else None)
    return rows


def strat_boot(y, pred, rng, boots, alpha):
    labs = np.unique(y)
    out = np.empty(boots)
    pools = [np.flatnonzero(y == l) for l in labs]
    for b in range(boots):
        idx = np.concatenate([rng.choice(p, len(p), replace=True) for p in pools])
        out[b] = balanced_accuracy_score(y[idx], pred[idx])
    return np.percentile(out, [100 * alpha / 2, 100 * (1 - alpha / 2)])


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--events", required=True)
    ap.add_argument("--labels", required=True,
                    help="CSV with session_uid and the label column")
    ap.add_argument("--label", default="harness")
    ap.add_argument("--classes", default=None, help="comma list to keep")
    ap.add_argument("--clock", choices=("client", "server"), default="server",
                    help="server = timestamps a client cannot forge (default)")
    ap.add_argument("--skip-first", type=int, default=0,
                    help="drop the first N events of every session before taking prefixes "
                         "(the login/registration preamble). Early curves can otherwise "
                         "reflect how each harness STARTS rather than how it browses.")
    ap.add_argument("--ks", default="5,8,12,20,30,45,60,90,130,200")
    ap.add_argument("--frac", type=float, default=0.95,
                    help="events-to-target = smallest k with BA >= frac * full-session BA")
    ap.add_argument("--min-class", type=int, default=5)
    ap.add_argument("--trees", type=int, default=300)
    ap.add_argument("--boots", type=int, default=500)
    ap.add_argument("--alpha", type=float, default=0.05)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default="prefix_curve")
    ap.add_argument("--plot", action="store_true", help="also write figures/prefix_curve.png")
    a = ap.parse_args()

    lab = pd.read_csv(a.labels)
    for c in ("session_uid", a.label):
        if c not in lab.columns:
            sys.exit(f"{a.labels}: no '{c}' column")
    lab = lab.dropna(subset=[a.label]).drop_duplicates("session_uid")
    if a.classes:
        lab = lab[lab[a.label].isin([c.strip() for c in a.classes.split(",")])]

    streams = load_streams(a.events, set(lab["session_uid"]))
    if a.skip_first:
        streams = {u: v[a.skip_first:] for u, v in streams.items()}
    clock = (lambda r: r.server_ts) if a.clock == "server" else (lambda r: r.timestamp)

    ks = [int(x) for x in a.ks.split(",")]
    kmin = min(ks)
    lab = lab[lab["session_uid"].isin(streams)]
    lab = lab[lab["session_uid"].map(lambda u: len(streams[u])) >= kmin]

    # Drop classes too small to cross-validate.
    counts = lab[a.label].value_counts()
    small = counts[counts < a.min_class]
    if len(small):
        print(f"dropping classes with < {a.min_class} usable sessions: {small.to_dict()}")
        lab = lab[~lab[a.label].isin(small.index)]
    if lab[a.label].nunique() < 2:
        sys.exit("need >= 2 classes with enough sessions")

    uids = list(lab["session_uid"])
    y = lab[a.label].to_numpy()
    n_ev = np.array([len(streams[u]) for u in uids])
    k_cls = len(np.unique(y))
    n_splits = int(min(5, counts[counts >= a.min_class].min()))
    cv = StratifiedKFold(n_splits, shuffle=True, random_state=a.seed)
    rng = np.random.default_rng(a.seed)

    print(f"{a.events} | label={a.label} clock={a.clock} skip_first={a.skip_first} | n={len(uids)} classes={k_cls} "
          f"chance={1 / k_cls:.3f} | {n_splits}-fold | features={len(USE)}")
    print(f"  class counts: {dict(sorted(collections.Counter(y).items()))}")
    print(f"  events/session: median {int(np.median(n_ev))}  "
          f"p10 {int(np.percentile(n_ev, 10))}  p90 {int(np.percentile(n_ev, 90))}\n")

    results = []
    for k in ks + [None]:
        X = feature_matrix(streams, uids, k, clock)
        ok = [r is not None for r in X]
        if not all(ok):
            # featurise needs >= 5 events and a usable clock; a cohort that
            # shrinks with k would make the curve incomparable, so say so.
            bad = len(ok) - sum(ok)
            print(f"  k={k}: {bad} sessions had no usable features (clock/rows) -- "
                  f"dropping them at THIS k only; curve may not be like-for-like")
        Xk = np.array([r if r is not None else [0.0] * len(USE) for r in X], dtype=float)
        Xk = np.nan_to_num(Xk)
        clf = RandomForestClassifier(n_estimators=a.trees, random_state=a.seed, n_jobs=-1)
        pred = cross_val_predict(clf, Xk, y, cv=cv)
        ba = balanced_accuracy_score(y, pred)
        lo, hi = strat_boot(y, pred, rng, a.boots, a.alpha)
        rec = {l: float((pred[y == l] == l).mean()) for l in np.unique(y)}
        kk = "full" if k is None else k
        done = float((n_ev <= (10 ** 9 if k is None else k)).mean())
        results.append(dict(k=kk, n=len(y), done_frac=round(done, 3),
                            ba=round(ba, 4), lo=round(lo, 4), hi=round(hi, 4),
                            **{f"recall_{l}": round(v, 3) for l, v in rec.items()}))
        print(f"  k={str(kk):>5}  BA {ba:.3f}  [{lo:.3f}, {hi:.3f}]   done@k {done:5.1%}")

    res = pd.DataFrame(results)
    res.to_csv(f"{a.out}.csv", index=False)

    full = res.iloc[-1]["ba"]
    target = a.frac * full
    reach = res[(res["k"] != "full") & (res["ba"] >= target)]
    print(f"\nfull-session BA {full:.3f}; target {a.frac:.0%} of it = {target:.3f}")
    if len(reach):
        kstar = reach.iloc[0]
        print(f"events-to-target: {kstar['k']} events (BA {kstar['ba']:.3f}, "
              f"CI [{kstar['lo']:.3f}, {kstar['hi']:.3f}])")
    else:
        print("events-to-target: not reached within the listed prefixes")
    print("(Not an SPRT: no sequential FPR guarantee. See module docstring.)")
    print(f"wrote {a.out}.csv")

    if a.plot:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
        except ImportError:
            print("matplotlib not installed; skipping --plot")
            return 0
        d = res[res["k"] != "full"].copy()
        d["k"] = d["k"].astype(int)
        fig, ax = plt.subplots(figsize=(7.5, 4.4))
        ax.fill_between(d["k"], d["lo"], d["hi"], color="#2a78d6", alpha=0.18, linewidth=0)
        ax.plot(d["k"], d["ba"], color="#2a78d6", linewidth=2, marker="o", markersize=6)
        ax.axhline(1 / k_cls, color="#52514e", linestyle=(0, (4, 3)), linewidth=1.2)
        ax.text(d["k"].min(), 1 / k_cls + 0.01, f"chance {1 / k_cls:.3f}", color="#52514e",
                fontsize=9)
        ax.axhline(full, color="#eb6834", linestyle=(0, (2, 2)), linewidth=1.4)
        ax.text(d["k"].max(), full + 0.01, f"full session {full:.3f}", color="#eb6834",
                fontsize=9, ha="right")
        ax.set_xscale("log")
        ax.set_xlabel("Events observed (log scale)")
        ax.set_ylabel("Balanced accuracy (out-of-fold)")
        ax.set_ylim(0, 1.08)
        ax.set_title(f"Events to attribution: {a.label}", loc="left", fontweight="bold")
        ax.grid(color="#e3e2de", linewidth=0.6)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
        out = Path(__file__).resolve().parent.parent / "figures"
        out.mkdir(exist_ok=True)
        fig.savefig(out / "prefix_curve.png", dpi=200, bbox_inches="tight")
        print(f"wrote {out / 'prefix_curve.png'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
