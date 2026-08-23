"""
advanced_agent.py — realistic Playwright behavioral agent for charweb.net
research data collection (human-vs-AI study).

This is a SCRIPTED automation agent (not a live reasoning AI) — tuned to
produce more human-like timing/mouse/scroll traces than a naive bot, per
the behavior model ChatGPT's Agent mode outlined:
  - log-normal think times (not fixed delays)
  - curved mouse trajectories with occasional overshoot-and-correct
  - variable scroll acceleration/deceleration
  - realistic typing with occasional typo + backspace correction
  - occasional idle periods
  - reading-time estimates based on post text length
  - non-deterministic navigation order in the free-browsing task

NOTE FOR YOUR PAPER: this remains a fixed script, not an independently
reasoning agent — it should be labeled/analyzed as a more sophisticated
variant of your existing simulated levels (e.g. "L5" / "advanced-scripted"),
not as a new independent AI decision-maker. True model-diversity requires
an agent that makes its own live choices (e.g. an actual LLM driving the
browser turn-by-turn), which this is not.

Usage:
    pip install playwright
    playwright install chromium
    python advanced_agent.py --base-url https://charweb.net --username ai_L5_run1

NOTE ON THE --username ABOVE: it must start with "ai_L5" (or another
playwright_tier-style prefix), never a model name like "chatgpt_run1" or
"claude_run1" -- research/label_architecture.py buckets sessions into
(arch, family, harness) by parsing this prefix, and family=llm_scripted /
family=llm_live are reserved for scripts under a genuine per-model identity
(gptTest.py, gemini.py, grok.py, copilat.py) or genuine live decision-making
(app/claude.py). Labeling this script's output under a model name would
silently misattribute a fixed-script's behavior to that model, which is
exactly the mistake the NOTE above exists to prevent.

Requires Python 3.9+.
"""

import argparse
import asyncio
import math
import random
import string

from playwright.async_api import async_playwright, Page

try:
    from run_labels import build_run_headers
except ImportError:
    from app.run_labels import build_run_headers


# ---------------------------------------------------------------------------
# Timing model
# ---------------------------------------------------------------------------

def lognormal_delay(median=0.6, sigma=0.6, floor=0.05, ceiling=6.0):
    """Sample a delay from a log-normal distribution (matches real human
    think-time distributions far better than a uniform/fixed delay)."""
    mu = math.log(median)
    val = random.lognormvariate(mu, sigma)
    return max(floor, min(ceiling, val))


async def think(median=0.6, sigma=0.6, idle_chance=0.06):
    """Pause before an action. Occasionally insert a longer 'distracted'
    idle period instead of the normal think-time."""
    if random.random() < idle_chance:
        await asyncio.sleep(random.uniform(4.0, 14.0))
    else:
        await asyncio.sleep(lognormal_delay(median, sigma))


def reading_time(text, wpm=220):
    """Rough estimate of how long a human would spend reading a post before
    scrolling on, based on word count, with individual variability."""
    words = max(1, len((text or "").split()))
    return (words / wpm) * 60 * random.uniform(0.7, 1.4)


def ease_in_out(t):
    """Smoothstep easing -- gives natural slow-fast-slow motion instead of
    constant velocity."""
    return t * t * (3 - 2 * t)


# ---------------------------------------------------------------------------
# Mouse movement: cubic-bezier curve, variable speed, occasional overshoot
# ---------------------------------------------------------------------------

class AgentState:
    def __init__(self, page: Page, base_url: str, username: str):
        self.page = page
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.mouse_x = random.uniform(200, 600)
        self.mouse_y = random.uniform(150, 400)


def _bezier(t, p0, p1, p2, p3):
    return (1 - t) ** 3 * p0 + 3 * (1 - t) ** 2 * t * p1 + 3 * (1 - t) * t ** 2 * p2 + t ** 3 * p3


