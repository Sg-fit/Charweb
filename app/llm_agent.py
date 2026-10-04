#!/usr/bin/env python3
"""Genuine LLM-in-the-loop browsing agent for Charweb.

Unlike the scripted harnesses (gemini.py, grok.py, ai_agent.py, ...), here a
language model chooses EACH action live from the current page state. This is
what creates the study's *model axis*: run the same harness under different
models (Gemini vs a Groq-hosted Llama vs GPT) and the differences are genuine
model behaviour, not scripted style. Running a model here vs through Fenris
gives the *harness axis*.

Backends are OpenAI-compatible, so one code path talks to all of them -- pick
with env vars. No API key is ever stored in the repo; pass it at runtime.

    # free Gemini (get a key at https://aistudio.google.com/apikey):
    CHARWEB_LLM_PROVIDER=gemini  CHARWEB_LLM_KEY=AIza...  \
    CHARWEB_LLM_MODEL=gemini-2.0-flash  CHARWEB_INSTRUCTION=free_explore \
    CHARWEB_RUN_ID=run_a \
    python app/llm_agent.py --username llm_gem_a --url https://charweb.net --headless

Requires:  pip install openai playwright   &&   playwright install chromium
"""
import argparse
import json
import os
import random
import re
import sys
import time

# This file lives in app/, which also contains modules named email.py, code.py,
# etc. Python puts a script's own directory first on sys.path, so those shadow
# the standard-library modules that third-party packages import (openai ->
# httpx -> importlib.metadata -> the stdlib 'email' package). Remove our own
# directory from sys.path before importing anything third-party, so the real
# stdlib wins.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != _HERE]
# ...but the repo root must stay importable, or `instructions` (the shared
# instruction conditions) cannot be found once app/ itself is off the path.
# The root holds no stdlib-shadowing names, so adding it is safe.
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout


def build_run_headers():
    """Attribution labels from CHARWEB_* env vars, sent as X-* headers so the
    server records harness/model/instruction/run_id on the session (see
    Charweb routes.track). Empty dict if none set (a valid no-op)."""
    env_to_hdr = {
        "CHARWEB_RUN_ID": "X-Run-Id",
        "CHARWEB_HARNESS": "X-Harness",
        "CHARWEB_MODEL": "X-Model",
        "CHARWEB_INSTRUCTION": "X-Instruction",
        "CHARWEB_ADV_CONDITION": "X-Adv-Condition",
        "CHARWEB_MIMICRY_TARGET": "X-Mimicry-Target",
    }
    return {h: os.environ[e] for e, h in env_to_hdr.items() if os.environ.get(e)}


# NOTE: duplicates app/run_labels.py. llm_agent.py is imported as a module by
# perception_agent.py (as LA), which calls LA.apply_run_context, so the helper
# must exist in THIS namespace. Kept local rather than imported to avoid the
# sys.path juggling scripted_agent.py needs; run_labels.py is the source of
# truth if the two ever diverge.
def build_run_cookies(url):
    """Same labels as cookies. Headers are not attached to sendBeacon and are
    unreliable on the page-teardown keepalive flush, so a fast-navigating agent
    whose events all flush on page-hide would be recorded unlabelled."""
    env_to_cookie = {
        "CHARWEB_RUN_ID": "cw_run_id",
        "CHARWEB_HARNESS": "cw_harness",
        "CHARWEB_MODEL": "cw_model",
        "CHARWEB_INSTRUCTION": "cw_instruction",
        "CHARWEB_ADV_CONDITION": "cw_adv_condition",
        "CHARWEB_MIMICRY_TARGET": "cw_mimicry_target",
    }
    return [{"name": c, "value": os.environ[e], "url": url}
            for e, c in env_to_cookie.items() if os.environ.get(e)]


def apply_run_context(context, url=None):
    """Attach attribution labels and, when armed, the Phase-2 tamper layer.

    CHARWEB_TAMPER is injected with add_init_script so window.__CHARWEB_TAMPER
    is defined BEFORE track.js parses. Unset during clean collection, so the
    control group is untouched by construction.
    """
    context.set_extra_http_headers(build_run_headers())
    target = url or os.environ.get("CHARWEB_URL", "https://charweb.net")
    cookies = build_run_cookies(target)
    if cookies:
        try:
            context.add_cookies(cookies)
        except Exception as e:
            print(f"[llm_agent] cookie labels skipped: {e}")
    mode = os.environ.get("CHARWEB_TAMPER")
    if mode:
        context.add_init_script(f"window.__CHARWEB_TAMPER = {json.dumps(mode)};")
        print(f"[llm_agent] TAMPER ACTIVE: {mode}")
    return context

