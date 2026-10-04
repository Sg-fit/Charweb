#!/bin/bash
# E3 result: does attribution survive a hostile client?
#
# Same sessions under both clocks, split by adversarial condition. The three
# columns answer three different questions:
#   client / all    -- what Phase 1 measured. Assumes honest telemetry.
#   server / all    -- timestamps hardened, event CONTENT still client-supplied.
#   server / timing -- fully tamper-resistant: every feature derives from when
#                      the server received a request.
set -a; . /etc/charweb.env; set +a
A=./venv/bin/python
$A research/export_research_features.py --run-id e3_grid_scripted --clock client \
   -o e3s_client.csv 2>&1 | grep -E "^wrote|^skipped"
$A research/export_research_features.py --run-id e3_grid_scripted --clock server \
   -o e3s_server.csv 2>&1 | grep -E "^wrote|^skipped"

$A - <<'PY'
import numpy as np, pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import balanced_accuracy_score
from sklearn.model_selection import StratifiedKFold, cross_val_predict

DROP={"struct_n_events","struct_duration_s","timing_rate"}
PRE=("timing_","action_","struct_","geom_")
B=200

def feats(df, only=None):
    return [c for c in df.columns
            if c.startswith(only or PRE) and c not in DROP
            and pd.api.types.is_numeric_dtype(df[c])]

def score(X,y,seed=0):
    clf=RandomForestClassifier(n_estimators=400,random_state=seed,n_jobs=-1)
    cv=StratifiedKFold(5,shuffle=True,random_state=seed)
    return balanced_accuracy_score(y,cross_val_predict(clf,X,y,cv=cv,n_jobs=1))

def run(df,only=None):
    f=feats(df,only)
    if not f: return None,None,0
    X=df[f].fillna(0).values; y=df["harness"].values
    acc=score(X,y)
    rng=np.random.default_rng(0)
    null=np.array([score(X,rng.permutation(y),seed=b) for b in range(B)])
    return acc,(int((null>=acc).sum())+1)/(B+1),len(f)

cl=pd.read_csv("e3s_client.csv"); sv=pd.read_csv("e3s_server.csv")
conds=sorted(cl.adversarial_condition.unique())
print(f"\n{'condition':<24}{'n':>4}{'client/all':>12}{'server/all':>12}"
      f"{'server/timing':>15}   chance")
for c in conds:
    a=cl[cl.adversarial_condition==c]; b=sv[sv.adversarial_condition==c]
    k=a.harness.nunique()
    r1=run(a); r2=run(b); r3=run(b,("timing_",))
    print(f"{c:<24}{len(a):>4}{r1[0]:>12.3f}{r2[0]:>12.3f}{r3[0]:>15.3f}"
          f"   {1/k:.3f}")
    print(f"{'':<24}{'':>4}{'p='+format(r1[1],'.4f'):>12}"
          f"{'p='+format(r2[1],'.4f'):>12}{'p='+format(r3[1],'.4f'):>15}")
PY
