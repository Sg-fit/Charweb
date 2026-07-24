---
layout: default
title: Charweb Research
---

# Charweb: Behavioral Biometric Detection of AI Web Agents

A controlled social-web testbed and detection pipeline for distinguishing human users from AI browsing agents — including scripted bots, LLM-driven agents, and custom-built autonomous agents — using passively collected interaction motor features (mouse, keystroke, click, scroll timing).

**Live testbed:** [charweb.net](https://charweb.net) · **Repo:** [github.com/Sg-fit/Charweb](https://github.com/Sg-fit/Charweb)
*Testbed is live on a local server — caution: may occasionally be unavailable due to server errors; repo may not fully reflect the latest local state.*

---

## 1. Overview

Charweb is a small, fully-instrumented social web application (feed, forms, real-time chat, a dungeon RPG, daily rewards) built specifically to generate realistic, varied human and AI interaction data under one consistent tracking pipeline. Every click, keystroke, scroll, and mouse movement is logged client-side with real timestamps via `/api/track`. It is a varied application based on [Microblog](https://blog.miguelgrinberg.com/post/the-flask-mega-tutorial-part-xvii-deployment-on-linux)

This repo contains:
- The Charweb application itself (Flask / Flask-SocketIO / Gunicorn / Cloudflare Tunnel)
- A task-type auto-labeling engine (`task_labeling.py`) that segments raw event logs into task-typed episodes
- A behavioral-feature engineering + classification pipeline (RandomForest / LogisticRegression) for human-vs-AI detection
- A growing dataset of labeled human and AI agent sessions across multiple automation architectures

## 2. Why This Project

Most published bot-detection work targets scripted, non-adaptive bots and evaluates against a narrow set of frontier LLM-driven agents. Two things remain largely untested:

1. **Does detection generalize across automation *harnesses/frameworks*, not just across underlying *models*?** Charweb's dataset spans genuinely different architectures — scripted Playwright agents at four sophistication tiers, an LLM-driven agent (GPT/Gemini-backed), and a custom hobbyist-built agent ("Fenris") — letting us separate model-fingerprint effects from harness-fingerprint effects.
2. **Do detectors trained on frontier/task-following agents fail on the long tail of small, custom, non-standard agents?** As agent-building tools become accessible to individual developers, real-world defenders increasingly face agents that were never part of any published detector's training distribution.

## 3. System Architecture

```
Browser (human or agent)
      │  clicks / keystrokes / scroll / mousemove
      ▼
track.js (client-side, real timestamps)
      │  batched JSON
      ▼
/api/track  ──────────────►  SQLite (TrackedAction table)
                                    │
                                    ▼
                    task_labeling.py (event → task-type + session)
                                    │
                                    ▼
                    feature engineering (timing CV, mouse dynamics,
                    click precision, scroll/keydown ratios, ...)
                                    │
                                    ▼
                    RandomForest / LogisticRegression classifier
                    (group-held-out CV, leave-one-architecture-out)
```

## 4. Detection Pipeline — Method Summary

**Features engineered per session/episode:**
- Inter-event and inter-keystroke timing (mean, median, std, coefficient of variation)
- Backspace ratio, typing accuracy
- Mouse path length, straightness, speed, turning-angle curvature
- Click precision / inter-click timing
- Action-type proportions (click / keydown / mousemove / scroll)

**Task-type auto-labeling:** every event is tagged with one of 6 categories listed: `signup_login`, `search`, `feed_browse`, `profile_edit`, `timed_dungeon`, `chat` via direct target-field matching with a time-windowed carry-forward for ambiguous events (generic buttons, mousemove, scroll). Unresolvable events are explicitly marked `unknown` — full method in [`task_labeling.py`](./task_labeling.py).

**Evaluation:** group-held-out cross-validation (grouped by subject/architecture, not session) to avoid leakage; leave-one-architecture-out for cross-architecture generalization testing.

## 5. Key Findings So Far

### 5.1 Naive within-session CV overstates performance
An initial LOOCV evaluation without subject/session grouping reported **AUC ≈ 0.97**. Once re-evaluated with proper group-held-out CV (no chunks/sessions from the same person or architecture appearing in both train and test), performance drops to a more honest **AUC ≈ 0.865** (accuracy 0.87, balanced precision/recall). This gap is a methodological finding.

### 5.2 Detection does not generalize evenly across agent architectures

Leave-one-architecture-out results (classifier trained on all other architectures + humans, tested on the held-out one):

| Architecture held out | N sessions | Recall on unseen AI |
|---|---|---|
| Playwright L1 | 23 | 0.96 |
| Playwright L2 | 17 | 0.82 |
| Playwright L3 | 15 | 1.00 |
| Playwright L4 | 15 | 1.00 |
| LLM-driven (GPT) | 4 | 0.75 |
| LLM-driven (Gemini) | 3 | **0.00** |
| Fenris (custom agent) | 1 | **0.00** |

*In-distribution reference (all architectures seen during training, 5-fold CV): recall 0.97, AUC 0.998.*

**Interpretation:** the classifier generalizes well within the Playwright family — it has effectively learned "Playwright-style automation" — but collapses completely on architectures never seen during training. High in-distribution accuracy masks near-zero cross-architecture generalization. `llm_gemini` (n=3) and `fenris` (n=1) are too small to treat as rate estimates; both are flagged as priority targets for expanded data collection.

### 5.3 A custom, non-frontier agent doesn't fit the task taxonomy at all

While diagnosing why Fenris (a custom "Jarvis-like" autonomous agent) had an 80% task-unknown rate, we found this wasn't a labeling-window artifact — even a 1-hour carry-forward window only reduced it to 72.7%. The real cause: **Fenris interacts almost entirely through generic, non-task-specific UI elements** (bare `BUTTON`/`submit`/`mousemove`), rarely touching the distinctly-tagged fields (`about_me`, `q`, `mission-timer`, etc.) that anchor task labeling for every other architecture, and shows multi-hour idle gaps between activity bursts.

This suggests custom/non-frontier agents may not just be *harder to detect* — they may not even fit the *analytical categories* (task taxonomies, feature assumptions) built around frontier and task-following agents. However, due to the lack of data, this finding might be mitigated by the small data set.

## 6. Current Limitations

- **Small, uneven architecture samples:** Playwright tiers are reasonably sized (15-23 sessions); LLM-driven and custom-agent architectures are not (n = 1-4). Cross-architecture recall numbers for these should be read as case studies, not rate estimates, until more data is collected.
- **Human sample size and independence:** current human data comes from 6 independent subjects (post-admin-exclusion) plus session-chunking of a few heavy users — thinner and less independent than the AI side.
- **Task-taxonomy coverage:** built from known Charweb DOM element IDs; may not generalize to agents that interact with the site in unanticipated ways (see Fenris finding above).
- **Consent / ethics:** anonymization of human subject identifiers is complete (see §8); formal informed-consent documentation from pilot participants is still required before any public dataset release beyond code/analysis artifacts.

## 7. Roadmap

- [ ] Expand `llm_gemini`, `llm_gpt`, and `fenris`-class sessions to 10-15+ each, matched across all task types
- [ ] Add 1-2 additional automation frameworks (Selenium, browser-use) to further separate harness vs. model effects
- [ ] Run Claude through the existing LLM-driven-agent harness to isolate Fenris's harness effect from its model effect (currently confounded — see Note below)
- [ ] Scale human data collection to 30-50 independent subjects doing the same standardized task set as AI agents
- [ ] Task × generation adversarial-jitter decay experiment (timing-jitter evasion + classifier retraining, per task type)
- [ ] Agent decision-latency as a jitter-resistant complementary signal channel
- [ ] Bootstrap confidence intervals on all headline metrics; naive single-feature baseline for comparison
- [ ] IRB/consent documentation and dataset anonymization ahead of any public release

> **Note on Fenris:** Fenris currently confounds model and harness (it runs on a different model *and* a different harness architecture simultaneously), so its 0% cross-architecture recall cannot yet be attributed to either factor specifically. Running the same model through the standard LLM-driven-agent harness is the planned fix.

## 8. Repository Structure

```
├── app/                        # Charweb Flask application
├── task_labeling.py             # Task-type auto-labeling (DB or CSV input)
├── code.ipynb                   # Feature engineering + classifier pipeline
├── classifier_1.ipynb           # Earlier classifier iteration (session-level, hand-labeled)
├── ai_anonymized.csv            # AI agent session data (already architecture-coded IDs, e.g. ai_L4_t3_..., gpt_run6, fenris)
├── human_anonymized.csv         # Human session data, admin activity excluded, real usernames/emails replaced with P01–P07 / U01–U07 IDs
└── README.md                    # This file
```

**Anonymization note:** `human_anonymized.csv` replaces every real username, email, and display name with an anonymous ID (`session_label` → `P01`–`P07`, `username` → `U01`–`U07`; mappings kept in a private, unpublished key file). `ai_anonymized.csv` required no changes — AI session identifiers were already architecture-coded (e.g. `ai_L4_t3_3c1cb7fb`, `gpt_run6`, `fenris`) rather than personally identifying. The `admin_hi` session (23,237 events of administrative dashboard activity, unrelated to the standardized task set — see §5.4) is excluded from the published human dataset entirely.

## 9. Citation / Status

This is an active, unpublished research project. Findings above are preliminary and pending expanded data collection per the roadmap. Not yet reviewed — feedback and issues welcome.