# OpenAI-compatible base URLs. Override with CHARWEB_LLM_BASE_URL if needed.
PROVIDERS = {
    "gemini":     "https://generativelanguage.googleapis.com/v1beta/openai/",
    "groq":       "https://api.groq.com/openai/v1",
    "cerebras":   "https://api.cerebras.ai/v1",
    "nvidia":     "https://integrate.api.nvidia.com/v1",     # free, no daily cap, 40 RPM
    "mistral":    "https://api.mistral.ai/v1",               # free ~1B tokens/month
    "openrouter": "https://openrouter.ai/api/v1",
    "openai":     "https://api.openai.com/v1",
}
DEFAULT_MODEL = {
    "gemini":     "gemini-2.0-flash",
    "groq":       "openai/gpt-oss-20b",
    "cerebras":   "llama-3.3-70b",
    # was meta/llama-3.3-70b-instruct until it hit end-of-life and started
    # returning 410 Gone. A dead default is worse than no default: forgetting
    # CHARWEB_LLM_MODEL silently produces label-only sessions instead of an
    # error. gpt-oss-20b is verified working by research/check_providers.py.
    "nvidia":     "openai/gpt-oss-20b",
    "mistral":    "mistral-small-latest",
    "openrouter": "meta-llama/llama-3.3-70b-instruct",
    "openai":     "gpt-4o-mini",
}

# Instruction conditions live in instructions.py (repo root) -- single source
# shared with run_fenris.py, so a condition cannot mean two different things
# depending on which harness ran it.
from instructions import for_llm_agent, CONDITIONS as INSTRUCTIONS

ACTION_SPEC = """\
Reply with ONE JSON object and nothing else (no markdown fences). Schema:
{"reasoning":"<one short sentence>",
 "action":"click|type|scroll|goto|wait|done",
 "index":<int, the [index] of the element for click/type>,
 "text":"<text to type, for type>",
 "url":"<path like /home, for goto>"}
Pick an element only by an [index] shown in the list. After 'type' on a search
or form field, Enter is pressed automatically. Use 'done' when your task/browse
is complete. Never use an index that is not in the list."""

# How many elements the agent is shown. 30 was a silent task-killer: on
# /explore, 98 elements exist and 68 were never listed, so controls the task
# needed simply did not exist as far as the model was concerned.
#
# Raised to 120 on 2026-08-28. At that cap nothing is truncated on any Charweb
# page (explore lists 66 of 98; the other 32 are genuinely display:none comment
# forms that only exist after their toggle is clicked).
#
# IMPORTANT: this changes what the agent can perceive, and therefore how it
# behaves. Data collected at 120 is NOT comparable with the Phase 1 corpus,
# which was collected at 30, and must use its own run_id. Set
# CHARWEB_MAX_ELEMENTS=30 to reproduce Phase 1 collection exactly.
MAX_ELEMENTS = int(os.environ.get("CHARWEB_MAX_ELEMENTS", "120"))

EXTRACT_JS = """
() => {
  const SEL = 'a,button,input,textarea,select,[role=button]';
  const all = [...document.querySelectorAll(SEL)];
  const vis = all.filter(el => {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return false;
    const st = window.getComputedStyle(el);
    return st.visibility !== 'hidden' && st.display !== 'none';
  });
  // Order matters once a cap bites. A feed is mostly links, so a naive
  // document-order listing spends the whole budget on navigation and drops the
  // form controls -- the only elements that can actually complete a task.
  // Rank interactive controls first, links last, stable within each group.
  const rank = el => {
    const t = el.tagName.toLowerCase();
    if (t === 'textarea' || t === 'select') return 0;
    if (t === 'input') {
      const ty = (el.getAttribute('type') || 'text').toLowerCase();
      return (ty === 'hidden') ? 9 : 0;
    }
    if (t === 'button' || el.getAttribute('role') === 'button') return 1;
    return 2;                                   // plain links
  };
  vis.sort((a, b) => rank(a) - rank(b));
  for (const e of document.querySelectorAll('[data-llm-idx]'))
    e.removeAttribute('data-llm-idx');
  const out = []; let i = 0;
  for (const el of vis) {
    const label = (el.innerText || el.value || el.getAttribute('placeholder') ||
                   el.getAttribute('name') || el.getAttribute('aria-label') || '')
                  .trim().replace(/\\s+/g,' ').slice(0,80);
    el.setAttribute('data-llm-idx', i);
    out.push({index:i, tag:el.tagName.toLowerCase(),
              type:el.getAttribute('type')||'', name:el.getAttribute('name')||'',
              href:(el.getAttribute('href')||'').slice(0,60), text:label});
    i++;
    if (i >= __MAX_ELEMENTS__) break;
  }
  return out;
}
""".replace("__MAX_ELEMENTS__", str(MAX_ELEMENTS))


