#!/usr/bin/env python3
"""Same model, same task, five different ways of PERCEIVING the page.

Every harness in the study so far reads the page through the DOM -- the
scripted profiles use selectors, llm_driven gets a text listing of elements,
Fenris drives through its addon. So the perception axis has exactly one level,
and H3 ("architecture leaves a signature the model does not") cannot be tested:
there is nothing to contrast against.

This adds the missing levels. The model, the task, the site and the tracking are
held constant; only how the agent SEES the page changes:

  dom      a text listing of interactive elements, act by [index]
           -- the existing llm_driven baseline, reproduced here so the
              comparison is within one script rather than across two
  axtree   the accessibility tree (role + accessible name), act by [index]
           -- Playwright MCP, browser-use
  rawhtml  cleaned HTML source with interactive elements annotated
           -- MindAct and the early HTML-in-context agents
  som      a SCREENSHOT with numbered boxes drawn over each element
           -- WebVoyager, SeeAct ("set of marks")
  vision   a plain SCREENSHOT; the agent replies with x,y PIXEL coordinates
           -- Claude computer use, Operator

Why this should leave a trace, mechanically: a DOM agent clicks the exact
centre of a resolved bounding box, and can act on elements that are off-screen.
A vision agent clicks where it *estimates* the target is -- off-centre,
sometimes missing -- and must scroll something into view before it can act on
it at all. Those are different distributions in geom_* and in scroll structure,
which is precisely what the geometry feature group was built to catch.

Needs a vision-capable model for som/vision. meta/llama-3.2-11b-vision-instruct
is one, and is already validated clean in this project.

    CHARWEB_LLM_PROVIDER=nvidia CHARWEB_LLM_KEY=... \\
    CHARWEB_LLM_MODEL=meta/llama-3.2-11b-vision-instruct \\
    CHARWEB_INSTRUCTION=free_explore CHARWEB_RUN_ID=percept_v1 \\
    python app/perception_agent.py --mode vision --username ilv --headless

The harness label written to the server is percept_<mode>, so every mode is its
own level of the harness axis and nothing pools with earlier data.
"""
import argparse
import base64
import os
import random
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != _HERE]
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import importlib.util

_spec = importlib.util.spec_from_file_location(
    "_llm_agent", os.path.join(_HERE, "llm_agent.py"))
LA = importlib.util.module_from_spec(_spec)
sys.modules["_llm_agent"] = LA
_spec.loader.exec_module(LA)

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout  # noqa: E402
from instructions import for_llm_agent                                      # noqa: E402

VIEWPORT = {"width": 1280, "height": 800}

# Modes that send an image. Kept explicit so a text-only model fails fast with
# a clear message instead of silently returning nonsense about a page it never
# saw.
VISUAL_MODES = {"som", "vision"}

# ---------------------------------------------------------------- action space

_INDEXED_SPEC = """\
Reply with ONE JSON object and nothing else (no markdown fences). Schema:
{"reasoning":"<one short sentence>",
 "action":"click|type|scroll|goto|wait|done",
 "index":<int, the [index] of the element for click/type>,
 "text":"<text to type, for type>",
 "url":"<path like /home, for goto>"}
Pick an element only by an [index] shown above. After 'type' on a search or
form field, Enter is pressed automatically. Use 'done' when your task is
complete. Never use an index that is not listed."""

_PIXEL_SPEC = """\
Reply with ONE JSON object and nothing else (no markdown fences). Schema:
{"reasoning":"<one short sentence>",
 "action":"click|type|scroll|goto|wait|done",
 "x":<int pixel x, for click/type>, "y":<int pixel y, for click/type>,
 "text":"<text to type, for type>",
 "url":"<path like /home, for goto>"}
The screenshot is %d x %d pixels; x,y are measured from its top-left corner.
Click the CENTRE of what you want. To reach something not visible, scroll
first -- you can only act on what is currently on screen. After 'type', Enter
is pressed automatically. Use 'done' when your task is complete.""" % (
    VIEWPORT["width"], VIEWPORT["height"])

# ------------------------------------------------------------------ perception

