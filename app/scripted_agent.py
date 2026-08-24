#!/usr/bin/env python3
"""Matched-task scripted harness for the harness axis (confound-free).

The three scripted harnesses (grok/ai_agent/advanced_agent) each ran a DIFFERENT
task list, so a classifier could separate them just by which pages they visited
-- a task-coverage confound, not a behavioural fingerprint. This script fixes
that: every profile runs the SAME task protocol (same pages, same actions, same
order) and differs ONLY in its behavioural policy:

  plain      -- fast, fixed typing; direct clicks; minimal dwell (grok-like)
  noisy      -- variable per-char delays + typo/backspace; stepped mouse moves
                before clicks; variable pauses (ai_agent-like)
  humanlike  -- gaussian "think" delays; reading-time dwell scaled to text;
                curved multi-segment mouse paths; longer varied pauses
                (advanced_agent-like)

Because task coverage is identical, leave-one-harness-out separates these on
*style* (timing / mouse / typing), which is the real harness signature.

Labels are forced from the profile: CHARWEB_HARNESS=scripted_<profile>,
CHARWEB_MODEL=none_scripted. No LLM, so no API/rate limits.

    python app/scripted_agent.py --profile plain     --n 15 --url https://charweb.net
    python app/scripted_agent.py --profile noisy     --n 15 --url https://charweb.net
    python app/scripted_agent.py --profile humanlike --n 15 --url https://charweb.net
"""
import argparse
import math
import os
import random
import time

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

try:
    from run_labels import build_run_headers, build_run_cookies
except ImportError:
    from app.run_labels import build_run_headers, build_run_cookies

PASSWORD = "ScriptedPass123!"

# --- behavioural profiles: each is the harness's low-level policy ------------
PROFILES = {
    "plain":     dict(cdelay=(12, 18), pause=(0.08, 0.25), typo=0.0,
                      mouse="direct", dwell=(0.3, 0.8), scrolls=(2, 3), think=0.0),
    "noisy":     dict(cdelay=(25, 120), pause=(0.2, 1.2), typo=0.08,
                      mouse="stepped", dwell=(0.5, 1.6), scrolls=(3, 4), think=0.0),
    "humanlike": dict(cdelay=(40, 200), pause=(0.5, 2.5), typo=0.05,
                      mouse="curved", dwell=(1.0, 6.0), scrolls=(3, 6), think=0.6),
}


def pause(p):
    time.sleep(random.uniform(*p["pause"]))


def think(p):
    if p["think"] > 0:
        time.sleep(abs(random.gauss(p["think"], p["think"])))


def move_mouse(page, x, y, p):
    if p["mouse"] == "direct":
        return
    if p["mouse"] == "stepped":
        page.mouse.move(x, y, steps=random.randint(5, 10))
    else:  # curved: a few short segments with slight jitter
        try:
            start = page.evaluate("() => [window.__mx||100, window.__my||100]")
        except Exception:
            start = [100, 100]
        sx, sy = start
        segs = random.randint(3, 5)
        for i in range(1, segs + 1):
            t = i / segs
            mx = sx + (x - sx) * t + random.uniform(-15, 15) * math.sin(t * math.pi)
            my = sy + (y - sy) * t + random.uniform(-15, 15) * math.sin(t * math.pi)
            page.mouse.move(mx, my, steps=random.randint(3, 8))
        page.evaluate(f"() => {{ window.__mx={x}; window.__my={y}; }}")


def click_sel(page, selector, p, timeout=6000):
    loc = page.locator(selector).first
    try:
        box = loc.bounding_box()
        if box:
            move_mouse(page, box["x"] + box["width"] / 2,
                       box["y"] + box["height"] / 2, p)
    except Exception:
        pass
    loc.click(timeout=timeout)


def type_text(page, selector, text, p):
    loc = page.locator(selector).first
    try:
        box = loc.bounding_box()
        if box:
            move_mouse(page, box["x"] + 5, box["y"] + 5, p)
    except Exception:
        pass
    loc.click()
    time.sleep(0.2)
    for ch in text:
        page.keyboard.type(ch, delay=random.randint(*p["cdelay"]))
        if random.random() < p["typo"]:
            page.keyboard.type(random.choice("abcdefghijklmnop"), delay=80)
            time.sleep(0.2)
            page.keyboard.press("Backspace")
    # Safety net: char-by-char keyboard typing can miss focus on a field and
    # leave it empty/garbled, which silently breaks register/login. Confirm the
    # field holds exactly `text`; fix it with fill() if not. Keeps the real
    # keystrokes (the behavioural signal) when focus was fine.
    try:
        if loc.input_value() != text:
            loc.fill(text)
    except Exception:
        pass


def read_dwell(page, p, text=""):
    lo, hi = p["dwell"]
    if p["mouse"] == "curved" and text:                 # reading time scales with text
        secs = min(len(text) / 22.0 + random.uniform(lo, 1.5), hi)
    else:
        secs = random.uniform(lo, hi)
    time.sleep(secs)


def submit_form(page, anchor):
    try:
        form = page.locator(f"form:has({anchor})").first
        btn = form.locator("input[type=submit], button[type=submit]")
        if btn.count():
            btn.first.click(timeout=5000)
        else:
            page.locator(anchor).first.press("Enter")
        page.wait_for_load_state("domcontentloaded")
    except Exception:
        try:
            page.locator(anchor).first.press("Enter")
        except Exception:
            pass


