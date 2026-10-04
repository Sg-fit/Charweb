#!/bin/bash
# E3 on the MODEL axis -- the informative test.
#
# The scripted harness axis was already 1.000 clean, so an attack there has no
# headroom to reveal. The model axis sits at 0.895 (client) / 0.766 (fully
# hardened), so degradation is measurable. Also re-runs the HARDEST scripted
# contrast (plain vs noisy, the two closest profiles) for the same reason.
set -a; . /etc/charweb.env; set +a
A=./venv/bin/python
$A research/export_research_features.py --run-id e3_grid_llm --clock client \
   -o e3m_client_raw.csv 2>&1 | grep -E "^wrote|^skipped"
$A research/export_research_features.py --run-id e3_grid_llm --clock server \
   -o e3m_server_raw.csv 2>&1 | grep -E "^wrote|^skipped"

$A -u - <<'PY'
import csv, os, numpy as np, pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import balanced_accuracy_score, recall_score
from sklearn.model_selection import StratifiedKFold, cross_val_predict

DROP={"struct_n_events","struct_duration_s","timing_rate"}
PRE=("timing_","action_","struct_","geom_"); B=200

# Same 5% uniform-rate certification as m2_model_final.sh. A session degraded by
# rate limiting or stalls is not evidence about the attack.
H="collection_health_fixed.csv"
health={}
if os.path.exists(H):
    health={r["session_uid"]:r for r in csv.DictReader(open(H))
            if r.get("run_id")=="e3_grid_llm"}
# collection_health.csv predates the action_timeouts column and appends never
# rewrite a header, so that gate may be unavailable here. Degrade explicitly
# rather than KeyError -- and SAY SO, because a silently weaker certification
# is exactly how the earlier 65-timeout sessions were certified "clean".
HAS_TIMEOUTS = bool(health) and "action_timeouts" in next(iter(health.values()))
if health and not HAS_TIMEOUTS:
    print("!! collection_health.csv has no action_timeouts column -- "
          "certifying WITHOUT the timeout gate (weaker than M2)")

def ok(r,t=0.05):
    c=max(int(r["calls"] or 1),1); s=max(int(r["steps_used"] or 1),1)
    good=(r["model_dead"]!="1"
        and int(r["rate_limited"] or 0)/c<=t and int(r["transient"] or 0)/c<=t
        and int(r["empty"] or 0)/c<=t and int(r["stalls"] or 0)/s<=t)
    if HAS_TIMEOUTS:
        good = good and int(r.get("action_timeouts") or 0)/s<=t
    return good

def feats(df,only=None):
    return [c for c in df.columns if c.startswith(only or PRE) and c not in DROP
            and pd.api.types.is_numeric_dtype(df[c])]
def score(X,y,seed=0):
    clf=RandomForestClassifier(n_estimators=400,random_state=seed,n_jobs=-1)
    return balanced_accuracy_score(y,cross_val_predict(
        clf,X,y,cv=StratifiedKFold(5,shuffle=True,random_state=seed),n_jobs=1))
def run(df,label,only=None):
    f=feats(df,only)
    X=df[f].fillna(0).values; y=df[label].values
    if len(set(y))<2: return None
    acc=score(X,y); rng=np.random.default_rng(0)
    null=np.array([score(X,rng.permutation(y),seed=b) for b in range(B)])
    return acc,(int((null>=acc).sum())+1)/(B+1)

def table(cl,sv,label,title):
    print(f"\n{'='*76}\n{title}\n{'='*76}")
    print(f"{'condition':<24}{'n':>4}{'client/all':>12}{'server/all':>12}"
          f"{'server/timing':>15}")
    for c in sorted(cl.adversarial_condition.unique()):
        a=cl[cl.adversarial_condition==c]; b=sv[sv.adversarial_condition==c]
        if a[label].nunique()<2: continue
        r1=run(a,label); r2=run(b,label); r3=run(b,label,("timing_",))
        print(f"{c:<24}{len(a):>4}{r1[0]:>12.3f}{r2[0]:>12.3f}{r3[0]:>15.3f}")
        print(f"{'':<28}{'p='+format(r1[1],'.4f'):>12}"
              f"{'p='+format(r2[1],'.4f'):>12}{'p='+format(r3[1],'.4f'):>15}")

cl=pd.read_csv("e3m_client_raw.csv"); sv=pd.read_csv("e3m_server_raw.csv")
if health:
    keep=lambda d: d[d.session_uid.map(lambda u: u in health and ok(health[u]))]
    before=len(cl); cl=keep(cl); sv=keep(sv)
    print(f"health-certified {len(cl)}/{before} sessions")
else:
    print("!! no health rows for e3_grid_llm -- UNCERTIFIED, treat as provisional")
cl.to_csv("e3m_client.csv",index=False); sv.to_csv("e3m_server.csv",index=False)
print(cl.groupby(["model","adversarial_condition"]).size().to_string())
table(cl,sv,"model","MODEL AXIS under attack (2 classes, chance 0.500)")

# Hardest scripted contrast: the two profiles that differ least.
s_cl=pd.read_csv("e3s_client.csv"); s_sv=pd.read_csv("e3s_server.csv")
m=lambda d: d[d.harness.isin(["scripted_plain","scripted_noisy"])]
table(m(s_cl),m(s_sv),"harness",
      "HARDEST HARNESS CONTRAST plain vs noisy (2 classes, chance 0.500)")
PY
