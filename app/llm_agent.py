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
import time

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

try:
    from run_labels import build_run_headers
except ImportError:
    from app.run_labels import build_run_headers

# OpenAI-compatible base URLs. Override with CHARWEB_LLM_BASE_URL if needed.
PROVIDERS = {
    "gemini":     "https://generativelanguage.googleapis.com/v1beta/openai/",
    "groq":       "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "openai":     "https://api.openai.com/v1",
}
DEFAULT_MODEL = {
    "gemini":     "gemini-2.0-flash",
    "groq":       "llama-3.3-70b-versatile",
    "openrouter": "meta-llama/llama-3.3-70b-instruct",
    "openai":     "gpt-4o-mini",
}

INSTRUCTIONS = {
    "free_explore": (
        "You are a new user exploring a small social site called Charweb. There is "
        "no checklist -- browse the way a curious person would: read the feed, open "
        "posts, like or comment on things that interest you, check your profile, try "
        "the daily sign-in and the little dungeon game, search for something. Vary "
        "how long you spend on pages. Stop when you've had a natural look around."
    ),
    "checklist": (
        "You are testing a small social site called Charweb. Do these in order: "
        "(1) read the home feed and like a post, (2) search for a keyword, (3) open "
        "a post and comment, (4) edit your profile 'about me', (5) do the daily "
        "sign-in. Then finish."
    ),
}

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
    if (i >= 60) break;
  }
  return out;
}
"""


def parse_args():
    p = argparse.ArgumentParser(description="LLM-in-the-loop Charweb agent")
    p.add_argument("--username", required=True)
    p.add_argument("--url", default="https://charweb.net")
    p.add_argument("--headless", action="store_true", default=False)
    p.add_argument("--steps", type=int, default=int(os.environ.get("CHARWEB_MAX_STEPS", "25")))
    return p.parse_args()


def llm_client():
    provider = os.environ.get("CHARWEB_LLM_PROVIDER", "gemini").lower()
    base_url = os.environ.get("CHARWEB_LLM_BASE_URL") or PROVIDERS.get(provider)
    model = os.environ.get("CHARWEB_LLM_MODEL") or DEFAULT_MODEL.get(provider, "gpt-4o-mini")
    key = os.environ.get("CHARWEB_LLM_KEY") or os.environ.get("OPENAI_API_KEY")
    if not base_url or not key:
        raise SystemExit(
            "Set CHARWEB_LLM_PROVIDER (gemini|groq|openrouter|openai) and "
            "CHARWEB_LLM_KEY. See the header of this file for an example.")
    from openai import OpenAI
    return OpenAI(base_url=base_url, api_key=key), model, provider


def ask_model(client, model, instruction, url, elements, history):
    system = instruction + "\n\n" + ACTION_SPEC
    listing = "\n".join(
        f'[{e["index"]}] <{e["tag"]}{"/"+e["type"] if e["type"] else ""}> '
        f'{e["text"] or e["name"] or e["href"]}'
        for e in elements) or "(no interactive elements found)"
    user = (f"Current page: {url}\n\nInteractive elements:\n{listing}\n\n"
            f"Recent actions: {history[-4:] if history else 'none'}\n\n"
            "What is your next action? Reply with the JSON object only.")
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
        temperature=0.7, max_tokens=300)
    return resp.choices[0].message.content


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
    try:
        page.click("input[name='submit'], button[type='submit']", timeout=5000)
    except Exception:
        pass
    time.sleep(1.5)
    page.goto(f"{site_root}/login", wait_until="domcontentloaded")
    try:
        page.fill("input[name='username']", username)
        page.fill("input[name='password']", pw)
        page.click("input[name='submit'], button[type='submit']", timeout=5000)
    except Exception:
        pass
    time.sleep(1.5)


def run():
    args = parse_args()
    site_root = args.url.rstrip("/")
    client, model, provider = llm_client()

    instr_name = os.environ.get("CHARWEB_INSTRUCTION", "free_explore")
    instruction = INSTRUCTIONS.get(instr_name, INSTRUCTIONS["free_explore"])

    # Make the recorded labels match what actually ran, unless already set.
    os.environ.setdefault("CHARWEB_HARNESS", "llm_driven")
    os.environ.setdefault("CHARWEB_MODEL", model)
    os.environ.setdefault("CHARWEB_INSTRUCTION", instr_name)

    print(f"[llm_agent] provider={provider} model={model} instruction={instr_name} "
          f"user={args.username} steps={args.steps}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        context.set_extra_http_headers(build_run_headers())
        page = context.new_page()

        scripted_signup(page, site_root, args.username)

        history = []
        for step in range(args.steps):
            try:
                elements = page.evaluate(EXTRACT_JS)
            except Exception:
                elements = []
            raw = ask_model(client, model, instruction, page.url, elements, history)
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
