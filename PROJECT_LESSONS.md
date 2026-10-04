# Charweb — Findings, Failures, and What They Taught Us

A working record of every substantive problem hit while building the web-agent
attribution study, and every observation worth keeping. Organised so it can be
read as a presentation narrative (§1 → §6) or scanned as a checklist (§7).

Last updated 2026-08-28.

---

## 1. The headline findings

| Claim | Number | Chance | Status |
|---|---|---|---|
| Harness identity is recoverable | **1.000** | 0.250 | established |
| …and survives an unseen task | **0.988** | 0.250 | established |
| Model identity is recoverable | **0.637** (p=0.005) | 0.333 | established |
| …and survives an unseen task | **0.492** (p=0.015) | 0.333 | established |
| Attribution does not need the client clock | server **0.948** vs client **0.950** | 0.167 | established |

**The through-line: the scaffolding, not the model, is what is fingerprintable.**
Harness identity is essentially perfectly separable; model identity is real but
modest. For anyone building agent detection, that gap is the useful result — the
harness is the durable signal, and swapping the underlying LLM changes far less
than swapping the framework around it.

### Three observations worth presenting on their own

**Agents don't fabricate when the target doesn't exist.** `impossible_goal`
scores 0.966: told to find a post about scuba diving in Antarctica — which does
not exist — agents search, come up empty, and stop rather than commenting on an
unrelated post. That is the opposite of the usual assumption about LLM agents
under task pressure.

**Attribution survives a lying client.** Rebuilding every timing feature from
the server's receipt timestamps instead of the browser's self-reported clock
costs 0.002 accuracy. An attacker who rewrites `track.js`'s clock cannot escape
a server-side detector. Most detection work has only client telemetry and
cannot run this comparison at all.

**Removing confounded features made the result stronger, not weaker.** Dropping
the three session-length features raised in-distribution accuracy 0.626 → 0.637
and pushed the transfer p-value from 0.035 to 0.015 — across the Bonferroni
threshold, converting "suggestive" into "established."

---

## 2. Bugs that changed the results

Ranked by how much damage they did before being caught.

### 2.1 The empty-reply bug — a 200 OK with nothing in it

`max_tokens=300`. `gpt-oss-20b` is a reasoning model: it emits chain-of-thought
first and the answer after. The budget was consumed by reasoning, so `content`
came back **empty on a successful HTTP 200** — no exception, no retry, nothing
any error path could see. Measured: `finish_reason='length'`,
`reasoning_chars=3714`.

The agent read an empty reply as "model busy", counted a stall, and quit after
two. Sessions died after ~3 actions out of 30.

- **Cost:** roughly two thirds of all model calls, across every `llm_driven`
  session in the corpus.
- **Fix:** `max_tokens` → 2500; empty replies counted separately; salvage from
  `reasoning_content` only when it contains real JSON.
- **Result:** `steps_used=3/30` → `30/30`, `empty=0`.

### 2.2 Two wrong diagnoses before the right one

Worth presenting, because the debugging path is more instructive than the bug.

1. *"The step cap is too low."* Raised 10 → 30. Task success did not move.
2. *"NVIDIA is rate limiting."* Plausible: sessions were dying early and the
   provider is free-tier. Added pacing and retry tolerance. Still nothing.
3. Added per-session health counters, which printed:
   `calls=30 rate_limited=0 transient=0 stalls=20`.

Twenty stalls with **zero** rate limits and **zero** transient errors. A stall
can only follow six exhausted retries, each of which increments one of those
counters. Both zero meant no retry ever ran — so the calls were succeeding and
returning nothing. The counters distinguished three hypotheses that all
produced identical symptoms.

### 2.3 The perception cap — tasks that were impossible, not hard

`EXTRACT_JS` listed at most 30 elements. On `/explore`, 98 exist; **68 were
never shown to the agent.** An element not in the listing does not exist as far
as the model is concerned.