async def move_mouse_human(state: AgentState, target_x, target_y):
    x0, y0 = state.mouse_x, state.mouse_y
    dx, dy = target_x - x0, target_y - y0
    dist = math.hypot(dx, dy) or 1.0
    steps = max(8, min(40, int(dist / 8)))

    # perpendicular offset gives the path a natural curve instead of a
    # straight line -- offset magnitude scales with distance, sign is random
    length = math.hypot(dx, dy) or 1.0
    perp_x, perp_y = -dy / length, dx / length
    curve = random.uniform(-0.3, 0.3) * dist

    cx1 = x0 + dx * 0.3 + perp_x * curve
    cy1 = y0 + dy * 0.3 + perp_y * curve
    cx2 = x0 + dx * 0.7 + perp_x * curve * 0.5
    cy2 = y0 + dy * 0.7 + perp_y * curve * 0.5

    overshoot = random.random() < 0.3
    end_x, end_y = target_x, target_y
    if overshoot:
        end_x = target_x + dx * random.uniform(0.03, 0.08) + random.uniform(-4, 4)
        end_y = target_y + dy * random.uniform(0.03, 0.08) + random.uniform(-4, 4)

    for i in range(steps + 1):
        t = ease_in_out(i / steps)
        x = _bezier(t, x0, cx1, cx2, end_x)
        y = _bezier(t, y0, cy1, cy2, end_y)
        await state.page.mouse.move(x, y)
        # variable per-step delay -> non-uniform velocity, unlike a bot that
        # moves in perfectly even steps
        await asyncio.sleep(random.uniform(0.004, 0.02))

    if overshoot:
        correction_steps = random.randint(3, 6)
        for i in range(correction_steps + 1):
            t = i / correction_steps
            x = end_x + (target_x - end_x) * t
            y = end_y + (target_y - end_y) * t
            await state.page.mouse.move(x, y)
            await asyncio.sleep(random.uniform(0.012, 0.03))

    state.mouse_x, state.mouse_y = target_x, target_y


async def human_click(state: AgentState, selector, timeout=10000):
    """Move the mouse to a random point within the target element (not
    always dead-center), pause briefly, then click."""
    el = await state.page.wait_for_selector(selector, state="visible", timeout=timeout)
    box = await el.bounding_box()
    if not box:
        return False
    tx = box["x"] + box["width"] * random.uniform(0.25, 0.75)
    ty = box["y"] + box["height"] * random.uniform(0.25, 0.75)
    await move_mouse_human(state, tx, ty)
    await think(median=0.15, sigma=0.4, idle_chance=0.02)
    await state.page.mouse.down()
    await asyncio.sleep(random.uniform(0.03, 0.1))
    await state.page.mouse.up()
    return True


# ---------------------------------------------------------------------------
# Typing: variable inter-key delay, occasional typo + backspace correction
# ---------------------------------------------------------------------------

_ADJACENT = {
    "a": "sq", "b": "vn", "c": "xv", "d": "sf", "e": "wr", "f": "dg",
    "g": "fh", "h": "gj", "i": "uo", "j": "hk", "k": "jl", "l": "k",
    "m": "n", "n": "bm", "o": "ip", "p": "o", "q": "wa", "r": "et",
    "s": "ad", "t": "ry", "u": "yi", "v": "cb", "w": "qe", "x": "zc",
    "y": "tu", "z": "x",
}


def _typo_for(ch):
    lower = ch.lower()
    if lower in _ADJACENT and random.random() < 0.7:
        repl = random.choice(_ADJACENT[lower])
        return repl.upper() if ch.isupper() else repl
    return random.choice(string.ascii_lowercase)


async def human_type(state: AgentState, selector, text, typo_rate=0.035):
    el = await state.page.wait_for_selector(selector, timeout=10000)
    box = await el.bounding_box()
    if box:
        await move_mouse_human(
            state,
            box["x"] + box["width"] * random.uniform(0.2, 0.8),
            box["y"] + box["height"] * random.uniform(0.3, 0.7),
        )
        await state.page.mouse.down()
        await asyncio.sleep(random.uniform(0.02, 0.06))
        await state.page.mouse.up()
    await think(median=0.25, sigma=0.4, idle_chance=0.03)

    for ch in text:
        if random.random() < typo_rate:
            await el.type(_typo_for(ch), delay=0)
            await asyncio.sleep(lognormal_delay(0.18, 0.5, floor=0.03, ceiling=0.6))
            await el.press("Backspace")
            await asyncio.sleep(lognormal_delay(0.12, 0.4, floor=0.02, ceiling=0.4))
        await el.type(ch, delay=0)
        await asyncio.sleep(lognormal_delay(0.11, 0.55, floor=0.02, ceiling=0.9))


