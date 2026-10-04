#!/bin/bash
set -a; . /etc/charweb.env; set +a
A=./venv/bin/python
for r in percept_v4 percept_v4b harness_v2 harness_v2b; do
  $A research/export_research_features.py --run-id $r --clock client \
     -o raw_$r.csv 2>&1 | grep -E "^wrote|^skipped"
done
$A - <<'PY'
import pandas as pd, csv
def health(path):
    return {r["session_uid"]: r for r in csv.DictReader(open(path))}
ph = health("percept_health_v2.csv"); lh = health("collection_health_v2.csv")
def ok(r, t=0.05, gate_timeouts=True):
    c = max(int(r["calls"] or 1),1); s = max(int(r["steps_used"] or 1),1)
    good = (r["model_dead"]!="1"
            and int(r["rate_limited"] or 0)/c <= t and int(r["transient"] or 0)/c <= t
            and int(r["empty"] or 0)/c <= t and int(r["stalls"] or 0)/s <= t)
    if gate_timeouts:
        good = good and int(r["action_timeouts"] or 0)/s <= t
    return good

# perception: timeouts are the architecture under test, so recorded not gated
p = pd.concat([pd.read_csv("raw_percept_v4.csv"), pd.read_csv("raw_percept_v4b.csv")])
p = p[p.session_uid.map(lambda u: u in ph and ok(ph[u], gate_timeouts=False))]
p.to_csv("percept_M2.csv", index=False); print("perception:", dict(p.harness.value_counts()))

# harness: scripted arms write no health row -> keep them; gate the llm arm
h = pd.concat([pd.read_csv("raw_harness_v2.csv"), pd.read_csv("raw_harness_v2b.csv")])
h = h[h.session_uid.map(lambda u: (u not in lh) or ok(lh[u]))]
h.to_csv("harness_M2.csv", index=False); print("harness:", dict(h.harness.value_counts()))
PY
echo "########## perception 3-class"
$A research/axis_test.py percept_M2.csv --label harness --perms 200
echo "########## mechanism dom vs vision"
$A research/axis_test.py percept_M2.csv --label harness --classes percept_dom,percept_vision --perms 200
echo "########## harness 4-class"
$A research/axis_test.py harness_M2.csv --label harness --perms 200
echo "########## harness LOTO"
$A research/m3_loto.py harness_M2.csv
echo "########## DONE"
