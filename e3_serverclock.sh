#!/bin/bash
# Client-trust vs server-trust attribution (Phase 2, E3 baseline).
#
# Same sessions, same certification, same features -- the ONLY difference is
# which timestamp the timing features are computed from. Client timestamps come
# from track.js and an attacker owns them; server_ts is stamped in /api/track
# and they do not. The gap between these two numbers is the cost of not
# trusting the client, measured on clean data before any attack is run.
#
# Caveat to report: server_ts is a per-BATCH receipt time (median 3 events,
# mean 6.4), so within-batch intervals are zero and only between-batch timing
# survives -- ~21 batches/session against ~135 events. Server-clock numbers are
# therefore a LOWER bound on what per-event receipt stamping would achieve.
set -a; . /etc/charweb.env; set +a
A=./venv/bin/python

for r in percept_v4 percept_v4b harness_v2 harness_v2b modelaxis_v5; do
  $A research/export_research_features.py --run-id $r --clock server \
     -o srv_$r.csv 2>&1 | grep -E "^wrote|^skipped"
done

$A - <<'PY'
import pandas as pd, csv
def health(p): return {r["session_uid"]: r for r in csv.DictReader(open(p))}
ph = health("percept_health_v2.csv"); lh = health("collection_health_v2.csv")

def ok(r, t=0.05, gate_timeouts=True):
    c = max(int(r["calls"] or 1),1); s = max(int(r["steps_used"] or 1),1)
    good = (r["model_dead"]!="1"
            and int(r["rate_limited"] or 0)/c <= t and int(r["transient"] or 0)/c <= t
            and int(r["empty"] or 0)/c <= t and int(r["stalls"] or 0)/s <= t)
    if gate_timeouts:
        good = good and int(r["action_timeouts"] or 0)/s <= t
    return good

def pair(srv, client_csv, out):
    """Keep only sessions present in BOTH clocks, so the two accuracies are
    computed on the same rows. Without this, a session the server clock drops
    would make the comparison a different-sample contrast."""
    keep = set(pd.read_csv(client_csv).session_uid)
    before = len(srv)
    srv = srv[srv.session_uid.isin(keep)]
    print(f"{out}: {len(srv)}/{before} rows paired against {client_csv}"
          f" (client has {len(keep)})")
    srv.to_csv(out, index=False)
    return srv

p = pd.concat([pd.read_csv("srv_percept_v4.csv"), pd.read_csv("srv_percept_v4b.csv")])
p = p[p.session_uid.map(lambda u: u in ph and ok(ph[u], gate_timeouts=False))]
p = pair(p, "percept_M2.csv", "percept_M2_srv.csv")
print("  perception:", dict(p.harness.value_counts()))

h = pd.concat([pd.read_csv("srv_harness_v2.csv"), pd.read_csv("srv_harness_v2b.csv")])
h = h[h.session_uid.map(lambda u: (u not in lh) or ok(lh[u]))]
h = pair(h, "harness_M2.csv", "harness_M2_srv.csv")
print("  harness:", dict(h.harness.value_counts()))

mh = {k:v for k,v in lh.items() if v["run_id"]=="modelaxis_v5"}
m = pd.read_csv("srv_modelaxis_v5.csv")
m = m[m.session_uid.map(lambda u: u in mh and ok(mh[u]))]
m = pair(m, "modelaxis_v5.csv", "modelaxis_v5_srv.csv")
print("  model:", dict(m.model.value_counts()))
PY

echo "########## SERVER-TRUST: harness 4-class"
$A research/axis_test.py harness_M2_srv.csv --label harness --perms 200
echo "########## SERVER-TRUST: model 2-class"
$A research/axis_test.py modelaxis_v5_srv.csv --label model --perms 200
echo "########## SERVER-TRUST: perception 3-class"
$A research/axis_test.py percept_M2_srv.csv --label harness --perms 200
echo "########## SERVER-TRUST: mechanism dom vs vision"
$A research/axis_test.py percept_M2_srv.csv --label harness \
    --classes percept_dom,percept_vision --perms 200
echo "########## DONE"
