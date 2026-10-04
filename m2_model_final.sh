#!/bin/bash
set -a; . /etc/charweb.env; set +a
A=./venv/bin/python
$A research/export_research_features.py --run-id modelaxis_v5 --clock client \
   -o modelaxis_v5_raw.csv 2>&1 | grep -v Warning
$A - <<'PY'
import pandas as pd, csv
h = {r["session_uid"]: r for r in csv.DictReader(open("collection_health_v2.csv"))
     if r["run_id"] == "modelaxis_v5"}
df = pd.read_csv("modelaxis_v5_raw.csv")
def rate_ok(r, t):
    c = max(int(r["calls"] or 1),1); s = max(int(r["steps_used"] or 1),1)
    return (r["model_dead"]!="1"
            and int(r["rate_limited"] or 0)/c <= t and int(r["transient"] or 0)/c <= t
            and int(r["empty"] or 0)/c <= t and int(r["stalls"] or 0)/s <= t
            and int(r["action_timeouts"] or 0)/s <= t)
for name, t in (("modelaxis_v5.csv",0.05), ("modelaxis_v5_strict.csv",0.0)):
    keep = df[df.session_uid.map(lambda u: u in h and rate_ok(h[u], t))]
    keep.to_csv(name, index=False)
    print(name, dict(keep.model.value_counts()))
print(pd.read_csv("modelaxis_v5.csv").groupby(["model","instruction_condition"]).size())
PY
echo "########## 5% rule"
$A research/axis_test.py modelaxis_v5.csv --label model --perms 200
echo "########## strict"
$A research/axis_test.py modelaxis_v5_strict.csv --label model --perms 200
echo "########## LOTO"
$A research/m3_loto.py modelaxis_v5.csv
echo "########## DONE"
