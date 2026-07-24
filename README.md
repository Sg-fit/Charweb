---
layout: default
title: Charweb Research
---

# Charweb AI-Agent Detection Research

Charweb is a small social/gaming web app (Flask) that doubles as a live testbed for
a research project on **detecting AI browser agents from behavioral and
request-level signals** — comparing multiple agent architectures against real
human users across a shared set of tasks.

This document summarizes the defense system built into the app, the agent
architectures and data collected against it, the task-labeling pipeline used to
prepare that data for analysis, the experimental design driving the research
questions, and the concrete findings so far.

---

## 1. System overview

Charweb tracks every user's clicks, keystrokes, scrolls, and mouse movement
client-side (`app/static/track.js`) and ships them to `/api/track`, where they're
stored per-session (`TrackedAction`, `UserSession` in `app/models.py`). On top of
that raw telemetry sits a two-layer defense system (`app/ai_defense.py`):

1. **Rule-based User-Agent check** — flags known bot/automation signatures
   (`HeadlessChrome`, `python-requests`, `curl`, Playwright, Selenium, empty UA,
   etc.) with essentially zero false-positive risk against real browsers.
2. **Behavioral ML scoring** — computes 9 session-level features (inter-event
   timing mean/CV, keydown timing mean/CV, and the click/keydown/mousemove/scroll
   activity mix + mean mouse velocity) from `TrackedAction` history and scores the
   session with a trained classifier.

Both signals are currently **observational only** — they populate `UserSession`
(`ua_bot_flag`, `ai_prediction`, `ai_probability`) and surface in the
`/admin/tracking` dashboard, but nothing blocks or rate-limits a flagged session
yet. That's a deliberate choice while the model is still being validated (see
§5) rather than a gap.

### Model versioning

Trained models live in `models/`, named by algorithm:

| File | Algorithm | Status |
|---|---|---|
| `RF_ai_detector_v2.joblib` / `.pkl` | RandomForest | trained on real collected session data; live in `app/ai_defense.py` |
| `LR_ai_detector_v1.joblib` | LogisticRegression | **trained on synthetic placeholder data** — pipeline demo only, not production-ready |

`Defense System/` holds the load/save/train reference scripts
(`load_model_example.py`, `save_model_example.py`, `train_lr_model.py`).

---

## 2. Agent architectures & data collected

| Architecture | Description | Levels/variants |
|---|---|---|
| Playwright scripts (`app/gptTest.py`, `gemini.py`, `copilat.py`, `grok.py`) | Scripted browser automation with tunable human-likeness | L1 `naive_bot` → L4 `highly_humanlike` (typing delay, mouse-path noise, typo rate, idle probability) |
| LLM-driven agent (`fenris`) | External agent architecture, less scripted | — |
| Human baseline | Real users completing the same task set | — |

Each Playwright script drives the full mission set — register, login, compose a
post, search, browse/scroll the feed, edit profile, read terms, a timed
register+post mission, an idle-then-resume post, a logout/forgot-password/re-login
flow, and the daily hub (sign-in, character creation, dungeon actions, shop,
equip, chat, rankings, attribute allocation) — so every session spans multiple
task types in one continuous run.

---

## 3. Task-type taxonomy & auto-labeling

Five task categories anchor the study, plus one added during labeling work:

- `signup_login` — registration and login forms
- `search` — the search box
- `feed_browse` — composing/scrolling/reading the home feed
- `profile_edit` — editing about-me
- `timed_dungeon` — the timed mission / daily dungeon hub (sign-in, character
  creation, dungeon actions, shop, equip)
- `chat` *(added)* — the chat panel; not part of the original five, but has
  stable identifiable elements in the data and was otherwise silently absorbed
  into whichever task preceded it

**`research/task_labeling.py`** auto-labels every tracked event with its task
type and groups contiguous same-task runs into **episodes** — the actual
analysis unit (a session can span several task episodes, so episode-level
labeling fits the study design better than one label per session).

Labeling strategy, in short:
1. **Direct match** — events whose `target` field uniquely identifies a task
   (`about_me` → profile_edit, `q` → search, `dungeon-explore-btn` →
   timed_dungeon, ...) are labeled directly.
2. **Carry-forward** — ambiguous/generic targets (`submit`, bare tag names like
   `BUTTON`/`DIV`, mousemove/scroll events with no target) inherit the label of
   the closest preceding direct match within a 60-second window.
3. Anything still unresolved is tagged `unknown` rather than guessed.

The script runs against **either** the live Charweb database **or** exported
CSVs (`--csv ai_raw_combined.csv`), sharing one labeling engine so the two paths
can't silently drift apart in behavior. It also reports a resolution
(direct/carry-forward/unknown) breakdown per session owner — the closest proxy
available to "per architecture," since the schema has no explicit `architecture`
field today.