def parse_args():
    p = argparse.ArgumentParser(description="LLM-in-the-loop Charweb agent")
    p.add_argument("--username", required=True)
    p.add_argument("--url", default="https://charweb.net")
    # Default to whatever the machine can actually do: a headless server has no
    # DISPLAY, and launching headed there fails with an X-server error that
    # looks nothing like "you forgot a flag". Explicit --headless/--headed still
    # win, so a desktop run is unchanged.
    p.add_argument("--headless", action="store_true",
                   default=not os.environ.get("DISPLAY"))
    p.add_argument("--headed", dest="headless", action="store_false")
    p.add_argument("--steps", type=int, default=int(os.environ.get("CHARWEB_MAX_STEPS", "18")))
    return p.parse_args()


def llm_client():
    provider = os.environ.get("CHARWEB_LLM_PROVIDER", "gemini").lower()
    base_url = os.environ.get("CHARWEB_LLM_BASE_URL") or PROVIDERS.get(provider)
    model = os.environ.get("CHARWEB_LLM_MODEL") or DEFAULT_MODEL.get(provider, "gpt-4o-mini")
    key = os.environ.get("CHARWEB_LLM_KEY") or os.environ.get("OPENAI_API_KEY")
    if not base_url or not key:
        # Say which one is actually missing and what the valid values are. The
        # old message named neither, so an unset key and an unknown provider
        # produced the same text -- which is exactly how a batch run can lose
        # every LLM session without it being obvious why.
        problems = []
        if not base_url:
            problems.append(
                f"CHARWEB_LLM_PROVIDER={provider!r} is not a known provider. "
                f"Valid: {', '.join(sorted(PROVIDERS))}. "
                f"(Or set CHARWEB_LLM_BASE_URL directly.)")
        if not key:
            problems.append(
                "CHARWEB_LLM_KEY is empty or unset (OPENAI_API_KEY also works). "
                "If you exported it in another shell or before an `ssh`, it is "
                "gone -- export it in the same shell that launches this.")
        raise SystemExit("LLM config incomplete:\n  - " + "\n  - ".join(problems))
    from openai import OpenAI
    # Tight per-request timeout so a slow/hung provider call fails in 45s and the
    # agent moves on, instead of stalling until run_grid's 900s kill. Our own
    # loop handles retries, so disable the SDK's internal retries.
    return OpenAI(base_url=base_url, api_key=key, timeout=90, max_retries=0), model, provider


# Sentinel telling the caller a failure is PERMANENT (retired model, rejected
# key) rather than transient (rate limit, timeout). Both used to surface as
# None, so sustained rate limiting on a free tier looked identical to a dead
# model -- and batch runners would disable a perfectly good arm because of it.
PERMANENT_FAILURE = object()

# Per-session collection health. Rate limiting does not merely slow a session
# down -- it truncates it, and a truncated session is stored with a full label
# and almost no behaviour. That shifts every feature at once and is invisible
# after the fact unless counted at collection time. Printed in the end-of-session
# summary so a batch's logs can be graded, and so a later analysis can control
# for stalls the way §3.3 of the M3 write-up controls for session length.
STATS = {"calls": 0, "rate_limited": 0, "transient": 0, "stalls": 0,
         "empty": 0, "recovered": 0, "action_timeouts": 0}

# Reasoning models spend this budget on chain-of-thought before the visible
# answer begins, so a value tuned for a plain chat model returns empty content
# on a 200 OK. 300 was that value, and it cost roughly two thirds of the
# actions in every gpt-oss session.
# 900 was still short: gpt-oss-20b emits ~3900 characters (~1000 tokens) of
# reasoning alone before its answer starts. The budget has to cover reasoning
# AND the answer, or the call succeeds and returns nothing.
MAX_TOKENS = int(os.environ.get("CHARWEB_MAX_TOKENS", "2500"))

# Where per-session collection health is appended. The log line alone cannot be
# joined to the data: it names no session. Writing session_uid + counters here
# is what makes "exclude every degraded session" possible at analysis time
# instead of hoping the degradation averaged out.
HEALTH_CSV = os.environ.get("CHARWEB_HEALTH_CSV", "collection_health.csv")