# ---------------------------------------------------------------------------
# Scrolling: variable acceleration/deceleration profile
# ---------------------------------------------------------------------------

async def human_scroll(state: AgentState, total_px, pause_to_read=True):
    direction = 1 if total_px >= 0 else -1
    remaining = abs(total_px)
    steps = random.randint(4, 10)
    profile = [ease_in_out(i / steps) for i in range(steps + 1)]
    deltas = [profile[i + 1] - profile[i] for i in range(steps)]

    for d in deltas:
        px = max(1, int(remaining * d)) * direction
        await state.page.mouse.wheel(0, px)
        await asyncio.sleep(random.uniform(0.03, 0.13))

    if pause_to_read and random.random() < 0.4:
        await asyncio.sleep(random.uniform(0.5, 3.0))


# ---------------------------------------------------------------------------
# Task implementations
# ---------------------------------------------------------------------------

async def task_signup(state: AgentState):
    print("[task] signup")
    await state.page.goto(f"{state.base_url}/register")
    await think()
    suffix = random.randint(1000, 9999)
    username = f"{state.username}_{suffix}"
    email = f"{username}@example-research.test"
    password = "ResearchPass!" + str(random.randint(100, 999))

    await human_type(state, "#username", username)
    await think(median=0.3)
    await human_type(state, "#email", email)
    await think(median=0.3)
    await human_type(state, "#password", password)
    await think(median=0.3)
    await human_type(state, "#password2", password)
    await think(median=0.4)
    await human_click(state, "#remember_me")
    await think(median=0.3)
    await human_click(state, "#accept_terms")
    await think(median=0.5)
    await human_click(state, "#submit")
    await think(median=1.0)
    return username, password


async def task_login(state: AgentState, username, password):
    print("[task] login")
    await state.page.goto(f"{state.base_url}/login")
    await think()
    await human_type(state, "#username", username)
    await think(median=0.3)
    await human_type(state, "#password", password)
    await think(median=0.4)
    await human_click(state, "#submit")
    await think(median=1.0)


async def task_search(state: AgentState):
    print("[task] search")
    queries = ["python tips", "sunset photos", "recipe pasta", "marathon", "keyboard build"]
    query = random.choice(queries)
    await human_click(state, "#q")
    await human_type(state, "#q", query)
    await think(median=0.4)
    await state.page.keyboard.press("Enter")
    await think(median=1.2)
    for _ in range(random.randint(2, 5)):
        await human_scroll(state, random.randint(150, 400))


async def task_feed_browsing(state: AgentState, minutes=2.5):
    print("[task] feed browsing")
    await state.page.goto(f"{state.base_url}/")
    end_time = asyncio.get_event_loop().time() + minutes * 60
    clicked = 0
    liked = 0
    commented = 0

    while asyncio.get_event_loop().time() < end_time:
        posts = await state.page.query_selector_all(".post")
        if posts:
            post = random.choice(posts)
            text_el = await post.query_selector("p, .post-body, .body")
            text = await text_el.inner_text() if text_el else ""
            await asyncio.sleep(min(reading_time(text), 6.0))

            if clicked < 5 and random.random() < 0.4:
                link = await post.query_selector("a")
                if link:
                    box = await link.bounding_box()
                    if box:
                        await move_mouse_human(
                            state, box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
                        )
                        await think(median=0.2)
                        await link.click()
                        clicked += 1
                        await think(median=1.0)
                        await state.page.go_back()
                        await think(median=0.5)

            if liked < 3 and random.random() < 0.3:
                like_btn = await post.query_selector("[data-like], .like-btn, button.like")
                if like_btn:
                    box = await like_btn.bounding_box()
                    if box:
                        await move_mouse_human(
                            state, box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
                        )
                        await think(median=0.2)
                        await like_btn.click()
                        liked += 1

            if commented < 2 and random.random() < 0.15:
                comment_input = await post.query_selector("input[name=body], textarea[name=body]")
                if comment_input:
                    comments = ["Nice post!", "Interesting, thanks for sharing.", "Love this."]
                    await human_type(state, "input[name=body], textarea[name=body]", random.choice(comments))
                    submit_btn = await post.query_selector("button[type=submit]")
                    if submit_btn:
                        await think(median=0.4)
                        await submit_btn.click()
                        commented += 1

        await human_scroll(state, random.randint(200, 500))
        await think(median=0.6, idle_chance=0.1)


