"""Tests for features_core.py. Run: python research/test_features_core.py

Guards the two things that must not regress:
  1. importing the feature code never pulls in the Flask app (that import is what
     recreated logs/Charweb.log as root:root and crash-looped the service);
  2. the feature definitions the certified tables were built with are unchanged.
"""
import collections
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import features_core as fc  # noqa: E402

assert "app" not in sys.modules and "flask" not in sys.modules, \
    "features_core must not import the Flask app"

Row = collections.namedtuple("Row", "timestamp server_ts action_type url target details seq")
T0 = datetime(2026, 8, 1, 12, 0, 0)


def stream(n, step_ms=100, server_lag_ms=500, kinds=("pageload", "mousemove", "click", "keydown")):
    rows = []
    for i in range(n):
        t = T0 + timedelta(milliseconds=i * step_ms)
        kind = "pageload" if i == 0 else kinds[1 + (i % (len(kinds) - 1))]
        det = '{"velocity": %d}' % (50 + i) if kind == "mousemove" else "{}"
        rows.append(Row(t, t + timedelta(milliseconds=server_lag_ms), kind,
                        "https://charweb.net/explore", None, det, i))
    return rows


# 29 features in four groups: 12 + 8 + 5 + 4
assert len(fc.FEATURES) == 29, len(fc.FEATURES)
assert (len(fc.TIMING), len(fc.ACTION), len(fc.STRUCT), len(fc.GEOM)) == (12, 8, 5, 4)
assert len(set(fc.FEATURES)) == 29, "duplicate feature names"

# too short -> None, never a half-filled dict
assert fc.featurise(stream(4)) is None

f = fc.featurise(stream(40))
assert f is not None and set(f) == set(fc.FEATURES), set(f) ^ set(fc.FEATURES)

# a row with no usable clock must refuse rather than emit garbage intervals
bad = stream(10)
bad[3] = bad[3]._replace(server_ts=None)
assert fc.featurise(bad, clock=lambda r: r.server_ts) is None
assert fc.featurise(bad) is not None            # client clock still fine

# the two clocks agree on intervals when the server lag is constant, and
# differ on absolute duration only through the lag (here: none, constant lag)
a = fc.featurise(stream(40), clock=lambda r: r.timestamp)
b = fc.featurise(stream(40), clock=lambda r: r.server_ts)
assert abs(a["timing_iv_mean"] - b["timing_iv_mean"]) < 1e-9

# deterministic
assert fc.featurise(stream(40)) == fc.featurise(stream(40))
print("features_core: all tests passed")