# ---------------------------------------------------------------------------
# Phase 2 / E4: behavioural mimicry.
#
# E3 established that forging TELEMETRY does not move model attribution -- the
# classifier falls back on what the agent DOES. So mimicry has to change
# behaviour, which is why these knobs act on the harness's real timing and on
# the instruction, never on the reported values.
#
# Targets are per-model medians of the Phase-1 certified clean data
# (modelaxis_v5.csv, n=86):
#
#                        llama-3.2-11b   gpt-oss-20b
#   timing_kd_mean            485 ms        1941 ms
#   timing_think_mean        5676 ms        9678 ms
#   action_pageload_pct        9.47 %        18.18 %
#   struct_events_per_url      18.94          15.08
#
# CRITICAL ASYMMETRY (H7): a harness can always ADD delay, never remove it.
# llama can slow to gpt-oss's latency profile; gpt-oss cannot speed up to
# llama's, because that wait is inference, not policy. Mimicry of
# latency-carried features should therefore work in ONE DIRECTION ONLY. The
# gpt-oss -> llama arm is collected anyway and is expected to fail on timing
# while still moving navigation. That failure is the result, not a bug -- do
# not "fix" it by faking timestamps, which would just make it E3 again.
MIMIC_PROFILES = {
    "openai/gpt-oss-20b": {
        "think_ms": 4000, "type_ms": (300, 700),
        # pageload_pct 18.18 vs 9.47 -> navigate roughly twice as often.
        # Phrased as guidance, not a harness override: navigation is a decision
        # the agent makes, and forcing it would be imitation of the telemetry
        # rather than of the behaviour.
        "nav_hint": ("Work in short visits: after about 8 actions on a page, "
                     "move to a different page rather than continuing there."),
    },
    "meta/llama-3.2-11b-vision-instruct": {
        "think_ms": 0, "type_ms": (40, 130),
        "nav_hint": ("Work in long visits: stay on a page and do as much as you "
                     "can there before navigating elsewhere."),
    },
}
MIMIC_TARGET = os.environ.get("CHARWEB_MIMICRY_TARGET") or None
_MIMIC = MIMIC_PROFILES.get(MIMIC_TARGET, {}) if MIMIC_TARGET else {}
MIMIC_THINK_MS = int(_MIMIC.get("think_ms", 0))
MIMIC_TYPE_MS = tuple(_MIMIC.get("type_ms", (40, 130)))
MIMIC_NAV_HINT = _MIMIC.get("nav_hint", "")
if MIMIC_TARGET:
    print(f"[llm_agent] MIMICRY TARGET: {MIMIC_TARGET} "
          f"(+{MIMIC_THINK_MS}ms think, type {MIMIC_TYPE_MS[0]}-{MIMIC_TYPE_MS[1]}ms/char)")

# Minimum seconds between model calls. Providers meter per minute (NVIDIA is
# 40 RPM), and an unpaced agent fires as fast as the site responds, so a LONGER
# session is more likely to hit the limit than a short one -- which is why
# raising the step cap made task success worse rather than better.
#
# Default 0 (off): a nonzero value adds a constant to the gap between actions,
# which is exactly where the timing features live. Turning it on changes the
# behavioural distribution and its sessions must NOT be pooled with data
# collected without it.
MIN_CALL_INTERVAL = float(os.environ.get("CHARWEB_MIN_CALL_INTERVAL_S", "0"))
_last_call = [0.0]

# Exhausted retry cycles tolerated before ending the session. Default 2 keeps
# Phase 1 behaviour byte-identical; Phase 2 collection should raise it.
MAX_STALLS = int(os.environ.get("CHARWEB_MAX_STALLS", "2"))


def session_uid_from_cookies(context):
    """Pull Charweb's session_uid out of the Flask session cookie.

    The server keeps session_uid in the signed Flask session (routes.track),
    so it is never sent to the client as its own cookie -- but the signed
    cookie's PAYLOAD is plain base64 JSON, and reading it needs no secret key
    (only forging one would). Signature and expiry are ignored on purpose:
    this is our own session, read for labelling, not authentication.
    """
    import base64
    import json as _json
    import zlib
    cookies = context.cookies()
    # The cookie name is configurable (SESSION_COOKIE_NAME), so try the default
    # first and then anything that decodes -- rather than assuming "session".
    names = [c["name"] for c in cookies]
    ordered = (["session"] if "session" in names else []) + \
              [n for n in names if n != "session"]
    for name in ordered:
        raw = next((c["value"] for c in cookies if c["name"] == name), None)
        if not raw:
            continue
        try:
            # itsdangerous format: [.]<b64 payload>.<b64 timestamp>.<b64 sig>
            # A LEADING DOT marks a zlib-compressed payload -- not a dash. With
            # the wrong marker, split(".")[0] on a compressed cookie returns ""
            # and every session comes back UNKNOWN, which is exactly what
            # happened to run clean_v2.
            compressed = raw.startswith(".")
            payload = (raw[1:] if compressed else raw).split(".")[0]
            if not payload:
                continue
            data = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
            if compressed:
                data = zlib.decompress(data)
            uid = _json.loads(data).get("session_uid")
            if uid:
                return uid
        except Exception:
            continue
    return None


