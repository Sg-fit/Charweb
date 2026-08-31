"""Score the auto-labeler against the hand labels (M1 §1.2).

Reports the four numbers that actually answer "how accurate is your labeler?",
kept separate because collapsing them is how labelers get oversold:

  COVERAGE   -- fraction of events the labeler was willing to label at all
                (auto_label != 'unknown'). An abstention is not an error, and
                counting it as one understates precision; hiding it entirely
                overstates usefulness. Both numbers get reported.

  ACCURACY   -- agreement on the covered events, unweighted (per stratum) and
                population-weighted (the headline). The sample over-represents
                the weak resolution passes on purpose, so the unweighted number
                is a LOWER bound on real-world accuracy and the weighted one is
                the estimate.

  PER-CLASS  -- precision, recall and F1 for each task type, plus support.
                A single accuracy figure hides a class that is never recovered.

  PER-PASS   -- accuracy broken out by `resolved_via`. This is the diagnostic:
                if `direct` is 0.99 and `carry_forward` is 0.60, the fix is the
                carry window, not the rule table.

Rows the human marked `unclear` are excluded from all of the above and reported
as an ambiguity rate -- they measure the task, not the labeler.

    ./venv/bin/python research/score_hand_labels.py
"""
import argparse
import sys
from collections import defaultdict


def load(path):
    import csv
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet", default="hand_label_sheet.csv")
    ap.add_argument("--key", default="hand_label_key.csv")
    ap.add_argument("--out", default="labeler_validation.csv")
    args = ap.parse_args()

    sheet = {r["sample_id"]: r for r in load(args.sheet)}
    key = {r["sample_id"]: r for r in load(args.key)}

    pairs = []
    blank = 0
    unclear = 0
    for sid, k in key.items():
        h = (sheet.get(sid, {}).get("hand_label") or "").strip().lower()
        if not h:
            blank += 1
            continue
        if h == "unclear":
            unclear += 1
            continue
        pairs.append((h, k["auto_label"], k["resolved_via"],
                      float(k["weight"])))

    n = len(pairs)
    if not n:
        sys.exit("no hand labels found -- fill the `hand_label` column first")

    print(f"hand-labeled {n} of {len(key)} sampled events "
          f"({blank} blank, {unclear} marked unclear = "
          f"{unclear / max(len(key) - blank, 1):.1%} ambiguity rate)\n")

    # --- coverage --------------------------------------------------------
    cov_w = sum(w for _, a, _, w in pairs if a != "unknown")
    tot_w = sum(w for *_, w in pairs)
    cov_u = sum(1 for _, a, _, _ in pairs if a != "unknown") / n
    print(f"COVERAGE   unweighted {cov_u:.3f}   "
          f"population-weighted {cov_w / tot_w:.3f}")

    covered = [(h, a, v, w) for h, a, v, w in pairs if a != "unknown"]
    if not covered:
        sys.exit("labeler abstained on every sampled event")

    # --- accuracy on covered events --------------------------------------
    acc_u = sum(1 for h, a, _, _ in covered if h == a) / len(covered)
    acc_w = (sum(w for h, a, _, w in covered if h == a)
             / sum(w for *_, w in covered))
    print(f"ACCURACY   unweighted {acc_u:.3f}   "
          f"population-weighted {acc_w:.3f}   (n={len(covered)} covered)\n")

    # --- per-class precision / recall -------------------------------------
    classes = sorted(({h for h, *_ in pairs} | {a for _, a, _, _ in pairs})
                     - {"unknown"})
    tp = defaultdict(float); fp = defaultdict(float); fn = defaultdict(float)
    sup = defaultdict(int)
    for h, a, _, _ in pairs:
        sup[h] += 1
        if a == "unknown":
            fn[h] += 1          # abstention costs recall, never precision
            continue
        if h == a:
            tp[h] += 1
        else:
            fp[a] += 1
            fn[h] += 1

    print(f"{'class':<15}{'prec':>7}{'rec':>7}{'F1':>7}{'support':>9}")
    rows = []
    for c in classes:
        p = tp[c] / (tp[c] + fp[c]) if (tp[c] + fp[c]) else float("nan")
        r = tp[c] / (tp[c] + fn[c]) if (tp[c] + fn[c]) else float("nan")
        f = 2 * p * r / (p + r) if (p == p and r == r and p + r) else float("nan")
        print(f"{c:<15}{p:>7.3f}{r:>7.3f}{f:>7.3f}{sup[c]:>9}")
        rows.append({"metric": "class", "name": c, "precision": p,
                     "recall": r, "f1": f, "support": sup[c]})

    # --- per resolution pass ---------------------------------------------
    print(f"\n{'resolved_via':<15}{'acc':>7}{'n':>7}")
    by_via = defaultdict(list)
    for h, a, v, w in pairs:
        by_via[v].append(h == a)
    for v, hits in sorted(by_via.items(), key=lambda kv: -len(kv[1])):
        a = sum(hits) / len(hits)
        print(f"{v:<15}{a:>7.3f}{len(hits):>7}")
        rows.append({"metric": "resolved_via", "name": v, "precision": a,
                     "recall": "", "f1": "", "support": len(hits)})

    # --- confusion, non-diagonal only ------------------------------------
    conf = defaultdict(int)
    for h, a, _, _ in pairs:
        if h != a:
            conf[(h, a)] += 1
    if conf:
        print("\nmislabels (hand -> auto), most common first:")
        for (h, a), c in sorted(conf.items(), key=lambda kv: -kv[1])[:12]:
            print(f"  {h:<15} -> {a:<15} {c}")

    import csv as _csv
    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        w = _csv.DictWriter(fh, fieldnames=["metric", "name", "precision",
                                            "recall", "f1", "support"])
        w.writeheader()
        for r in rows:
            w.writerow(r)
        w.writerow({"metric": "overall", "name": "coverage_weighted",
                    "precision": cov_w / tot_w, "support": n})
        w.writerow({"metric": "overall", "name": "accuracy_weighted",
                    "precision": acc_w, "support": len(covered)})
        w.writerow({"metric": "overall", "name": "accuracy_unweighted",
                    "precision": acc_u, "support": len(covered)})
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