Worse, Charweb's comment box lives inside `<div class="comments-section"
style="display: none">` and the extractor (correctly) skips hidden elements. So
the comment input was *never* listed until something clicked the toggle — and
the toggle itself was often past index 30. On top of that, **there is no
post-detail page at all** (no `/post/<id>` route), while the instruction said
"open it, and leave a comment."

- **Cost:** zero comments across 239 sessions, while search succeeded 87% of the
  time. Two of five conditions scored as agent failures.
- **Fix:** cap → 120; form controls ranked above links so a cap can never hide
  the one control a task needs; instruction text rewritten to describe the real
  UI.

**This is the most important methodological point in the project:** two
conditions were recorded as capability failures for reasons that had nothing to
do with the agents.

### 2.4 A dead default that scored as clean

The NVIDIA default model `meta/llama-3.3-70b-instruct` reached end-of-life and
returns `410 Gone`. Forgetting `CHARWEB_LLM_MODEL` silently produced label-only
sessions. Worse, the health metric marked them **`clean=1`** — a retired model
kills the session on call one, so every counter stays at zero and the session
looks perfect.

- **Fix:** `model_dead` flag folded into `clean`; default changed to a model
  verified alive by `check_providers.py`.
- **Lesson:** a dead default is worse than no default. Silence is not health.

### 2.5 Label loss — `None/None` on logged-in sessions

Two independent server-side bugs, found together:

- **`sendBeacon` drops custom headers.** The page-hide flush carried no `X-*`
  attribution headers, so sessions were recorded unlabelled. Fixed with
  `fetch(keepalive)` plus a `cw_*` cookie fallback read server-side.
- **Sticky login.** `/api/track` set `user_session.user_id` unconditionally, so
  the post-logout flush reset it to `None` — a session that logged in mid-visit
  was recorded as never logged in.

### 2.6 Hyphens in usernames

`scripted_agent` generated accounts like `scr_plain_001-4821`. The username
validator rejects hyphens, so **every registration silently failed** and every
scripted session browsed logged-out. Underscores fixed it.

---

## 3. Methodology traps

### 3.1 The batch/temporal confound (closed)

Every harness had been collected in one contiguous block, making *when* a
session was recorded interchangeable with *what* recorded it. No analysis of
that data could separate them — the fix had to be scheduling, not statistics.

Rebuilt collection to rotate arms one session at a time, reshuffling order each
round. Result: a classifier trained on collection time alone scores **0.090**
(chance 0.250) on the harness axis and **0.332** (chance 0.333) on the model
axis. The confound is dead, and the test is now a permanent part of the pipeline.

### 3.2 The session-length confound (closed, and instructive)

`deepseek` and `llama` were rate-limited during collection while `gpt-oss`
mostly wasn't, and session lengths differed sharply (Kruskal p=0.0006; llama
119 events vs ~43 for the others). Length genuinely differs by model, so this
had to be tested rather than assumed away.

```
LENGTH ALONE           0.590     length IS informative
WITHOUT length feats   0.637     HIGHER than the full set
LENGTH-MATCHED (IQR)   0.666     n=37, higher still
FULL (25 features)     0.626
```

If the axis were an artifact of length, both controls would fall toward chance.
Both rose. **Decision: report the length-free feature set as primary.** It is
both the higher number and the defensible one.

### 3.3 Latency features: tested and rejected

Added time-to-first-action and deliberation-time features on the theory that an
LLM harness pauses to think. Accuracy *dropped* 0.637 → 0.619. The per-model
table showed why:

```
timing_ttfa_ms      107.32   108.38   108.04   <- identical across 3 models
timing_think_mean  4404.34  2184.45  1789.99   <- 2.5x spread
```

`ttfa` was degenerate — measuring browser paint time, not deliberation — and a
constant feature is pure noise that dilutes informative ones under feature
sampling. And `think_mean` correlates with exactly the model that was being
throttled, so it risks reintroducing the rate-limit confound through a new door.

**"We tested deliberation latency, found time-to-first-action degenerate and
think-time confounded with provider retry behaviour, and excluded both"** is a
stronger methods paragraph than quietly adding them would have been.

### 3.4 A condition that would have collapsed into another

`targeted_search` originally told agents to search for a "dungeon"-related post.
Checking the corpus: **"dungeon" appears in 0 of 40 posts.** The condition would
have become a second `impossible_goal`, and the contrast between them — the
whole point of the pair — would have been silently lost. Nothing in the
collected data would have revealed it.

Replaced with "introduction" (12/40 posts) and added `check_conditions.py`,
which refuses to collect if the findable term drops below 3 posts or the
"impossible" probe ever appears. The second guard matters because agents write
posts: the day one of them mentions scuba diving, `impossible_goal` quietly
becomes achievable.

### 3.5 Action timeouts — suspected, measured, dismissed

`do_action` set no timeout, so Playwright's 30s default applied. The agent
selects elements by `data-llm-idx`, which is stamped per listing and goes stale
after any navigation — so clicking a stale index waits the full 30 seconds and
inserts a dead gap straight into the timing features.

The worry was differential contamination: scripted harnesses use known-good
selectors and never time out, while LLM-driven ones pick from a listing that
goes stale. If so, part of the harness result would be reading "this harness
stalls for exactly 30 seconds" rather than how it browses.

Unlike the empty-reply rate, this was **measurable retroactively** — a timeout
leaves a ~30s signature in the inter-event intervals, and those are in the data.
Fraction of gaps in [27s, 36s]:

| harness | rate |
|---|---|
| fenris | 0.069 |
| llm_driven | 0.002 |
| playwright | 0.002 |
| scripted_humanlike / noisy / plain | 0.000 |

Two things are true. The rate is tiny for `llm_driven` (56 gaps in 24,045, ~4s
per session against 23–51s durations) — far too small to manufacture the
result. But scripted harnesses sit at **exactly** zero, so any ~30s gap is a
perfect LLM-harness giveaway, present in perhaps 13% of those sessions. A real
leak, just not a load-bearing one.

Settled by the same control used for provider latency on the model axis —
delete the feature group and see if the result survives:

```
ALL features        0.950
WITHOUT timing      0.947      (n=727, 6 classes, chance 0.167)
```

Harness attribution does not need timing features at all. **That is the stronger
form of the argument:** rather than arguing the timeouts are rare, show the
result does not depend on the features they contaminate.

*Housekeeping for future collection: an explicit 5s timeout (30s is far longer
than any real interaction needs) and a `timeouts` counter in the health file, so
the next person reads the rate instead of inferring it from interval histograms.*

### 3.6 Chance is 1/k, not the majority-class rate

Repeatedly corrected. With 4 harness classes, chance is 0.250. Balanced accuracy
against majority-class baseline is a different and much weaker claim.

### 3.7 Multiple comparisons

The transfer result initially sat at p=0.035 — significant alone, but not under
a Bonferroni correction for two tests (threshold 0.025). Flagged as "suggestive"
rather than established. Removing the length features moved it to p=0.015, which
clears the correction. Worth noting that the honest fix came from better
features, not a friendlier test.

### 3.8 The design is nested, not crossed

Model is nested inside harness — only `llm_driven` has multiple models — so a
crossed ANOVA is not identifiable. Used semi-partial R² (drop-one-factor ΔR²)
instead, which avoids double-counting shared variance in an unbalanced design.

### 3.9 A scoring criterion that measured the wrong thing

`checklist`'s "edited profile" item was scored by whether the session visited
`/edit_profile`. It scored **99%** — because every harness lands on that page
during registration. The criterion measured registration, not the task. Changed
to test whether `about_me` is actually non-empty.

### 3.10 An inverted success metric

`impossible_goal` scores success for *correctly giving up*. That makes it
unusable as an evasion-tax condition: an attack that suppresses actions would
*raise* its score. Conditions now carry an explicit "usable as tax metric" flag
rather than leaving that for someone to notice later.

### 3.11 Analysing a stale export

An analysis was run on a CSV written before its batch finished — n=348 against
an expected ~427. The batch was fine; the file was old. Exports now fail loudly
and list available `run_id`s rather than producing a confusing sklearn error on
an empty frame.

---

## 4. Operational failures

Small, repeated, and collectively expensive.

- **Retired models mid-study.** `meta/llama-3.1-8b-instruct` (39 sessions) and
  `meta/llama-3.3-70b-instruct` (9) began returning 410; `gemma-3-12b`,
  `mistral-nemo-12b` returned 404. Provider catalogues are not stable across a
  study. Led to `check_providers.py`, which validates every candidate model on
  every key in the collector's *real* request shape before a batch runs.
- **The model-alias trap.** The provider sweep's first recommendation was
  `gemini-3.5-flash-lite` + `gemini-flash-lite-latest` — almost certainly the
  same model under two names. That would have been a model axis containing one
  model, and nothing downstream would have revealed it: both labels appear, both
  collect fine, the classifier just quietly fails. Alias collapsing is now part
  of the recommender, and `-latest` ids are avoided because a provider can
  repoint them mid-study.
- **Provider-specific defects.** `gpt-oss-20b` fails on Groq with
  `400 tool_use_failed` while working fine on NVIDIA. Same model, same request,
  different provider.
- **Placeholder pastes.** Four separate times, a literal placeholder
  (`nvapi-...`, `<the-real-id>`, `MODEL_A`) was pasted as a real value. The
  9-character "key" produced 403s that looked like auth failure. Tools now
  refuse keys under 20 characters or starting with `<`.
- **Orphaned processes.** `pkill -f run_interleaved.py` leaves `llm_agent.py`
  children and Chrome running, consuming rate limit invisibly. Kill parent →
  children → chrome, in that order.
- **Provider/model confound risk.** Timing features are the harness axis's
  strongest signal, so a model axis built across two providers would compare
  infrastructure as much as models. The model axis is always same-provider.

---

## 5. The one open risk

**Differential degradation, and why it is different from everything else here.**

The three confounds in §3 were all closed the same way: the confounding variable
was *in the data*, so it could be measured, controlled for, and shown not to
drive the result. Collection time was in the data. Session length was in the
data. Timing features could be dropped and everything recomputed.

The empty-reply rate is **not in the data.** Those sessions have no `empty`
counter, because the counter did not exist when they were collected.

If the truncation rate differed by model — plausible, since a heavier reasoner
overruns a 300-token budget more often — then part of the 0.637 is "which model
blows the budget" rather than "how this model browses." Both produce real,
stable, learnable differences in the feature vectors. A classifier cannot
distinguish them, and neither can a permutation test: the signal is genuinely
there, it just isn't the signal being claimed.

There is a second-order version on the harness axis. Scripted harnesses never
call an LLM, so they never had empty replies; `llm_driven` and `fenris` had them
constantly, each producing a stall or a truncated session. Some fraction of that
1.000 may be detecting "this harness intermittently pauses on nothing." Harness
attribution would be near-perfect regardless — scripted and LLM-driven behaviour
differ enormously — but the number is contaminated in a way that cannot
currently be quantified.

**Resolution:** re-collect the model axis at the fixed settings with health
counters on (~45 sessions, a few hours), then compare against the old batch. If
they agree, the clean numbers are reported and the defect is a footnote. If they
disagree, that is itself an important finding and the clean batch is what gets
published. Either beats a limitations paragraph admitting a confound that could
not be measured.

---

## 6. Cross-cutting lessons

**A successful call that returns nothing is invisible to every error path a
client has.** No exception, no retry, HTTP 200, plausible-looking output. Three
separate bugs in this project had that shape: empty model replies, unknown
session ids, and dead-model sessions scored as clean. Each was found only
because something counted what actually happened per session rather than
trusting that no news was good news.

**Instrument the collection, not just the outcome.** The health counters were
added to support a step-budget experiment that turned out to be irrelevant. They
immediately found a bug that had been silently degrading every LLM session in
the study. That single line — `calls=30 rate_limited=0 stalls=20` — was worth
more than the experiment it was built for.

**Distinguish "the agent could not" from "the agent was not shown."** Two of
five conditions were recorded as capability failures caused entirely by a
listing cap and a hidden form. Any claim about agent capability requires first
proving the affordance was perceivable. `check_affordances.py` now does that,
and separates cap-truncation from `display:none` so the two are never conflated
again.

**Some confounds must be designed out, not analysed out.** No statistical
technique could separate collection time from harness identity in blocked data.
Interleaving fixed it; nothing else could have.

**Removing bad features can strengthen a result.** Dropping the length features
raised accuracy *and* pushed the transfer claim across the significance
threshold. The defensible version was also the better one.

**A confound you can measure is a footnote; one you cannot is a limitation.**
That distinction determines which problems are worth re-collecting to fix.

---

## 7. Checklist before any future batch

- [ ] `check_providers.py` — every model answers, in the real request shape
- [ ] `check_conditions.py` — corpus assumptions still hold
- [ ] `check_affordances.py` — every needed control is perceivable
- [ ] `CHARWEB_LLM_MODEL` set explicitly (never rely on a default)
- [ ] Keys pasted for real — check length, not just presence
- [ ] Fresh `run_id`; never pool across an instrument change
- [ ] `collection_health.csv` removed before the run, inspected after
- [ ] Interleaved, not blocked
- [ ] After collection: clock-only control, and export with `--health-csv`

### Settings that matter, and why

| Variable | Value | Why |
|---|---|---|
| `CHARWEB_MAX_TOKENS` | 2500 | 900 still truncated reasoning models |
| `CHARWEB_MAX_ELEMENTS` | 120 | 30 hid 68 of 98 controls on `/explore` |
| `CHARWEB_MAX_STEPS` | 30 | 10 could not fit register + task |
| `CHARWEB_MAX_STALLS` | 6 | 2 quits on a normal transient blip |

Any change to the top two alters what the agent perceives or produces, and
therefore how it behaves. **Data collected under different values is not
comparable and must use its own `run_id`.**