def write_health(session_uid, args, model, used, model_dead=False):
    """Append one row per session so the exporter can drop degraded ones."""
    import csv as _csv
    empty_rate = STATS["empty"] / STATS["calls"] if STATS["calls"] else 0.0
    row = {
        "session_uid": session_uid or "",
        "run_id": os.environ.get("CHARWEB_RUN_ID", ""),
        "harness": "llm_driven",
        "model": model,
        "instruction_condition": os.environ.get("CHARWEB_INSTRUCTION", ""),
        "steps_used": used, "steps_budget": args.steps,
        "calls": STATS["calls"], "rate_limited": STATS["rate_limited"],
        "transient": STATS["transient"], "empty": STATS["empty"],
        "action_timeouts": STATS["action_timeouts"],
        "recovered": STATS["recovered"], "stalls": STATS["stalls"],
        "empty_rate": f"{empty_rate:.3f}",
        "max_tokens": MAX_TOKENS,
        # A retired model or rejected key kills the session after one call. The
        # counters above stay at zero because nothing was retried and nothing
        # came back empty -- so without this flag such a session scores as
        # perfectly clean, which is the opposite of the truth. Found exactly
        # that way: a fallback to a 410-Gone model logged clean=1 on 1 step.
        "model_dead": int(bool(model_dead)),
        # One column the analysis can filter on directly. A session is clean
        # when the model answered every time it was asked; anything else means
        # its behaviour was shaped by the plumbing as well as by the model.
        "clean": int(STATS["empty"] == 0 and STATS["stalls"] == 0
                     and STATS["rate_limited"] == 0
                     # 503 ResourceExhausted retries sleep 5s and land inside
                     # inter-event gaps exactly like 429 retries do. Omitting
                     # this certified poolside/laguna-xs as clean while 13 of
                     # its 43 calls were retries.
                     and STATS["transient"] == 0
                     and STATS["action_timeouts"] <= 0.05 * max(used, 1)
                     and not model_dead),
    }
    try:
        # A header is written once and never revisited, so when this dict
        # gains a field every later row silently misaligns against it --
        # DictReader then maps values onto the WRONG column names. That has now
        # happened three times in this project (transient, unparseable->clean,
        # action_timeouts) and each time it produced a confidently wrong
        # certification. Validate the header, or start a new file.
        new = not os.path.exists(HEALTH_CSV)
        if not new:
            with open(HEALTH_CSV, newline="", encoding="utf-8") as fh:
                hdr = next(_csv.reader(fh), [])
            if hdr != list(row):
                import time as _t
                alt = (HEALTH_CSV.rsplit(".", 1)[0]
                       + f".schema{int(_t.time())}.csv")
                print(f"[llm_agent] HEALTH SCHEMA CHANGED -- {HEALTH_CSV} "
                      f"header does not match this row. Writing {alt} instead.")
                HEALTH_TARGET, new = alt, True
            else:
                HEALTH_TARGET = HEALTH_CSV
        else:
            HEALTH_TARGET = HEALTH_CSV
        with open(HEALTH_TARGET, "a", newline="", encoding="utf-8") as fh:
            w = _csv.DictWriter(fh, fieldnames=list(row))
            if new:
                w.writeheader()
            w.writerow(row)
    except Exception as e:
        print(f"[llm_agent] could not write health row: {str(e)[:90]}")
    if not session_uid:
        print("[llm_agent] WARNING: session_uid unavailable; this session "
              "cannot be excluded by health later.")


