import pandas as pd, numpy as np, json, ast

def parse_details(s):
    try: return json.loads(s)
    except Exception:
        try: return ast.literal_eval(s)
        except Exception: return {}

def intervals(g):
    g = g.sort_values('timestamp')
    return g['timestamp'].diff().dt.total_seconds().dropna() * 1000

def extract_features(csv_path):
    g = pd.read_csv(csv_path)
    g['timestamp'] = pd.to_datetime(g['timestamp'], format='mixed')
    g['d'] = g['details'].apply(parse_details)

    iv = intervals(g); iv = iv[(iv >= 0) & (iv < 5000)]
    kd = g[g.action_type == 'keydown']
    kdiv = intervals(kd); kdiv = kdiv[(kdiv >= 0) & (kdiv < 3000)]
    act = g['action_type'].value_counts(normalize=True) * 100
    mm = g[g.action_type == 'mousemove']
    vel = pd.to_numeric(mm['d'].apply(lambda x: x.get('velocity')), errors='coerce').dropna()
    vel = vel[(vel >= 0) & (vel < 8000)]

    return pd.DataFrame([{
        'iv_mean': iv.mean(), 'iv_cv': iv.std()/iv.mean() if iv.mean() else np.nan,
        'kd_mean': kdiv.mean(), 'kd_cv': kdiv.std()/kdiv.mean() if kdiv.mean() else np.nan,
        'click_pct': act.get('click', 0), 'keydown_pct': act.get('keydown', 0),
        'mousemove_pct': act.get('mousemove', 0), 'scroll_pct': act.get('scroll', 0),
        'vel_mean': vel.mean()
    }])

import joblib

model = joblib.load('ai_detector_rf_v2.pkl')
features = extract_features('some_new_session.csv')

pred = model.predict(features)[0]          # 0 = human, 1 = ai
prob = model.predict_proba(features)[0][1]  # P(ai), 0.0–1.0

print(f"Verdict: {'AI' if pred == 1 else 'Human'}  (P(ai) = {prob:.3f})")