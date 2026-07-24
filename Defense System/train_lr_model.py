"""
Train a LogisticRegression version of the AI-vs-human session classifier.

IMPORTANT: There is no labeled training dataset in this repo (no CSV of the
9 behavioral features with known human/ai labels). The data below is
SYNTHETIC PLACEHOLDER data generated to match the same intuition the
RandomForest model uses (humans: variable timing, higher CV; bots: uniform,
low-CV timing) purely so this script runs end-to-end and produces a real
model file. Replace `build_training_data()` with real feature rows pulled
from TrackedAction sessions (see app/ai_defense.py:compute_session_features)
paired with actual known human/ai labels before relying on this model.

Requirements: pip install scikit-learn joblib pandas numpy
"""
import numpy as np
import pandas as pd
import joblib
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import classification_report

FEATURE_COLUMNS = [
    'iv_mean', 'iv_cv', 'kd_mean', 'kd_cv', 'click_pct',
    'keydown_pct', 'mousemove_pct', 'scroll_pct', 'vel_mean',
]


def build_training_data(n_per_class=400, seed=42):
    """SYNTHETIC placeholder data -- see module docstring."""
    rng = np.random.default_rng(seed)

    # Humans: irregular timing (high coefficient of variation), a mix of
    # interaction types, moderate/variable mouse velocity.
    human = pd.DataFrame({
        'iv_mean': rng.normal(350, 80, n_per_class).clip(50),
        'iv_cv': rng.normal(0.9, 0.2, n_per_class).clip(0.2),
        'kd_mean': rng.normal(220, 60, n_per_class).clip(30),
        'kd_cv': rng.normal(0.8, 0.2, n_per_class).clip(0.2),
        'click_pct': rng.normal(8, 3, n_per_class).clip(0),
        'keydown_pct': rng.normal(35, 10, n_per_class).clip(0),
        'mousemove_pct': rng.normal(45, 12, n_per_class).clip(0),
        'scroll_pct': rng.normal(12, 5, n_per_class).clip(0),
        'vel_mean': rng.normal(450, 150, n_per_class).clip(10),
    })
    human['label'] = 0  # human

    # Bots: uniform/robotic timing (low CV), keydown-heavy, low mouse activity.
    bot = pd.DataFrame({
        'iv_mean': rng.normal(300, 40, n_per_class).clip(10),
        'iv_cv': rng.normal(0.15, 0.08, n_per_class).clip(0.01),
        'kd_mean': rng.normal(200, 20, n_per_class).clip(10),
        'kd_cv': rng.normal(0.1, 0.05, n_per_class).clip(0.01),
        'click_pct': rng.normal(3, 2, n_per_class).clip(0),
        'keydown_pct': rng.normal(80, 8, n_per_class).clip(0),
        'mousemove_pct': rng.normal(10, 5, n_per_class).clip(0),
        'scroll_pct': rng.normal(4, 2, n_per_class).clip(0),
        'vel_mean': rng.normal(600, 60, n_per_class).clip(10),
    })
    bot['label'] = 1  # ai/bot

    data = pd.concat([human, bot], ignore_index=True)
    return data.sample(frac=1, random_state=seed).reset_index(drop=True)


def train():
    data = build_training_data()
    X = data[FEATURE_COLUMNS]
    y = data['label']

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, stratify=y, random_state=42)

    model = Pipeline([
        ('scaler', StandardScaler()),
        ('clf', LogisticRegression(max_iter=1000)),
    ])
    model.fit(X_train, y_train)

    print("Holdout performance (on synthetic data):")
    print(classification_report(y_test, model.predict(X_test), target_names=['human', 'ai']))

    out_path = '../models/LR_ai_detector_v1.joblib'
    joblib.dump(model, out_path)
    print(f'Saved to {out_path}')
    return model


if __name__ == '__main__':
    train()