def ask_model(client, model, instruction, url, elements, history):
    system = instruction + "\n\n" + ACTION_SPEC
    listing = "\n".join(
        f'[{e["index"]}] <{e["tag"]}{"/"+e["type"] if e["type"] else ""}> '
        f'{e["text"] or e["name"] or e["href"]}'
        for e in elements) or "(no interactive elements found)"
    user = (f"Current page: {url}\n\nInteractive elements:\n{listing}\n\n"
            f"Recent actions: {history[-4:] if history else 'none'}\n\n"
            "What is your next action? Reply with the JSON object only.")
    for attempt in range(6):
        try:
            if MIN_CALL_INTERVAL:
                wait = MIN_CALL_INTERVAL - (time.time() - _last_call[0])
                if wait > 0:
                    time.sleep(wait)
            _last_call[0] = time.time()
            STATS["calls"] += 1
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": user}],
                temperature=0.7, max_tokens=MAX_TOKENS)
            choice = resp.choices[0]
            content = choice.message.content

            # A reasoning model (gpt-oss, deepseek-r*, o*) emits its chain of
            # thought first and the visible answer after. With a small
            # max_tokens the budget is spent before the answer starts, so the
            # call SUCCEEDS and content comes back empty. That is not an error
            # anywhere in the SDK -- no exception, no retry, HTTP 200 -- so it
            # used to surface as an unexplained "model busy" stall with zero
            # rate-limit and zero transient counts. Handle it explicitly.
            if not (content or "").strip():
                STATS["empty"] += 1
                fr = getattr(choice, "finish_reason", None)
                # Some providers park the answer here when content is empty.
                reasoning = (getattr(choice.message, "reasoning_content", None)
                             or getattr(choice.message, "reasoning", None) or "")
                if STATS["empty"] == 1:
                    print(f"[llm_agent] EMPTY content from model "
                          f"(finish_reason={fr!r}, reasoning_chars={len(reasoning)}, "
                          f"max_tokens={MAX_TOKENS}). This is a truncated "
                          f"reasoning reply, not a rate limit -- raise "
                          f"CHARWEB_MAX_TOKENS if it repeats.")
                # Only salvage reasoning text that actually contains a JSON
                # object. Returning raw chain-of-thought prose just moves the
                # failure one step later ("unparseable model reply; skipping")
                # and still burns the step -- worse than admitting the miss,
                # because it looks like the model answered badly rather than
                # not at all.
                if reasoning.strip() and re.search(r"\{[^{}]*\"action\"", reasoning):
                    STATS["recovered"] += 1
                    return reasoning
                if fr == "length" and attempt < 2:
                    continue          # same call, another shot at finishing
                return None
            return content
        except Exception as e:
            msg = str(e)
            low = msg.lower()
            is_rate = "429" in msg or "resource_exhausted" in low or "rate limit" in low
            is_transient = ("timed out" in low or "timeout" in low or "connection" in low
                            or "503" in msg or "502" in msg or "500" in msg
                            or "service unavailable" in low or "unavailable" in low
                            or "resourceexhausted" in low or "overloaded" in low
                            or "temporarily" in low)
            if not (is_rate or is_transient):
                print(f"[llm_agent] model error: {msg[:160]}")
                # PERMANENT: a retired model (410), a rejected key (403), an
                # unknown model (404). Retrying cannot help and every later
                # session on this model would fail the same way.
                return PERMANENT_FAILURE
            if is_rate and any(k in low for k in ("per day", "requests per day",
                                                  "tokens per day", " rpd", " tpd", "daily")):
                print("[llm_agent] DAILY free-tier quota reached for this key/model — "
                      "waiting won't help. Resume after it resets, or switch provider/key.")
                raise SystemExit(3)     # stop cleanly; don't spin all night
            if is_rate:
                mm = re.search(r"(?:try again|retry) in\s*(?:([0-9.]+)m)?\s*([0-9.]+)", low)
                if mm:
                    delay = float(mm.group(1) or 0) * 60 + float(mm.group(2))
                else:
                    m2 = re.search(r"([0-9.]+)\s*s", low)
                    delay = float(m2.group(1)) if m2 else 30
                delay = min(delay + 1, 90)
                kind = "rate limited"
                STATS["rate_limited"] += 1
            else:
                delay = 5              # transient slow / timeout: quick retry
                kind = "slow/timeout"
                STATS["transient"] += 1
            # Show the actual provider message on the first attempt. Printing
            # only the category ("rate limited" / "slow/timeout") hides whether
            # this is a 429, a connection reset, or a read timeout -- which are
            # three different problems with three different fixes.
            detail = f" :: {msg[:120]}" if attempt == 0 else ""
            print(f"[llm_agent] {kind}; retry in {delay:.0f}s "
                  f"(attempt {attempt+1}/6){detail}")
            time.sleep(delay)
    # TRANSIENT, exhausted: the backend is alive but busy. End this session,
    # but do NOT let the caller conclude the model is dead -- on a free tier,
    # sustained rate limiting is normal and the next session may well succeed.
    return None