---

## 4. Experimental design

**RQ1 (cross-architecture transfer):** Does a classifier trained on one agent
architecture detect unseen architectures? — leave-one-architecture-out training,
measuring recall/AUC specifically on the held-out architecture.

**RQ2 (task-type breakdown):** Which task/interaction type carries the
strongest and weakest discriminative signal? — per-task-type AUC plus effect
sizes (Mann-Whitney U, Cliff's delta) on top features.

**RQ3 (adversarial co-evolution):** How quickly does detection decay as an
adversary iterates against the classifier? — Gen 0 baseline agent → jittered/
humanized Gen 1 → optional classifier retrain (Gen 2), tracking AUC across
generations for both a frozen and a retrained classifier.

Planned unifying figure: a 3-panel result — (A) architecture × task-type AUC
heatmap, (B) per-task feature importance, (C) adversarial decay/recovery curve.

---

## 5. Findings so far

**Data quality, from actually running the labeling pipeline against real data:**

- **`fenris` has an 80% unknown-label rate** (44/55 events unresolvable) versus
  roughly 65–75% direct-match rates for the Playwright/LLM-scripted sessions —
  a real coverage gap worth investigating before treating Fenris data as
  comparably labeled.
- **The human dataset resolves far worse than the AI dataset overall: ~73%
  unknown (48,337/66,340 events) vs. ~2.3% for AI.** One user accounts for
  94.6% of all human events and alone drives most of that gap — possibly
  collected against an older template version, or humans genuinely generating
  more passive/ambiguous scroll-and-read activity than scripted task
  completion. Not yet root-caused.
- **`human_raw_combined.csv` is truncated** — the file cuts off mid-line, mid-
  JSON-field, at the very last row. The labeling script now detects this,
  falls back to a tolerant parser, and prints an explicit warning rather than
  silently treating the file as complete.
- Timestamps in the exported CSVs use **mixed formats** (with and without
  microseconds) — breaks naive `pd.to_datetime()` calls; handled with
  `format='mixed'`.

**Bugs found and fixed by actually running the agent scripts against a live
test server, not just reading the code:**

- All four Playwright scripts were clicking the site's navbar search button
  instead of the actual form's submit control (an ambiguous `button[type=
  submit]` selector matched the wrong element first in DOM order) — silently
  breaking registration and login for every one of them.
- Two scripts attempted a second account registration without logging out
  first; since `/register` redirects authenticated users to `/home`, this hung
  on a 30-60s timeout every time.
- A "read terms" step assumed same-tab navigation, but the link opens in a new
  tab (`target="_blank"`) — always reported failure despite working correctly.
- A tracking-flush call with no error handling could crash an entire bot trial
  outright if it ran during a page navigation — a race condition, not a
  reproducible bug, which explained inconsistent failures across runs.
- `User.last_message_at` / `tokens` existed in the ORM model but had no
  migration, 500-ing *every* register/login attempt on any database built from
  the actual migration chain — found by reproducing the failure locally,
  fixed with a new migration.
- A CodeQL scan on the resulting PR caught a real open-redirect vulnerability
  in the login `next` parameter (bypassable via backslash-style URLs); fixed
  and verified.

**Infrastructure:** the project's local dev environment and VM deployment had
drifted onto two entirely unrelated Alembic migration histories for the same
schema — reconciled by snapshotting the VM's uncommitted state to its own
branch before touching anything, rather than force-overwriting.

---

## 6. Repo map (research-relevant parts)

```
app/
  ai_defense.py          # UA rules + behavioral ML scoring
  models.py              # TrackedAction, UserSession, User schema
  static/track.js        # client-side event capture
  templates/admin_tracking.html
  gptTest.py, gemini.py, copilat.py, grok.py   # Playwright agent scripts (L1-L4)
Defense System/
  load_model_example.py, save_model_example.py, train_lr_model.py
models/
  RF_ai_detector_v2.joblib   # production RandomForest
  LR_ai_detector_v1.joblib   # LogisticRegression, synthetic data only
research/
  task_labeling.py       # task-type auto-labeling (DB or CSV)
migrations/               # Alembic history
```

---

## 7. Status / next steps

- [ ] Root-cause the human dataset's high unknown-rate before using it for
      Experiment 2
- [ ] Add a real `architecture` field so per-architecture (not per-username)
      breakdowns are possible
- [ ] One more independent agent architecture (Selenium/browser-use) for a
      genuine unseen-architecture test in RQ1
- [ ] Jitter/humanization perturbation module for the RQ3 Gen-1 agent
- [ ] Scale human data collection (target 30-50 subjects) across the same task
      set
- [ ] Retrain the LogisticRegression model on real labeled sessions instead of
      synthetic data