# Playwright's page.accessibility was deprecated and removed, so the tree is
# built here from ARIA semantics -- which is what browser-use and Playwright
# MCP do in practice anyway. Elements are stamped with data-llm-idx so the
# action space matches the other indexed modes: the variable under test is what
# the agent SEES (role + accessible name, not tag + text), not how the click is
# ultimately dispatched.
_AXTREE_JS = """
() => {
  const roleOf = (el) => {
    const explicit = el.getAttribute('role');
    if (explicit) return explicit;
    const t = el.tagName.toLowerCase();
    if (t === 'a') return el.hasAttribute('href') ? 'link' : 'generic';
    if (t === 'button') return 'button';
    if (t === 'select') return 'combobox';
    if (t === 'textarea') return 'textbox';
    if (t === 'input') {
      const ty = (el.getAttribute('type') || 'text').toLowerCase();
      if (ty === 'search') return 'searchbox';
      if (ty === 'checkbox') return 'checkbox';
      if (ty === 'radio') return 'radio';
      if (['submit','button','reset','image'].includes(ty)) return 'button';
      return 'textbox';
    }
    return 'generic';
  };
  const nameOf = (el) =>
    (el.getAttribute('aria-label') || el.getAttribute('alt') ||
     el.innerText || el.value || el.getAttribute('placeholder') ||
     el.getAttribute('title') || el.getAttribute('name') || '')
    .trim().replace(/\\s+/g, ' ').slice(0, 70);
  const out = []; let i = 0;
  for (const el of document.querySelectorAll(
        'a,button,input,textarea,select,[role]')) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    const st = window.getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none') continue;
    if (el.getAttribute('aria-hidden') === 'true') continue;
    const role = roleOf(el);
    if (role === 'generic') continue;
    el.setAttribute('data-llm-idx', i);
    out.push({index: i, role: role, name: nameOf(el),
              disabled: el.disabled === true});
    i++;
    if (i >= %d) break;
  }
  return out;
}
""" % LA.MAX_ELEMENTS

_RAWHTML_JS = """
() => {
  let i = 0;
  for (const el of document.querySelectorAll(
        'a,button,input,textarea,select,[role=button]')) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    const st = window.getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none') continue;
    el.setAttribute('data-llm-idx', i); i++;
    if (i >= %d) break;
  }
  const clone = document.body.cloneNode(true);
  for (const bad of clone.querySelectorAll('script,style,svg,noscript'))
    bad.remove();
  return clone.innerHTML.replace(/\\s+/g, ' ');
}
""" % LA.MAX_ELEMENTS

# Draws a numbered badge over every interactive element, so a vision model can
# name a target by number instead of by pixel. This is the "set of marks"
# technique -- a hybrid: the page is seen as an image, but the action space is
# still discrete and DOM-anchored.
_SOM_JS = """
() => {
  for (const old of document.querySelectorAll('.__som_badge')) old.remove();
  let i = 0;
  for (const el of document.querySelectorAll(
        'a,button,input,textarea,select,[role=button]')) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    const st = window.getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none') continue;
    if (r.bottom < 0 || r.top > window.innerHeight) continue;   // off-screen
    el.setAttribute('data-llm-idx', i);
    el.style.outline = '2px solid #e11';
    const b = document.createElement('div');
    b.className = '__som_badge';
    b.textContent = i;
    b.style.cssText = 'position:fixed;z-index:2147483647;background:#e11;' +
      'color:#fff;font:bold 12px monospace;padding:0 3px;border-radius:2px;' +
      'left:' + Math.max(0, r.left) + 'px;top:' + Math.max(0, r.top - 14) + 'px;';
    document.body.appendChild(b);
    i++;
    if (i >= %d) break;
  }
  return i;
}
""" % LA.MAX_ELEMENTS

_SOM_CLEAN_JS = """
() => { for (const b of document.querySelectorAll('.__som_badge')) b.remove();
        for (const el of document.querySelectorAll('[data-llm-idx]'))
          el.style.outline = ''; }
"""


def _shot(page):
    return base64.b64encode(page.screenshot(type="jpeg", quality=70)).decode()