def parse_action(raw):
    if not raw:
        return None
    s = raw.strip()
    if s.startswith("```"):
        s = s.strip("`")
        s = s[s.find("{"):]
    a, b = s.find("{"), s.rfind("}")
    if a == -1 or b == -1:
        return None
    try:
        return json.loads(s[a:b + 1])
    except json.JSONDecodeError:
        return None


def human_move_click(page, loc):
    """Move the pointer to the element before clicking, so mouse-kinematic
    features get real data rather than a teleported click."""
    try:
        box = loc.bounding_box()
        if box:
            page.mouse.move(box["x"] + box["width"] / 2 + random.uniform(-4, 4),
                            box["y"] + box["height"] / 2 + random.uniform(-4, 4),
                            steps=random.randint(4, 12))
            time.sleep(random.uniform(0.1, 0.4))
    except Exception:
        pass
    loc.click(timeout=5000)


def do_action(page, act, site_root):
    a = (act or {}).get("action")
    if a == "click":
        human_move_click(page, page.locator(f'[data-llm-idx="{act["index"]}"]').first)
    elif a == "type":
        loc = page.locator(f'[data-llm-idx="{act["index"]}"]').first
        human_move_click(page, loc)
        try:
            loc.fill("")
        except Exception:
            pass
        for ch in str(act.get("text", "")):
            loc.type(ch, delay=random.randint(*MIMIC_TYPE_MS))
        try:
            loc.press("Enter")
        except Exception:
            pass
    elif a == "scroll":
        page.mouse.wheel(0, random.randint(300, 700))
    elif a == "goto":
        u = act.get("url", "") or "/"
        page.goto(u if u.startswith("http") else site_root + u,
                  wait_until="domcontentloaded")
    elif a == "wait":
        time.sleep(random.uniform(1.0, 2.5))


def _submit_form(page, anchor_selector):
    """Submit the form that CONTAINS anchor_selector, not whatever generic
    submit button happens to be first in the DOM (e.g. a nav search button).
    Clicks that form's own submit; falls back to pressing Enter in the field."""
    try:
        form = page.locator(f"form:has({anchor_selector})").first
        btn = form.locator("input[type=submit], button[type=submit]")
        if btn.count():
            btn.first.click(timeout=5000)
        else:
            page.locator(anchor_selector).first.press("Enter")
        page.wait_for_load_state("domcontentloaded")
    except Exception:
        try:
            page.locator(anchor_selector).first.press("Enter")
            page.wait_for_load_state("domcontentloaded")
        except Exception:
            pass


def scripted_signup(page, site_root, username):
    """Deterministic account creation + login (constant across all sessions, so
    not part of the behavioural signature). The LLM drives everything after."""
    pw = "AgentPass123!"
    page.goto(f"{site_root}/register", wait_until="domcontentloaded")
    for sel, val in [("input[name='username']", username),
                     ("input[name='email']", f"{username}@example.com"),
                     ("input[name='password']", pw),
                     ("input[name='password2']", pw)]:
        try:
            page.fill(sel, val)
        except Exception:
            pass
    for sel in ("input[name='accept_terms']", "input[name='remember_me']"):
        try:
            if page.locator(sel).count():
                page.check(sel)
        except Exception:
            pass
    _submit_form(page, "input[name='password2']")   # submit the REGISTER form
    time.sleep(1.0)
    page.goto(f"{site_root}/login", wait_until="domcontentloaded")
    try:
        page.fill("input[name='username']", username)
        page.fill("input[name='password']", pw)
    except Exception:
        pass
    _submit_form(page, "input[name='password']")     # submit the LOGIN form
    time.sleep(1.0)
    # Verify: a logged-out session redirects /edit_profile back to /login.
    page.goto(f"{site_root}/edit_profile", wait_until="domcontentloaded")
    logged_in = "/login" not in page.url
    print(f"[llm_agent] account={username} logged_in={logged_in} (url={page.url})")
    page.goto(f"{site_root}/", wait_until="domcontentloaded")
    return logged_in


