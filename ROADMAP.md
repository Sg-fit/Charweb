# Charweb — Phases & Milestones (revised)

> **STATUS UPDATE (2026-10-03) -- this roadmap is dated 2026-08-03 and is partly obsolete.**
> Source of truth for what happened since: `REPO_DELTA.md` section 6 and the `claude/*.md`
> findings docs in the claude.ai Project. Corrections to what is written below:
>
> - **The study is now AI-only and zero-human-subjects.** M3 below ("Human data, 30-50
>   subjects, IRB") no longer applies; the IRB critical path and the human-baseline
>   items in M0.2/M3/M4's readiness gate are void. Phase-0 human-vs-AI data is historical
>   and is not part of the claim.
> - **M1 closed.** Labeler repaired (24,003 events relabelled), validated against 200 hand
>   labels: accuracy 0.893 weighted / 0.867 unweighted, Cohen's kappa 0.863 (intra-annotator).
>   Items 1.1-1.5 above are superseded.
> - **M3 closed** (CIs + leave-one-task-out done), reframed as harness / model / instruction
>   axes rather than human-vs-agent.
> - **Phase 0 mostly done:** configurable harness runner and grid driver exist (M0.5);
>   migrations applied on the VPS (M0.1/M0.4). Still unconfirmed here: M0.2 (`pageload` /
>   `performance.now()` instrumentation) for Phase III.
> - **Production DB is PostgreSQL**, not the SQLite files discussed in the open
>   questions and repo-hygiene notes below (nightly `pg_dump`, restore-tested
>   2026-11-07).
> - **E3 (client- vs server-trust, tamper ladder) complete; E4 (mimicry) recommended dropped.**
> - **Open:** H1 crossed-cell decomposition (`harness x model` interaction share), then
>   **M4 (abstract + poster), not started -- the real deadline risk.**
> - **Metric change adopted in write-up:** add TPR at fixed low FPR and AUPRC (with
>   prevalence baseline) next to balanced accuracy -- `research/bootstrap_ci.py --ovr`.
>
> Sections below are left as originally written for the record.

Supersedes §8 of *Research Proposal: Detecting the Long Tail*. The three
research phases are unchanged — the escalating spine still holds. What changed
is the milestone breakdown: a body of instrumentation work turned out to sit
*before* any collection can start, two of the original milestones were
under-specified in ways that would have blocked their own goal, and the sample
targets were below what the comparable literature uses.

Revision date: 2026-08-03.

---

## What changed, and why

**A Phase 0 now exists.** The original plan treated M1 as the only prerequisite
to collection. In practice there are six pieces of instrumentation and
experimental setup that must land *before* M2/M3, because collecting without
them means collecting twice.

**The harness axis was not identifiable.** Proposal §3 names
Playwright-vs-Fenris as the harness-axis comparison, but Playwright is scripted
with no LLM at all while Fenris varies harness *and* model. That contrast
confounds three things, so Phase I could have shown that generalisation fails
without being able to attribute it to an axis — which is the contribution.
Fixing it requires one new data cell (same model, two harnesses), promoted into
M2.

**No architecture metadata exists.** Nothing in `TrackedAction` or
`UserSession` records which architecture produced a session;
`resolution_breakdown_by_owner()` groups on username as a proxy and its own
docstring concedes this. M4 is *defined* as leave-one-harness-out. It cannot run
against a username substring.

**Phase III's signal was not being recorded.** Proposal §5 states decision
latency is "already present in our LLM-driven-agent logs," but Charweb never
captured it — `track.js` has no page-load event, so there is no site-side
page-ready→first-action measurement and no human baseline at all. This moved
work from M7 to Phase 0: instrument first, or every session collected is
unusable for Phase III.

**Sample targets were low.** *Whose Agent Are You?* uses 30 trials per agent;
*Known By Their Actions* has ~75 traces per model and reports ~25 is where
performance plateaus. M2's original 10–15 sits below both.

**New capability, not in the original plan.** Passive TLS fingerprinting is now
built and tested. It is optional, but it gives a behavioural-only vs
behavioural-plus-network comparison *on your own data* — the axis *Whose Agent
Are You?* uses to reach 97%.

---

## Phase 0 — Instrumentation & Setup

Nothing here is research; all of it gates research. Target: 1.5–2 weeks,
overlapping M1 and the IRB submission.

| ID | Milestone | Status | Gates |
|----|-----------|--------|-------|
| M0.1 | Migrate off local machine to VPS (Hetzner CX22, Ashburn) per `deploy/DEPLOY.md` | runbook written | all collection |
| M0.2 | Finish instrumentation: `pageload` event, `performance.now()` clock, mousemove event-time stamp, visibility tracking | **not started** | **Phase III** |
| M0.3 | Experimental metadata: `harness`, `model`, `instruction_condition`, `run_id` per session | **not started** | **Phase I / M4** |
| M0.4 | Apply migrations `b3f7c21a90de` (url) + `c8e2f45b71ac` (client addr/port); resolve where historical rows live | migrations written, unapplied | M1 validation, TLS join |
| M0.5 | Configurable harness runner + batch grid driver | designed | M2, and makes M5 nearly free |
| M0.6 | Pilot 3–5 sessions end-to-end; verify labels, latency decomposition, features | — | M2, M3 |
| M0.7 | *(optional)* Deploy passive TLS capture | built + tested | optional network-layer result |

M0.2 is the one with a hard deadline: it must precede M2 and M3 or Phase III
has no data and no human baseline.

M0.5 is worth more than it looks. One parameterised harness with knobs for
target selection, mouse path, typing mode, delay policy, waiting policy, and
navigation mode gives three things at once: the crossed design M4 needs,
property-level ablation stronger than comparing harness identities, and M5's
jitter module as a config file rather than a component. Note that Playwright
L1–L4 is *already* a harness manipulation with the model held constant — it has
just never been framed that way, and formalising what varies across the tiers
turns four existing data points into a Phase I result at zero collection cost.

---

## Phase I — Harness vs. Model

### M1 — Task-type auto-labeling

Code complete. **Results and validation are not.** Nothing has been run against
real data.

Built this session: `CARRY_FORWARD_SECONDS` / `EPISODE_GAP_SECONDS` split from
the old single constant; per-session adaptive windows; a URL matching tier
between target-match and carry-forward; bidirectional fill with configurable
conflict policy; `--ignore-url` and `labeling_regime` for cross-era
comparability; per-episode resolution counts; `sweep_gap.py` (window sensitivity
grid); `validate_carry.py` (holdout fill accuracy). Defaults reproduce prior
labeling byte-for-byte.

Remaining:

| # | Task | Blocks M4? |
|---|------|-----------|
| 1.1 | Run `sweep_gap.py` + `validate_carry.py` on the real exports | no — but M1 has no evidence without it |
| 1.2 | Hand-label ~200 sampled events; report taxonomy precision | **yes** |
| 1.3 | Commit `research/test_task_labeling.py` | no |
| 1.4 | Update README §4 (still describes target-match + carry-forward only) | no |
| 1.5 | Episode→feature join — decide M1 or M3, then build | **yes** |

On 1.2: `validate_carry.py` measures whether *fill* recovers a label but treats
the anchor rules as ground truth. Nobody has checked that `about_me` →
profile_edit is correct. "How accurate is your labeler?" is the first question a
reviewer asks and the answer is currently "it is asserted." One afternoon.

On 1.5: `build_episodes()` emits identifiers, timestamps, and counts — no
features. Feature extraction still runs per *session*. Phase I may not need
episodes, but Phase II does.

### M2 — Agent data

**Target raised to 25–30 per architecture** (was 10–15).

Must include the crossed cell: **Fenris's model driven through the LLM-driven
harness**. Without it there is no clean harness contrast and M4's headline table
has no harness column.

Task types must be covered *within* each architecture, not pooled across them —
*Known By Their Actions* finds single-task transfer weak but multi-task pooling
recovers attribution.

Collection controls: cap concurrency at 2–3 and record it (server load inflates
the latency signal); interleave architectures across time rather than blocking
them; freeze the site and record a git SHA per run; seed and record task order.

### M3 — Human data

30–50 subjects. **Start the IRB submission on day 0** — it is the critical path
and its blocker is administrative, not technical. The original serial
M1→M2→M3 ordering parks a 3–4 week clearance behind two weeks of other work for
no reason.

Sub-steps: IRB/consent paperwork → consent flow (partial scaffolding exists:
`User.terms_accepted_at`, `/terms`, `accept_terms`) → standardised task script
matched to the agent instruction document → collection.

Controls: **desktop only, or record and stratify by device** — a mobile browser
emits zero mousemove events and would look like a headless agent on every mouse
feature. Guided task page with automatic acceptance checks. Same instruction
document as the agents. Note that current human data is 6 independent subjects
plus session-chunking of heavy users, which is not independent and must be
grouped in CV.

### M4 — Phase I analysis

Leave-one-**harness**-out and leave-one-**model**-out, reported as separate
axes. Contrast against *Known By Their Actions*' model-only holdout.

Readiness gate — all must hold:

- [ ] every session carries `harness`, `model`, `instruction_condition`
- [ ] ≥25 sessions per architecture, task types covered within each
- [ ] at least one model appears under two harnesses
- [ ] ≥30 human subjects under documented consent
- [ ] labeler validated against hand labels (1.2)
- [ ] grouped CV parameterised by `harness` and by `model` as alternative keys

That last item is M4's machinery and costs nothing to build during M3's
collection wait.

---

## Phase II — Task-Conditioned Detection on the Long Tail

### M5 — Perturbation module

Largely free if M0.5 landed: jitter is the delay-policy and mouse-path knobs of
the configurable harness. Building it that way also makes Gen-0 vs Gen-1 differ
in exactly one documented setting rather than by a bolt-on layer.

*Optional extension:* SapiAgent generates human-like mouse trajectories offline
(CNN/RNN autoencoders trained on SapiMouse). Porting its trajectories in as a
`mouse_path = sapiagent` setting gives a learned adversary evaluated **within**
Charweb — same instrumentation, same tasks. Do not compare SapiAgent output to
Charweb data across datasets; that measures dataset artifacts, not evasion.

### M6 — Phase II analysis

Task × generation × architecture-class AUC surface. Gen-0 baseline → Gen-1
frozen classifier → Gen-2 retrained. The question is whether "retraining
recovers detection," established for frontier models, holds for long-tail
architectures with far less training signal.

---

## Phase III — Cognitive Latency

### M7 — Decision latency

Depends entirely on M0.2 having landed before collection.

Measurement: t₀ = `navigation.domContentLoadedEventEnd`, t₁ = first deliberate
event's `performance.now()`, latency = t₁ − t₀. Both on the same per-document
stopwatch, so network and server time are excluded by construction.

Controls: subtract tab-hidden time; match within-URL or within-task-type; median
and CV rather than mean.

**The claim's sharpest form.** If Gen-1 jitter delays every action it also
delays the first one, so "jitter doesn't touch decision latency" is circular.
The non-circular version: model inference imposes a *floor* that jitter can only
add to. So the discriminative statistic is the **lower tail** — minimum or 10th
percentile of per-page latency — not the mean, which jitter shifts. Humans have
no comparable floor.

Validate the site-side measurement against the harness's own logs; the paper's
claim must rest on what a defender can observe, but showing the proxy tracks
ground truth pre-empts "how do you know you're measuring inference."

---

## Cross-cutting

### M8 — Write-up and statistics

Bootstrap CIs on all headline metrics; naive single-feature baseline;
Mann-Whitney / Cliff's delta; related-work positioning table.

**Metric change:** AUROC alone is insufficient. It is threshold-free, and a
system whose goal is blocking is nothing but a threshold. Report **TPR at fixed
low FPR** and **AUPRC** (with prevalence baseline stated) alongside it.

**Feature additions** from *Known By Their Actions*, all computable from data
already logged: click-coordinate dispersion, structural key ratio, link-click
ratio, time-to-first-action. These are the features that *survive* delay
injection, so they matter most for Phase II.

Add XGBoost to the model set — it beat RandomForest, LogReg, Lasso, and an LSTM
in the comparison paper. Note that it beating the LSTM is itself evidence that
sequence modelling adds little over aggregate features, which supports the
current approach.

### M9 *(optional)* — External benchmark

Run SapiMouse (120 subjects) + SapiAgent as a **self-contained** benchmark, held
out by subject, entirely separate from Charweb. Report alongside the Charweb
number for comparability with published work.

Do **not** blend: SapiMouse humans were recorded on a purpose-built
mouse-logging page in 3-minute sessions. Training "human = SapiMouse, AI =
Charweb agents" separates *datasets*, not humans from agents — and your
mousemove is sampled on a 100 ms interval while theirs is a raw event stream, so
a tree model finds the instrumentation difference in one split.

Also adopt the 39-feature definition from the aggregator repo so the numbers are
comparable rather than ad hoc.

Priority note: your own no-mouse ablation scored *identically* to the full
feature set (AUC 1.0 both ways), and top features are `backspace_ratio`,
`iki_cv`, `inter_event_cv`. Mouse dynamics is not where the signal lives, so M9
should not displace M2.

---

## Timeline

| Block | Original | Revised |
|---|---|---|
| Phase 0 | — | 1.5–2 wk (parallel with M1, IRB) |
| M1 | 3–5 d | 3–5 d remaining |
| M2 | 1–2 wk | 2–3 wk (higher n, crossed cell) |
| M3 | 3–4 wk | 3–4 wk (start day 0) |
| M4 | 1 wk | 1 wk |
| M5 | 1 wk | 2–3 d if M0.5 landed |
| M6 | 2 wk | 2 wk |
| M7 | 3–5 d | 3–5 d |
| M8 | 2 wk | 2 wk |
| **Total** | **10–12 wk** | **13–16 wk** |

Critical path is M3's IRB clearance. Everything else should finish before it
clears.

---

## Open questions still blocking

1. Was Fenris run under `ai_site_intro.md` (free exploration) while the
   Playwright tiers got `agent_site_guide.md` (five-task checklist)? If so, part
   of the 80% unknown rate is an instruction confound, not an agent property.
2. Is Fenris vision-driven or DOM-driven? Vision-driven would mechanistically
   explain the unknown rate — coordinate clicks land on generic containers, never
   distinctive element ids — and upgrade §5.3 from "one unusual agent" to "a
   class of agent invisible to DOM-anchored taxonomies."
3. Which model does Fenris run, and can it be driven through the LLM harness?
   Determines whether the crossed design is achievable.
4. Does the production DB still hold the historical `TrackedAction` rows? Local
   `instance/app.db` has zero; both backups predate the `session_uid` migration.
5. Is `manifest.csv` complete for past runs — can harness/model be backfilled?
6. Episode→feature join: M1 or M3?
7. Context on "the Metaxa email" — if it raises a dual-use concern, it should
   shape the ethics note *before* an evasion generator goes into the harness.

## Deferred

Mid-page blocking defense architecture — deliberately deferred until after the
paper, since the design depends on results that don't exist yet. The relevant
groundwork (incremental scoring already runs on every `/api/track` POST) is in
place when it's time.

## Repo hygiene, unrelated to research but worth an hour

README §8 lists `code.ipynb`, `classifier_1.ipynb`, `ai_anonymized.csv`,
`human_anonymized.csv` as repo contents; none are present, so the
reproducibility claim is currently false. Three SQLite backups
(`*.db.bak.*`, `*.db.old_orphaned_chain`) are untracked and *unignored* — they
contain the user table with real emails and password hashes, and would be
committed by a `git add -A`. 72 files show pure CRLF churn against an
LF-storing repo.
