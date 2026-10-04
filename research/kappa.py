"""Cohen's kappa between two labeling passes on the overlap subset (M1 6).

Raw agreement is not reportable on its own: with one class at ~40% of events,
two passes that both defaulted to it would post a high percentage while sharing
no judgement. Kappa subtracts the agreement the marginals already explain.
Landis & Koch: <.20 slight, .21-.40 fair, .41-.60 moderate, .61-.80 substantial,
>.80 almost perfect.
"""
import argparse, csv, sys
from collections import Counter, defaultdict
ap = argparse.ArgumentParser()
ap.add_argument("--a", default="hand_label_sheet.csv")
ap.add_argument("--b", default="overlap_sheet.csv")
g = ap.parse_args()

def load(p):
    with open(p, newline="", encoding="utf-8") as fh:
        return {r["sample_id"]: ((r.get("hand_label") or "").strip().lower(),
                                 r["session_id"], r["timestamp"])
                for r in csv.DictReader(fh)}

A, B = load(g.a), load(g.b)
# The overlap must be a SUBSET of the main sheet, not a fresh draw -- otherwise
# the two passes are labeling different events and kappa is meaningless.
bad = [s for s in B if s in A and A[s][1:] != B[s][1:]]
if bad:
    sys.exit(f"sample_id collision: {len(bad)} ids point to different events")
sh = [s for s in B if s in A and A[s][0] and B[s][0]]
if not sh:
    sys.exit("no overlapping labeled ids")
uncl = [s for s in sh if "unclear" in (A[s][0], B[s][0])]
pairs = [(A[s][0], B[s][0]) for s in sh if s not in uncl]
n = len(pairs)
print(f"{len(sh)} overlapping events, {len(uncl)} unclear dropped, n={n}")
obs = sum(1 for x, y in pairs if x == y) / n
ca, cb = Counter(x for x, _ in pairs), Counter(y for _, y in pairs)
exp = sum(ca[c] * cb[c] for c in set(ca) | set(cb)) / (n * n)
k = (obs - exp) / (1 - exp)
band = ("almost perfect" if k > .80 else "substantial" if k > .60 else
        "moderate" if k > .40 else "fair" if k > .20 else "slight")
print(f"observed {obs:.3f}   expected-by-chance {exp:.3f}")
print(f"Cohen's kappa {k:.3f} ({band})")
d = defaultdict(int)
for x, y in pairs:
    if x != y: d[(x, y)] += 1
for (x, y), c in sorted(d.items(), key=lambda kv: -kv[1]):
    print(f"  {x:<15} -> {y:<15} {c}")
print(f"\npass1 marginals {dict(ca)}\npass2 marginals {dict(cb)}")