def observe(page, mode):
    """Returns (user_content, spec, axnodes).

    user_content is either a string (text modes) or an OpenAI-style content
    list carrying an image. axnodes is the role/name list for axtree mode, used
    to resolve an index back to a clickable locator.
    """
    if mode == "dom":
        els = page.evaluate(LA.EXTRACT_JS)
        listing = "\n".join(
            f'[{e["index"]}] <{e["tag"]}{"/"+e["type"] if e["type"] else ""}> '
            f'{e["text"] or e["name"] or e["href"]}' for e in els) \
            or "(no interactive elements found)"
        return f"Interactive elements:\n{listing}", _INDEXED_SPEC, None

    if mode == "axtree":
        nodes = page.evaluate(_AXTREE_JS)
        lines = "\n".join(
            f'[{n["index"]}] {n["role"]} "{n["name"]}"'
            + ("  (disabled)" if n["disabled"] else "") for n in nodes)
        return ("Accessibility tree:\n" + (lines or "(empty)"),
                _INDEXED_SPEC, nodes)

    if mode == "rawhtml":
        html = page.evaluate(_RAWHTML_JS)
        # A whole page of HTML will not fit a sane context; truncating is what
        # real HTML-in-context agents do too, and the truncation itself is part
        # of that architecture's behaviour.
        html = html[:12000]
        return (f"Page HTML (interactive elements carry data-llm-idx):\n{html}",
                _INDEXED_SPEC, None)

    if mode == "som":
        n = page.evaluate(_SOM_JS)
        img = _shot(page)
        page.evaluate(_SOM_CLEAN_JS)
        return ([{"type": "text",
                  "text": f"Screenshot with {n} numbered, outlined targets. "
                          f"Refer to one by its number as 'index'."},
                 {"type": "image_url",
                  "image_url": {"url": f"data:image/jpeg;base64,{img}"}}],
                _INDEXED_SPEC, None)

    if mode == "vision":
        img = _shot(page)
        return ([{"type": "text", "text": "Screenshot of the current page."},
                 {"type": "image_url",
                  "image_url": {"url": f"data:image/jpeg;base64,{img}"}}],
                _PIXEL_SPEC, None)

    raise SystemExit(f"unknown mode {mode!r}")


# --------------------------------------------------------------------- acting

def act(page, a, mode, axnodes, site_root):
    kind = (a or {}).get("action")

    if kind in ("scroll", "goto", "wait") or kind is None:
        return LA.do_action(page, a, site_root)

    if mode == "vision":
        x, y = a.get("x"), a.get("y")
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            raise ValueError("vision action without x,y")
        x = max(0, min(int(x), VIEWPORT["width"] - 1))
        y = max(0, min(int(y), VIEWPORT["height"] - 1))
        # Move then click, so the mouse-kinematic features see a real path --
        # the same courtesy do_action extends to the DOM modes, otherwise the
        # geometry difference between architectures would be an artifact of
        # how we drive the browser rather than of the architecture.
        page.mouse.move(x + random.uniform(-2, 2), y + random.uniform(-2, 2),
                        steps=random.randint(4, 12))
        time.sleep(random.uniform(0.1, 0.35))
        page.mouse.click(x, y)
        if kind == "type":
            for ch in str(a.get("text", "")):
                page.keyboard.type(ch, delay=random.randint(40, 130))
            page.keyboard.press("Enter")
        return

    # dom, axtree, rawhtml, som all address elements by data-llm-idx
    return LA.do_action(page, a, site_root)


# --------------------------------------------------------------------- asking

def ask(client, model, system, user_content):
    """Same retry/empty-reply handling as llm_agent, but accepting multimodal
    content. Counters are shared so the health file is comparable."""
    for attempt in range(6):
        try:
            LA.STATS["calls"] += 1
            r = client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": user_content}],
                temperature=0.7, max_tokens=LA.MAX_TOKENS)
            ch = r.choices[0]
            content = ch.message.content
            if not (content or "").strip():
                LA.STATS["empty"] += 1
                reasoning = (getattr(ch.message, "reasoning_content", None)
                             or getattr(ch.message, "reasoning", None) or "")
                if LA.STATS["empty"] == 1:
                    print(f"[percept] EMPTY content (finish_reason="
                          f"{getattr(ch,'finish_reason',None)!r}, "
                          f"max_tokens={LA.MAX_TOKENS})")
                import re as _re
                if reasoning.strip() and _re.search(r'\{[^{}]*"action"', reasoning):
                    LA.STATS["recovered"] += 1
                    return reasoning
                if getattr(ch, "finish_reason", None) == "length" and attempt < 2:
                    continue
                return None
            return content
        except Exception as e:
            msg, low = str(e), str(e).lower()
            is_rate = "429" in msg or "rate limit" in low or "resource_exhausted" in low
            is_tran = any(s in low for s in ("timeout", "timed out", "connection",
                                             "503", "502", "500", "unavailable",
                                             "overloaded", "temporarily"))
            if not (is_rate or is_tran):
                print(f"[percept] model error: {msg[:180]}")
                return LA.PERMANENT_FAILURE
            LA.STATS["rate_limited" if is_rate else "transient"] += 1
            print(f"[percept] {'rate limited' if is_rate else 'transient'}; "
                  f"retry (attempt {attempt+1}/6)"
                  + (f" :: {msg[:110]}" if attempt == 0 else ""))
            time.sleep(31 if is_rate else 5)
    return None