# --- the MATCHED task protocol (identical across all profiles) ---------------
def run_session(page, site, username, p):
    # 1. register
    page.goto(f"{site}/register", wait_until="domcontentloaded")
    think(p)
    type_text(page, "input[name='username']", username, p); pause(p)
    type_text(page, "input[name='email']", f"{username}@test.com", p); pause(p)
    type_text(page, "input[name='password']", PASSWORD, p); pause(p)
    type_text(page, "input[name='password2']", PASSWORD, p); pause(p)
    for sel in ("input[name='accept_terms']", "input[name='remember_me']"):
        try:
            if page.locator(sel).count():
                page.check(sel)
        except Exception:
            pass
    submit_form(page, "input[name='password2']"); pause(p)

    # register diagnostic: if we're still on /register, the form was rejected
    # (validation error) and the account was never created.
    if "/register" in page.url:
        try:
            err = page.locator("body").inner_text()[:300].replace("\n", " ")
        except Exception:
            err = "?"
        print(f"[scripted:{p['name']}] REGISTER REJECTED url={page.url} :: {err!r}")

    # 2. login
    page.goto(f"{site}/login", wait_until="domcontentloaded")
    type_text(page, "input[name='username']", username, p); pause(p)
    type_text(page, "input[name='password']", PASSWORD, p)
    submit_form(page, "input[name='password']"); pause(p)

    # verify
    page.goto(f"{site}/edit_profile", wait_until="domcontentloaded")
    logged_in = "/login" not in page.url
    print(f"[scripted:{p['name']}] account={username} logged_in={logged_in}")
    if not logged_in:
        try:
            err = page.locator("body").inner_text()[:300].replace("\n", " ")
        except Exception:
            err = "?"
        print(f"[scripted:{p['name']}] LOGIN FAILED url={page.url} :: {err!r}")

    # 3. home feed: scroll + dwell
    page.goto(f"{site}/", wait_until="domcontentloaded")
    for _ in range(random.randint(*p["scrolls"])):
        page.mouse.wheel(0, random.randint(300, 700))
        read_dwell(page, p)

    # 4. open first post
    try:
        posts = page.locator("div.post a, .post a, td a, li a")
        if posts.count():
            txt = ""
            try:
                txt = posts.first.inner_text()
            except Exception:
                pass
            click_sel(page, "div.post a, .post a, td a, li a", p)
            read_dwell(page, p, txt); pause(p)
    except Exception:
        pass

    # 5. like a post (best-effort, identical attempt across profiles)
    for sel in ("button:has-text('Like')", "form[action*='like'] button",
                "form[action*='like'] input[type=submit]", ".like-btn"):
        try:
            if page.locator(sel).count():
                click_sel(page, sel, p); pause(p); break
        except Exception:
            pass

    # 6. search
    page.goto(f"{site}/", wait_until="domcontentloaded")
    try:
        if page.locator("input[name='q']").count():
            type_text(page, "input[name='q']", "test", p)
            page.keyboard.press("Enter")
            page.wait_for_load_state("domcontentloaded")
            read_dwell(page, p)
    except Exception:
        pass

    # 7. edit profile about_me
    page.goto(f"{site}/edit_profile", wait_until="domcontentloaded")
    try:
        am = "textarea[name='about_me'], input[name='about_me']"
        if page.locator(am).count():
            loc = page.locator(am).first
            try:
                loc.fill("")
            except Exception:
                pass
            type_text(page, am, "Exploring the site.", p); pause(p)
            submit_form(page, am)
    except Exception:
        pass

    # 8. daily sign-in (best-effort, identical attempt)
    page.goto(f"{site}/daily", wait_until="domcontentloaded")
    read_dwell(page, p)
    for sel in ("button:has-text('Sign')", "form[action*='daily'] button",
                "button:has-text('Claim')", "input[type=submit]"):
        try:
            if page.locator(sel).count():
                click_sel(page, sel, p); pause(p); break
        except Exception:
            pass

    # 9. logout
    page.goto(f"{site}/logout", wait_until="domcontentloaded")
    time.sleep(3)   # let the last track.js batch flush


def main():
    ap = argparse.ArgumentParser(description="Matched-task scripted harness")
    ap.add_argument("--profile", required=True, choices=list(PROFILES))
    ap.add_argument("--n", type=int, default=15, help="sessions to run")
    ap.add_argument("--url", default="https://charweb.net")
    ap.add_argument("--headless", action="store_true", default=True)
    ap.add_argument("--headed", dest="headless", action="store_false")
    args = ap.parse_args()

    site = args.url.rstrip("/")
    p = dict(PROFILES[args.profile]); p["name"] = args.profile

    # Force the labels this harness owns (overrides any stale $env:).
    os.environ["CHARWEB_HARNESS"] = f"scripted_{args.profile}"
    os.environ["CHARWEB_MODEL"] = "none_scripted"
    os.environ.setdefault("CHARWEB_INSTRUCTION", "matched")

    print(f"[scripted_agent] profile={args.profile} n={args.n} url={site}")
    with sync_playwright() as pw:
        for i in range(args.n):
            # underscores only: hyphens are rejected by the username validator,
            # so every hyphenated account silently failed to register.
            uname = f"scr_{args.profile}_{i:03d}_{random.randint(1000,9999)}"
            print(f"[{i+1}/{args.n}] {uname}", flush=True)
            browser = pw.chromium.launch(headless=args.headless)
            context = browser.new_context(viewport={"width": 1280, "height": 800})
            context.set_extra_http_headers(build_run_headers())
            cookies = build_run_cookies(site)
            if cookies:
                context.add_cookies(cookies)
            page = context.new_page()
            try:
                run_session(page, site, uname, p)
            except PWTimeout:
                print(f"    (timeout) {uname}")
            except Exception as e:
                print(f"    (error) {uname}: {str(e)[:120]}")
            finally:
                browser.close()
    print("[scripted_agent] done.")


if __name__ == "__main__":
    main()
