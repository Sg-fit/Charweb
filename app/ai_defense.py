"""AI defense system: user-agent signature checks plus behavioral scoring.

Two independent layers, both feeding into UserSession:
  1. classify_user_agent  - cheap rule-based check against known bot/automation UAs.
  2. score_session        - runs the trained RandomForest (models/RF_ai_detector_v2.joblib)
                             over the behavioral features of a session's TrackedAction rows.
"""
import json
import os
import statistics

import sqlalchemy as sa

from app import app, db
from app.models import TrackedAction

# Model files are named by algorithm: RF_... for RandomForest, LR_... for
# LogisticRegression. Swap this path to switch which trained model is used.
MODEL_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'models', 'RF_ai_detector_v2.joblib')

# Substrings seen in User-Agent strings from headless browsers, scripting
# libraries, and crawlers. Matched case-insensitively.
BOT_UA_SIGNATURES = (
    'bot', 'crawl', 'spider', 'slurp', 'headlesschrome', 'phantomjs',
    'python-requests', 'python-urllib', 'curl', 'wget', 'playwright',
    'puppeteer', 'selenium', 'scrapy', 'okhttp', 'go-http-client', 'axios/',
    'libwww-perl', 'java/',
)

_model = None
_model_load_failed = False


def classify_user_agent(ua_string):
    """Rule-based first line of defense. Returns (is_bot, reason_or_None)."""
    if not ua_string or not ua_string.strip():
        return True, 'empty user-agent'
    lowered = ua_string.lower()
    for signature in BOT_UA_SIGNATURES:
        if signature in lowered:
            return True, f'matched signature "{signature}"'
    return False, None


def _get_model():
    global _model, _model_load_failed
    if _model is not None or _model_load_failed:
        return _model
    try:
        import joblib
        _model = joblib.load(MODEL_PATH)
    except Exception as exc:
        app.logger.warning('AI defense: could not load detector model (%s): %s', MODEL_PATH, exc)
        _model_load_failed = True
        _model = None
    return _model


def _pct(count, total):
    return (count / total * 100.0) if total else 0.0


def _mean_cv(deltas):
    """Mean and coefficient of variation (stdev / mean) of a list of intervals."""
    if len(deltas) < 2:
        return 0.0, 0.0
    mean = statistics.fmean(deltas)
    if mean == 0:
        return mean, 0.0
    return mean, statistics.pstdev(deltas) / mean


def features_from_rows(rows):
    """Build the 9-column feature row the model was trained on
    (iv_mean, iv_cv, kd_mean, kd_cv, click_pct, keydown_pct, mousemove_pct,
    scroll_pct, vel_mean) from an ordered list of row-like objects, each
    exposing .timestamp (datetime), .action_type (str), .details (JSON
    string or None) -- i.e. TrackedAction rows, or anything shaped like one.

    Split out from compute_session_features() so research/build_features.py
    can compute this exact feature set offline (from a CSV export, without a
    live DB) without copy-pasting the logic -- see that script's own
    docstring on why train/serve drift is the bug class to avoid here.
    """
    if len(rows) < 5:
        return None

    timestamps = [r.timestamp for r in rows]
    intervals = [
        (timestamps[i] - timestamps[i - 1]).total_seconds() * 1000.0
        for i in range(1, len(timestamps))
    ]
    keydown_ts = [r.timestamp for r in rows if r.action_type == 'keydown']
    kd_intervals = [
        (keydown_ts[i] - keydown_ts[i - 1]).total_seconds() * 1000.0
        for i in range(1, len(keydown_ts))
    ]

    velocities = []
    for r in rows:
        if r.action_type == 'mousemove' and r.details:
            try:
                v = json.loads(r.details).get('velocity')
            except (ValueError, TypeError):
                v = None
            if isinstance(v, (int, float)):
                velocities.append(v)

    total = len(rows)
    iv_mean, iv_cv = _mean_cv(intervals)
    kd_mean, kd_cv = _mean_cv(kd_intervals)

    return {
        'iv_mean': iv_mean,
        'iv_cv': iv_cv,
        'kd_mean': kd_mean,
        'kd_cv': kd_cv,
        'click_pct': _pct(sum(1 for r in rows if r.action_type == 'click'), total),
        'keydown_pct': _pct(len(keydown_ts), total),
        'mousemove_pct': _pct(sum(1 for r in rows if r.action_type == 'mousemove'), total),
        'scroll_pct': _pct(sum(1 for r in rows if r.action_type == 'scroll'), total),
        'vel_mean': statistics.fmean(velocities) if velocities else 0.0,
    }


def compute_session_features(session_uid):
    """features_from_rows(), fed from this session's live TrackedAction rows."""
    rows = db.session.scalars(
        sa.select(TrackedAction)
        .where(TrackedAction.session_uid == session_uid)
        .order_by(TrackedAction.timestamp.asc())
    ).all()
    return features_from_rows(rows)


def score_session(session_uid):
    """Run the trained RandomForest over a session's behavioral features.
    Returns (prediction, probability) where prediction is 'human'/'ai',
    or (None, None) if there isn't enough data yet or the model is unavailable."""
    model = _get_model()
    if model is None:
        return None, None
    features = compute_session_features(session_uid)
    if features is None:
        return None, None
    try:
        import pandas as pd
        row = pd.DataFrame([features])
        pred = model.predict(row)[0]
        prob = model.predict_proba(row)[0][1]
        return ('ai' if pred == 1 else 'human'), float(prob)
    except Exception as exc:
        app.logger.warning('AI defense: scoring failed for session %s: %s', session_uid, exc)
        return None, None
