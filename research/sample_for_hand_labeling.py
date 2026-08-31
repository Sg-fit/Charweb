"""Draw a blind, reweightable sample of events for hand labeling (M1 §1.2).

The labeler has never been checked against a human. `validate_carry.py` only
asks whether the carry-forward *fill* recovers the anchor's label -- it treats
the anchor rules themselves as ground truth. So the one number a reviewer
always asks for ("how accurate is your labeler?") is currently asserted, not
measured. This script produces the sheet that measures it.

Three design decisions worth defending on a poster:

1. **The sheet is blind.** The auto-label is written to a separate key file and
   never appears in the sheet you fill in. Showing it would turn the exercise
   into agreement-with-the-machine rather than an independent judgement, and
   the resulting precision would be meaningless.

2. **Stratified by `resolved_via`, with weights.** A purely proportional sample
   would be ~90% `direct`/`url` events -- the easy cases -- and would contain
   too few `carry_forward` / `carry_back` events to say anything about the
   passes most likely to be wrong. So each resolution pass gets a floor
   (--min-per-stratum). That over-samples the weak passes, which would bias a
   headline accuracy upward or downward depending on which way they err, so
   every row carries an inverse-probability `weight` and the scorer reports
   BOTH the unweighted per-stratum numbers and a population-weighted headline.

3. **Context columns.** A bare (action_type, target) pair is often not
   hand-labelable -- `click` on `submit` could be any task. Each row carries
   the two events before and after it, so the human sees what the agent was
   doing around that moment, exactly as the carry-forward pass does.

Usage on the server:

    cd /srv/charweb; set -a; . /etc/charweb.env; set +a
    ./venv/bin/python research/sample_for_hand_labeling.py -n 200
    # -> hand_label_sheet.csv   (fill the `hand_label` column, blind)
    #    hand_label_key.csv     (do not open until the sheet is finished)
"""
import argparse
import csv
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd

import task_labeling as TL

# The vocabulary the hand-labeler may use. `unclear` is deliberately offered:
# forcing a human to guess on a genuinely ambiguous event manufactures
# disagreement that says nothing about the labeler. Those rows are excluded
# from precision/recall and reported as an ambiguity rate instead.
VOCAB = ["feed_browse", "search", "profile_edit", "timed_dungeon",
         "signup_login", "chat", "unknown", "unclear"]


def context(rows, i, k=2):
    """The k events on each side of row i, as compact strings."""
    def fmt(j):
        if j < 0 or j >= len(rows):
            return ""
        r = rows[j]
        return f"{r['action_type']}:{r['target'] or '-'}@{r['url'] or '-'}"
    before = " | ".join(fmt(j) for j in range(i - k, i))
    after = " | ".join(fmt(j) for j in range(i + 1, i + 1 + k))
    return before, after


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=200,
                    help="total events to sample")
    ap.add_argument("--min-per-stratum", type=int, default=20,
                    help="floor per resolved_via value, so the weak passes "
                         "are actually measurable")
    ap.add_argument("--csv", default=None,
                    help="label a CSV export instead of the live DB")
    ap.add_argument("--sheet", default="hand_label_sheet.csv")
    ap.add_argument("--key", default="hand_label_key.csv")
    ap.add_argument("--seed", type=int, default=20260831)
    args = ap.parse_args()

    if args.csv:
        events, _ = TL.label_from_csv(args.csv)
    else:
        events, _ = TL.label_from_db()

    if events.empty:
        sys.exit("no events -- is DATABASE_URL set?")

    events = events.sort_values(["session_id", "timestamp"]).reset_index(drop=True)
    events["row_id"] = events.index
    rows = events.to_dict("records")

    pop = len(events)
    counts = events["resolved_via"].value_counts().to_dict()
    print(f"population: {pop} events across "
          f"{events['session_id'].nunique()} sessions")
    for k, v in counts.items():
        print(f"  {k:<14} {v:>6}  ({v / pop:.1%})")

    # --- allocate the sample across strata -------------------------------
    strata = list(counts)
    alloc = {}
    for s in strata:
        proportional = round(args.n * counts[s] / pop)
        alloc[s] = min(counts[s], max(proportional, min(args.min_per_stratum,
                                                        counts[s])))
    # If the floors pushed the total over n, trim the largest strata first --
    # they are the ones a few extra rows help least.
    while sum(alloc.values()) > args.n:
        s = max(alloc, key=lambda k: alloc[k] - min(args.min_per_stratum,
                                                    counts[k]))
        if alloc[s] <= min(args.min_per_stratum, counts[s]):
            break
        alloc[s] -= 1

    rng = random.Random(args.seed)
    picked = []
    for s in strata:
        pool = events.index[events["resolved_via"] == s].tolist()
        take = rng.sample(pool, alloc[s])
        # Inverse sampling probability: one sampled row stands for this many
        # events in the population. The scorer uses it for the headline.
        w = counts[s] / alloc[s] if alloc[s] else 0.0
        picked.extend((i, s, w) for i in take)

    rng.shuffle(picked)   # so strata are not visible from row order

    sheet_cols = ["sample_id", "session_id", "timestamp", "action_type",
                  "target", "url", "before", "after", "hand_label"]
    with open(args.sheet, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(sheet_cols)
        for n, (i, s, wt) in enumerate(picked, 1):
            r = rows[i]
            b, a = context(rows, i)
            w.writerow([n, r["session_id"], r["timestamp"], r["action_type"],
                        r["target"], r["url"], b, a, ""])

    with open(args.key, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["sample_id", "auto_label", "resolved_via", "weight",
                    "labeling_regime", "row_id"])
        for n, (i, s, wt) in enumerate(picked, 1):
            r = rows[i]
            w.writerow([n, r["task_type"], s, f"{wt:.4f}",
                        r["labeling_regime"], r["row_id"]])

    print(f"\nsampled {len(picked)} events")
    for s in strata:
        print(f"  {s:<14} {alloc[s]:>4}  weight {counts[s] / alloc[s]:.2f}"
              if alloc[s] else f"  {s:<14}    0")
    print(f"\nsheet -> {args.sheet}   (fill `hand_label`, one of: "
          f"{', '.join(VOCAB)})")
    print(f"key   -> {args.key}      (do NOT open until the sheet is done)")


if __name__ == "__main__":
    main()