def run():
    args = parse_args()
    site_root = args.url.rstrip("/")
    client, model, provider = llm_client()

    instr_name = os.environ.get("CHARWEB_INSTRUCTION", "free_explore")
    instruction = for_llm_agent(instr_name)
    if MIMIC_NAV_HINT:
        # Appended, not substituted: the TASK must stay identical to the clean
        # arm or the comparison measures a different task rather than mimicry.
        instruction = instruction + "\n\n" + MIMIC_NAV_HINT

    # Force the labels this harness owns, so a stale CHARWEB_HARNESS/CHARWEB_MODEL
    # left in the shell from a previous run (PowerShell keeps $env: between
    # commands) can't mislabel this session. This run IS llm_driven on `model`.
    os.environ["CHARWEB_HARNESS"] = "llm_driven"
    os.environ["CHARWEB_MODEL"] = model
    os.environ.setdefault("CHARWEB_INSTRUCTION", instr_name)

    print(f"[llm_agent] provider={provider} model={model} instruction={instr_name} "
          f"user={args.username} steps={args.steps}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        apply_run_context(context)
        page = context.new_page()

        # Unique account per run so registration never collides with a prior run.
        account = f"{args.username}_{os.urandom(3).hex()}"
        if not scripted_signup(page, site_root, account):
            print("[llm_agent] WARNING: could not confirm login; the agent will "
                  "browse logged-out. Check the register/login form selectors.")

        history = []
        fails = 0
        # A session that registers and logs in but then gets nothing from the
        # model is NOT an llm_driven session -- it is a login with an
        # llm_driven label on it. Left as a success it silently poisons the
        # model axis with empty episodes, so track it and exit non-zero below.
        model_dead = False
        for step in range(args.steps):
            try:
                elements = page.evaluate(EXTRACT_JS)
            except Exception:
                elements = []
            raw = ask_model(client, model, instruction, page.url, elements, history)
            if raw is PERMANENT_FAILURE:
                # One is enough: this model will never answer with this key.
                print("[llm_agent] model is unusable (permanent error); "
                      "ending session and flagging the backend as dead.")
                model_dead = True
                break
            if raw is None:
                fails += 1
                STATS["stalls"] += 1
                # How many exhausted retry cycles to absorb before giving up.
                # 2 is right for a short session, but on a long one against a
                # per-minute limiter, hitting the ceiling twice is normal rather
                # than fatal -- and quitting there is what produced sessions
                # that used 3 of 30 steps. Raise it for Phase 2 collection where
                # completing the task is the point.
                if fails >= MAX_STALLS:
                    print(f"[llm_agent] model busy/unreachable after retries "
                          f"({fails} stalls, limit {MAX_STALLS}); ending session "
                          f"early (backend may recover).")
                    break
                continue
            fails = 0
            act = parse_action(raw)
            if not act:
                print(f"[{step}] unparseable model reply; skipping:", (raw or "")[:120])
                continue
            print(f"[{step}] {act.get('action')} "
                  f"idx={act.get('index')} {act.get('reasoning','')[:60]}")
            if act.get("action") == "done":
                break
            try:
                do_action(page, act, site_root)
            except PWTimeout:
                STATS["action_timeouts"] += 1
                print(f"[{step}] action timed out")
            except Exception as e:
                print(f"[{step}] action error: {e}")
            history.append({k: act.get(k) for k in ("action", "index", "url") if act.get(k) is not None})
            time.sleep(random.uniform(0.6, 2.0) + MIMIC_THINK_MS / 1000.0)  # human-like dwell (+mimicry)

        time.sleep(3)  # let the last track.js batch flush
        # Read the id BEFORE closing the browser -- the context is gone after.
        sid = session_uid_from_cookies(context)
        browser.close()
        # Grade the session in one line. "done." alone could not distinguish a
        # session that ran its full budget from one that quit after three
        # actions because the provider was throttling -- and those two produce
        # very different feature rows under the same label.
        used = step + 1 if args.steps else 0
        # Name the actual cause. The previous version called every stall
        # "rate-limited", which was wrong two thirds of the time and sent the
        # investigation after the provider instead of after max_tokens.
        if model_dead:
            health = "DEAD (model unusable -- session is label-only)"
        elif STATS["empty"] > STATS["calls"] * 0.2:
            health = (f"DEGRADED (empty replies: {STATS['empty']}/"
                      f"{STATS['calls']} -- raise CHARWEB_MAX_TOKENS)")
        elif STATS["rate_limited"]:
            health = "DEGRADED (rate-limited)"
        elif STATS["stalls"]:
            health = "DEGRADED (unexplained stalls)"
        else:
            health = "clean"
        write_health(sid, args, model, used, model_dead)
        print(f"[llm_agent] done. steps_used={used}/{args.steps} "
              f"calls={STATS['calls']} rate_limited={STATS['rate_limited']} "
              f"transient={STATS['transient']} empty={STATS['empty']} "
              f"recovered={STATS['recovered']} stalls={STATS['stalls']} "
              f"sid={(sid or 'UNKNOWN')[:12]} -> {health}")

    # Exit code 4 = "the model backend is broken, not this session". Batch
    # runners use it to disable the arm instead of grinding through every
    # remaining round producing label-only sessions.
    if model_dead:
        raise SystemExit(4)


if __name__ == "__main__":
    run()