_ALIASES = {"read": "wait", "browse": "wait", "look": "wait", "observe": "wait",
            "navigate": "goto", "open": "click", "press": "click",
            "input": "type", "write": "type", "enter": "type"}


def parse_tolerant(raw):
    """Accept what a real agent framework would accept.

    llm_agent's parser is deliberately strict and is left untouched, so the
    existing corpus keeps its semantics. Here the strictness would be measuring
    the wrong thing: if `som` returns [{'action': 'goto'}] -- a list, with
    Python quotes -- and that scores as a failed step, the number reflects my
    parser, not the architecture. Every production agent tolerates this.

    Returns (action_dict_or_None, note) where note names the leniency used, so
    the log shows how often a mode needed rescuing.
    """
    import ast
    import json as _json
    import re as _re
    if not raw:
        return None, "empty"
    s = raw.strip()
    if s.startswith("```"):
        s = s.strip("`")
        i = s.find("{")
        j = s.find("[")
        cut = min(x for x in (i, j) if x != -1) if (i != -1 or j != -1) else 0
        s = s[cut:]

    obj, note = None, ""
    # Try the outermost {...} or [...] with JSON first, then Python literals
    # (single quotes / True / None), which small models emit constantly.
    for pat in (r"\{.*\}", r"\[.*\]"):
        m = _re.search(pat, s, _re.S)
        if not m:
            continue
        for loader, tag in ((_json.loads, ""), (ast.literal_eval, "py-literal")):
            try:
                obj = loader(m.group(0))
                note = tag
                break
            except Exception:
                continue
        if obj is not None:
            break
    if obj is None:
        return None, "unparseable"

    if isinstance(obj, list):
        # A list of steps: take the first. Agents that plan several actions at
        # once are common; executing the head and re-observing is the standard
        # way frameworks handle it.
        obj = next((o for o in obj if isinstance(o, dict)), None)
        note = (note + "+list").strip("+")
        if obj is None:
            return None, "list-without-object"
    if not isinstance(obj, dict):
        return None, "not-an-object"

    # Some models put the verb under "type" (colliding with the type action) or
    # omit it entirely while supplying an obviously-typed field.
    act_v = obj.get("action")
    if not isinstance(act_v, str):
        act_v = obj.get("act") or obj.get("command") or obj.get("type") or ""
        note = (note + "+verb-key").strip("+")
    act_v = str(act_v).strip().lower()
    if "|" in act_v:                       # "click|goto" -- undecided model
        act_v = act_v.split("|")[0]
        note = (note + "+alternatives").strip("+")
    if act_v in _ALIASES:
        note = (note + f"+alias:{act_v}").strip("+")
        act_v = _ALIASES[act_v]
    obj["action"] = act_v

    for k in ("index", "x", "y"):
        if k in obj and isinstance(obj[k], str) and obj[k].strip().lstrip("-").isdigit():
            obj[k] = int(obj[k].strip())
    return obj, note


