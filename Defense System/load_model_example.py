"""
How to use RF_ai_detector_v2.joblib / .pkl

This file is a saved scikit-learn Pipeline object (imputer + RandomForestClassifier).
It is NOT meant to be opened by double-clicking — it must be loaded inside Python.

Model files in ../models are named by algorithm: RF_... for RandomForest,
LR_... for LogisticRegression. Point this at whichever one you want to try.

Requirements: pip install scikit-learn joblib pandas
"""
import joblib
import pandas as pd

# 1. Load the model (kept in ../models alongside other model versions)
model = joblib.load('../models/RF_ai_detector_v2.joblib')  # or .pkl, same content

# 2. Build a feature row for a new session (same 9 columns used in training)
# iv_mean, iv_cv, kd_mean, kd_cv, click_pct, keydown_pct, mousemove_pct, scroll_pct, vel_mean
new_session = pd.DataFrame([{
    'iv_mean': 300.0,
    'iv_cv': 1.8,
    'kd_mean': 220.0,
    'kd_cv': 1.4,
    'click_pct': 2.0,
    'keydown_pct': 85.0,
    'mousemove_pct': 10.0,
    'scroll_pct': 3.0,
    'vel_mean': 500.0,
}])

# 3. Predict
pred = model.predict(new_session)          # 0 = human, 1 = ai
prob = model.predict_proba(new_session)    # [P(human), P(ai)]

print('Prediction:', 'AI' if pred[0] == 1 else 'Human')
print('P(ai) =', prob[0][1])
