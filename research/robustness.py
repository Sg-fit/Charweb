"""Simulated-evasion robustness table (Phase 2, H6).

An adversary can only manipulate what the client controls. Removing a feature
group is therefore the LIMIT CASE of perfectly evading it: `-timing` asks "what
if every delay were randomized beyond recovery?", `-geom` asks the same of mouse
kinematics. This bounds E1/E2 from data already collected -- no attack driver
and no new sessions. A real attack can only do WORSE than perfect removal, so
accuracy here is a lower bound on robustness, not an estimate.

What survives every mask is the robust core (H6). Note which groups are
client-forgeable (timing, geom, action) versus server-anchored (anything built
on TrackedAction.seq / server_ts, which a modified track.js cannot fake).

    ./venv/bin/python research/robustness.py harness_M2.csv:harness \
        modelaxis_v5.csv:model percept_M2.csv:perception
"""
import sys
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import balanced_accuracy_score
from sklearn.model_selection import StratifiedKFold, cross_val_predict

# Length proxies stay out: session length is shaped by rate limits and step
# budgets, not only behaviour (same exclusion as axis_test.py).
DROP = {"struct_n_events", "struct_duration_s", "timing_rate"}
GROUPS = ("timing_", "action_", "struct_", "geom_")

# name -> feature-group prefixes the attacker has neutralised
ATTACKS = [
    ("clean (no attack)",        ()),
    ("E1 perfect timing obfus.", ("timing_",)),
    ("E2 perfect mouse spoof",   ("geom_",)),
    ("E1+E2 client surface",     ("timing_", "geom_")),
    ("E5 DOM/action evasion",    ("action_",)),
    ("E1+E2+E5 combined",        ("timing_", "geom_", "action_")),
]
B = 200          # permutations; floor p = 1/(B+1) = 0.0050


def feats(df, killed):
    return [c for c in df.columns
            if c.startswith(GROUPS) and c not in DROP
            and not c.startswith(killed)
            and pd.api.types.is_numeric_dtype(df[c])]


def score(X, y, seed=0):
    clf = RandomForestClassifier(n_estimators=400, random_state=seed, n_jobs=-1)
    cv = StratifiedKFold(5, shuffle=True, random_state=seed)
    return balanced_accuracy_score(y, cross_val_predict(clf, X, y, cv=cv, n_jobs=1))


for arg in sys.argv[1:]:
    path, label = arg.split(":")
    df = pd.read_csv(path)
    y = df[label].values
    k = len(set(y))
    print(f"\n{'='*74}\n{path}  label={label}  n={len(y)}  classes={k}  "
          f"chance={1/k:.3f}\n{'='*74}")
    print(f"{'attack':<28}{'feats':>6}{'bal acc':>9}{'vs clean':>10}{'p':>8}")
    base = None
    for name, killed in ATTACKS:
        f = feats(df, killed)
        if not f:
            print(f"{name:<28}{0:>6}   (no features left)")
            continue
        X = df[f].fillna(0).values
        acc = score(X, y)
        if base is None:
            base = acc
        rng = np.random.default_rng(0)
        null = np.array([score(X, rng.permutation(y), seed=b) for b in range(B)])
        p = (int((null >= acc).sum()) + 1) / (B + 1)
        print(f"{name:<28}{len(f):>6}{acc:>9.3f}{acc-base:>+10.3f}{p:>8.4f}")