async def task_profile_edit(state: AgentState):
    print("[task] profile edit")
    await state.page.goto(f"{state.base_url}/edit_profile")
    await think()
    bios = [
        "Researching web behavior and automation.",
        "Just here browsing and reading posts.",
        "Enjoy long walks and short novels.",
    ]
    await human_type(state, "#about_me", random.choice(bios))
    await think(median=0.6)
    await human_click(state, "#submit")
    await think(median=1.0)


async def task_timed_widget(state: AgentState):
    print("[task] timed widget")
    await state.page.goto(f"{state.base_url}/daily")
    await think()
    started = await human_click(state, "#start-timer")
    if started:
        await asyncio.sleep(random.uniform(3.0, 8.0))
        # occasional check-in glances at the timer display
        for _ in range(random.randint(1, 3)):
            await think(median=1.5, idle_chance=0.2)


async def task_free_browsing(state: AgentState, minutes=5.0):
    print("[task] free browsing")
    actions = [task_search, task_feed_browsing]
    random.shuffle(actions)
    end_time = asyncio.get_event_loop().time() + minutes * 60
    while asyncio.get_event_loop().time() < end_time:
        action = random.choice(actions)
        if action is task_feed_browsing:
            await action(state, minutes=random.uniform(0.5, 1.5))
        else:
            await action(state)
        await think(median=0.8, idle_chance=0.1)


async def task_error_recovery(state: AgentState):
    print("[task] error recovery")
    await state.page.goto(f"{state.base_url}/register")
    await think()
    username = f"{state.username}_err{random.randint(100,999)}"
    await human_type(state, "#username", username)
    await think(median=0.3)
    await human_type(state, "#email", f"{username}@example-research.test")
    await think(median=0.3)
    await human_type(state, "#password", "MismatchOne!1")
    await think(median=0.3)
    await human_type(state, "#password2", "MismatchTwo!2")  # deliberate mismatch
    await think(median=0.4)
    await human_click(state, "#submit")
    await think(median=1.0)  # "notice" the error

    # correct it
    try:
        await state.page.fill("#password2", "")
    except Exception:
        pass
    await think(median=0.5)
    await human_type(state, "#password2", "MismatchOne!1")
    await think(median=0.4)
    await human_click(state, "#submit")
    await think(median=1.0)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run(base_url, username, headless):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(viewport={"width": 1280, "height": 800})
        await context.set_extra_http_headers(build_run_headers())
        page = await context.new_page()
        state = AgentState(page, base_url, username)

        real_username, password = await task_signup(state)
        await task_login(state, real_username, password)
        await task_search(state)
        await task_feed_browsing(state, minutes=2.5)
        await task_profile_edit(state)
        await task_timed_widget(state)
        await task_free_browsing(state, minutes=5.0)
        await task_error_recovery(state)

        print(f"[done] session complete for {real_username}")
        await browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="https://charweb.net")
    parser.add_argument("--username", default="ai_L5_run1",
                         help="base username; a random suffix is appended at signup. "
                              "MUST start with 'ai_L5' (see module docstring) -- "
                              "research/label_architecture.py keys off this prefix.")
    parser.add_argument("--headless", action="store_true", default=False)
    args = parser.parse_args()

    asyncio.run(run(args.base_url, args.username, args.headless))
