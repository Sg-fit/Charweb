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
    "nvidia":     "meta/llama-3.3-70b-instruct",
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

EXTRACT_JS = """
() => {
  const els = [...document.querySelectorAll('a,button,input,textarea,select,[role=button]')];
  const out = []; let i = 0;
  for (const el of els) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    const st = window.getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none') continue;
    const label = (el.innerText || el.value || el.getAttribute('placeholder') ||
                   el.getAttribute('name') || el.getAttribute('aria-label') || '')
                  .trim().replace(/\\s+/g,' ').slice(0,80);
    el.setAttribute('data-llm-idx', i);
    out.push({index:i, tag:el.tagName.toLowerCase(),
              type:el.getAttribute('type')||'', name:el.getAttribute('name')||'',
              href:(el.getAttribute('href')||'').slice(0,60), text:label});
    i++;
    if (i >= 30) break;
  }
  return out;
}
"""


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
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": user}],
                temperature=0.7, max_tokens=300)
            return resp.choices[0].message.content
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
                return None            # genuine error: skip this step, don't crash
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
            else:
                delay = 5              # transient slow / timeout: quick retry
                kind = "slow/timeout"
            print(f"[llm_agent] {kind}; retry in {delay:.0f}s (attempt {attempt+1}/6)")
            time.sleep(delay)
    return None                        # exhausted retries: caller ends session


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
            loc.type(ch, delay=random.randint(40, 130))
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
        context.set_extra_http_headers(build_run_headers())
        page = context.new_page()

        # Unique account per run so registration never collides with a prior run.
        account = f"{args.username}_{os.urandom(3).hex()}"
        if not scripted_signup(page, site_root, account):
            print("[llm_agent] WARNING: could not confirm login; the agent will "
                  "browse logged-out. Check the register/login form selectors.")

        history = []
        fails = 0
        for step in range(args.steps):
            try:
                elements = page.evaluate(EXTRACT_JS)
            except Exception:
                elements = []
            raw = ask_model(client, model, instruction, page.url, elements, history)
            if raw is None:
                fails += 1
                if fails >= 2:
                    print("[llm_agent] repeated model failures; ending session early.")
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
                print(f"[{step}] action timed out")
            except Exception as e:
                print(f"[{step}] action error: {e}")
            history.append({k: act.get(k) for k in ("action", "index", "url") if act.get(k) is not None})
            time.sleep(random.uniform(0.6, 2.0))  # human-like dwell

        time.sleep(3)  # let the last track.js batch flush
        browser.close()
        print("[llm_agent] done.")


if __name__ == "__main__":
    run()