def write_health(sid, harness, model, used, budget, model_dead):
    import csv
    calls = LA.STATS["calls"]
    row = {
        "session_uid": sid or "", "run_id": os.environ.get("CHARWEB_RUN_ID", ""),
        "harness": harness, "model": model,
        "instruction_condition": os.environ.get("CHARWEB_INSTRUCTION", ""),
        "steps_used": used, "steps_budget": budget, "calls": calls,
        "rate_limited": LA.STATS["rate_limited"],
        "transient": LA.STATS["transient"], "empty": LA.STATS["empty"],
        "recovered": LA.STATS["recovered"], "stalls": LA.STATS["stalls"],
        "empty_rate": f"{(LA.STATS['empty']/calls if calls else 0):.3f}",
        "max_tokens": LA.MAX_TOKENS, "model_dead": int(bool(model_dead)),
        "clean": int(LA.STATS["empty"] == 0 and LA.STATS["stalls"] == 0
                     and LA.STATS["rate_limited"] == 0 and not model_dead),
    }
    try:
        new = not os.path.exists(LA.HEALTH_CSV)
        with open(LA.HEALTH_CSV, "a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(row))
            if new:
                w.writeheader()
            w.writerow(row)
    except Exception as e:
        print(f"[percept] could not write health row: {str(e)[:90]}")


def main():
    p = argparse.ArgumentParser(description="Perception-modality agent")
    p.add_argument("--mode", required=True,
                   choices=["dom", "axtree", "rawhtml", "som", "vision"])
    p.add_argument("--username", required=True)
    p.add_argument("--url", default="https://charweb.net")
    p.add_argument("--headless", action="store_true",
                   default=not os.environ.get("DISPLAY"))
    p.add_argument("--headed", dest="headless", action="store_false")
    p.add_argument("--steps", type=int,
                   default=int(os.environ.get("CHARWEB_MAX_STEPS", "30")))
    args = p.parse_args()

    site = args.url.rstrip("/")
    client, model, provider = LA.llm_client()
    harness = f"percept_{args.mode}"
    instr_name = os.environ.get("CHARWEB_INSTRUCTION", "free_explore")

    # The harness label IS the perception mode -- that is the whole variable.
    os.environ["CHARWEB_HARNESS"] = harness
    os.environ["CHARWEB_MODEL"] = model
    os.environ.setdefault("CHARWEB_INSTRUCTION", instr_name)

    print(f"[percept] mode={args.mode} provider={provider} model={model} "
          f"instruction={instr_name} steps={args.steps}")
    if args.mode in VISUAL_MODES:
        print(f"[percept] this mode sends IMAGES -- {model} must be "
              f"vision-capable or every reply will be about a page it "
              f"never saw.")

    system = (for_llm_agent(instr_name) + "\n\n" +
              (_PIXEL_SPEC if args.mode == "vision" else _INDEXED_SPEC))

    model_dead, used, sid = False, 0, None
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=args.headless)
        ctx = browser.new_context(viewport=VIEWPORT)
        ctx.set_extra_http_headers(LA.build_run_headers())
        page = ctx.new_page()

        account = f"{args.username}_{os.urandom(3).hex()}"
        if not LA.scripted_signup(page, site, account):
            print("[percept] WARNING: could not confirm login")

        history = []
        fails = bad = lenient = 0
        for step in range(args.steps):
            used = step + 1
            try:
                content, spec, axnodes = observe(page, args.mode)
            except Exception as e:
                print(f"[{step}] observe failed: {str(e)[:110]}")
                continue

            tail = f"\n\nRecent actions: {history[-4:] if history else 'none'}\n\nWhat is your next action? JSON only."
            if isinstance(content, str):
                user = content + tail
            else:
                user = list(content) + [{"type": "text", "text": tail}]

            raw = ask(client, model, system, user)
            if raw is LA.PERMANENT_FAILURE:
                model_dead = True
                break
            if raw is None:
                fails += 1
                LA.STATS["stalls"] += 1
                if fails >= int(os.environ.get("CHARWEB_MAX_STALLS", "2")):
                    print("[percept] model unreachable; ending session early")
                    break
                continue
            fails = 0

            a, note = parse_tolerant(raw)
            if not a:
                bad += 1
                print(f"[{step}] unparseable ({note}): {(raw or '')[:100]}")
                continue
            if note:
                lenient += 1
            where = (f"({a.get('x')},{a.get('y')})" if args.mode == "vision"
                     else f"idx={a.get('index')}")
            print(f"[{step}] {a.get('action')} {where} "
                  f"{str(a.get('reasoning',''))[:52]}"
                  + (f"   [{note}]" if note else ""), flush=True)
            if a.get("action") == "done":
                break
            try:
                act(page, a, args.mode, axnodes, site)
            except PWTimeout:
                print(f"[{step}] action timed out")
            except Exception as e:
                print(f"[{step}] action failed: {str(e)[:110]}")
            history.append(f"{a.get('action')} {where}")
            time.sleep(random.uniform(0.6, 2.0))

        time.sleep(3)
        sid = LA.session_uid_from_cookies(ctx)
        browser.close()

    write_health(sid, harness, model, used, args.steps, model_dead)
    s = LA.STATS
    health = ("DEAD (model unusable)" if model_dead
              else f"DEGRADED (empty {s['empty']}/{s['calls']})"
              if s["empty"] > s["calls"] * 0.2
              else "DEGRADED (rate-limited)" if s["rate_limited"]
              else "DEGRADED (stalls)" if s["stalls"] else "clean")
    print(f"[percept] done. mode={args.mode} steps_used={used}/{args.steps} "
          f"calls={s['calls']} empty={s['empty']} stalls={s['stalls']} "
          f"unparseable={bad} lenient={lenient} "
          f"sid={(sid or 'UNKNOWN')[:12]} -> {health}")
    if model_dead:
        raise SystemExit(4)


if __name__ == "__main__":
    main()
