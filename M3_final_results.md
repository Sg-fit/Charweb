# M3 Final Results — harness axis, model axis, and the controls behind them

Status 2026-08-31. Supersedes `M3_results.md`, `M3_LOTO_results.md`, and all
earlier model-axis numbers in this file's own history.

---

## 1. Headline findings

**The scaffolding and the model are both fingerprintable, and both survive a
task the detector has never seen.**

| Axis | In-distribution | Unseen task (LOTO) | Chance | n |
|---|---|---|---|---|
| **Harness** (4 classes, interleaved batch) | **1.000 ± 0.000** | **0.988** | 0.250 | 80 |
| **Harness** (6 classes, full corpus) | **0.950 ± 0.039** | — | 0.167 | 727 |
| **Model** (2 models, certified batch) | **0.936**, p=0.005 | **0.896**, p=0.005 | 0.500 | 35 |
| Attribution without the client clock | server **0.948** vs client **0.950** | — | 0.167 | 727 |

The model axis is measured on run `modelaxis_v3`, restricted to sessions that
are **individually certified clean** — present in the collection health file,
zero empty replies, zero stalls, model confirmed alive. That is the strongest
evidential basis in the project; see §4 for why the earlier, larger batches are
not preferable despite their size.

## 2. Model axis — the certified result

`research/m3_model_axis.py`, 200 label shuffles, length-free feature set (26
features), `meta/llama-3.2-11b-vision-instruct` (19) vs `openai/gpt-oss-20b` (16).

```
in-distribution   0.936  (chance 0.500)   p = 0.0050
unseen task       0.896  (chance 0.500)   p = 0.0050
```

Both sit at the permutation floor (1/201) and clear a Bonferroni correction for
two tests. Confusion matrix — **one error in 35 sessions**:

|  | llama | gpt-oss |
|---|---|---|
| **llama** (recall 0.947) | 18 | 1 |
| **gpt-oss** (recall 1.000) | 0 | 16 |

The transfer result barely drops from in-distribution (0.936 → 0.896). Earlier
batches showed a much steeper fall, which suggested the classifier was partly
learning task structure. On clean data it is not: the fingerprint is
task-independent.

### Feature groups

```
timing only         0.883  (11 features)     ALL minus timing   0.888
action only         0.790  ( 8 features)     ALL                0.936
struct only         0.698  ( 3 features)
geom   only         0.917  ( 4 features)
```

Two things worth stating. Removing every timing feature costs 0.048, so the
axis is **not** reducible to how fast the provider answered. And **mouse
geometry alone reaches 0.917 on four features** — these two models move a
cursor differently enough to be told apart almost as well as the full feature
set. That is a concrete, reportable mechanism rather than "the classifier finds
something."

## 3. Controls — four confounds, all closed

### 3.1 Batch / temporal
Collection interleaves arms one session at a time, reshuffling order each round.
Training on collection time alone:

| | Harness | Model (certified) |
|---|---|---|
| clock-only | 0.090 (chance 0.250) | **0.431** (chance 0.500) |
| behavioural | 1.000 | 0.936 |

The model-axis clock score is *below* chance — what a genuinely uninformative
feature does under cross-validation. Compare the pre-interleaving baseline,
where clock-only reached 0.751 on the harness axis.

### 3.2 Provider latency
See the feature-group table above: 0.888 without any timing feature.

### 3.3 Session length (rate limiting)
Length differs by model (Kruskal p=0.0006 on the earlier batch), so it was
tested rather than assumed away. Removing the three length features and
matching on length both *raised* accuracy. The length-free feature set is used
throughout; `struct_n_events`, `struct_duration_s`, and `timing_rate` are
excluded.

### 3.4 Action timeouts
Suspected because the agent selects elements by an index that goes stale after
navigation, and a stale click waits 30s. Measured as the fraction of
inter-event gaps in [27s, 36s]: `fenris` 0.069, `llm_driven` 0.002,
`playwright` 0.002, all three scripted harnesses **exactly 0.000**. Tiny for
`llm_driven`, but a perfect giveaway where it occurs. Settled by deletion:
harness attribution is 0.950 with all features and 0.947 without any timing
feature, so the leak is not load-bearing.

## 4. Why the certified 35 beats the uncertified 73

The uncertified three-model batch scored 0.893, and an earlier one scored 0.637.
Neither is preferable, for a reason that only became visible once per-session
health was recorded:

```
deepseek-v4-pro    steps_used  3.455   stalls 1.455   clean  3/11
llama-3.2-11b      steps_used 26.000   stalls 0.000   clean 19/19
gpt-oss-20b        steps_used 25.100   stalls 0.050   clean 16/20
```

Deepseek sessions were **3-step stubs** — throttled to exhaustion — while the
other two ran 26 steps. Deepseek then scored the *highest* per-model recall
(0.900) in that batch. A classifier separating "3 events" from "26 events" is
reading which model the provider throttles, not how the model browses.

**Deepseek is excluded.** It was throttled to unusable session lengths in two
independent batches on the same NVIDIA key. That is a collection-feasibility
limitation of the infrastructure, not a statement about the model, and it should
be reported as such.

## 5. Robustness

Harness attribution moves ≤0.05 under every perturbation tested — repeated CV,
classifier choice, class balancing, volume-feature drop, thin-cell drop, LOMO
bootstrap CI — and improves under class balancing. Chance is 1/k throughout,
never the majority-class rate.

## 6. Dataset

- Full corpus: 727 labelled sessions, ~40k events, 6 harness classes.
- Harness axis (interleaved): run `interleaved_20260827_040738`, 80 sessions.
- Model axis (certified): run `modelaxis_v3`, 35 of 60 sessions surviving
  certification, 2 models × 5 conditions.
- Collection settings for the certified batch: `CHARWEB_MAX_TOKENS=2500`,
  `CHARWEB_MAX_ELEMENTS=120`, `CHARWEB_MAX_STEPS=30`.

## 7. Limitations to state plainly

1. **Two models, not three.** Deepseek could not be collected reliably; chance
   is therefore 0.500 rather than 0.333.
2. **n=35 for the model axis.** Small, but every session is certified and both
   p-values are at the permutation floor.
3. **One website.** Every session is Charweb. Cross-site generalisation is
   untested and is the largest remaining external-validity gap.
4. **Instrument changes between batches.** The certified batch differs from
   earlier ones in token budget, element cap, step budget, and instruction
   wording. Batches must not be pooled, and the old-vs-new delta is not
   attributable to any single change.
5. **The perception layer bounded agent behaviour.** Until fixed, a 30-element
   listing cap hid up to 68 of 98 controls, and two instruction conditions
   described controls that did not exist on the page the agent was sent to. Any
   claim about agent *capability* from data collected before that fix is
   unsupportable.
