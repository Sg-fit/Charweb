"""
Multi-level, multi-trial AI agent — all 10 missions on charweb.net

Install:
    pip install playwright
    playwright install chromium

Run:
    python ai_missions.py
"""

import time
import random
import csv
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ---- CONFIGURE ----
BASE_URL = "https://charweb.net"
PASSWORD = "SuperSecret123!"
TRIALS_PER_LEVEL = 3
MANIFEST_PATH = "ai_missions_manifest.csv"
# -------------------

LEVELS = {
    1: {
        "name": "naive_bot",
        "use_real_typing": False,
        "typing_delay": (0, 0),
        "mouse_steps": (1, 1),
        "pause": (0, 0),
        "error_rate": 0.0,
    },
    2: {
        "name": "basic_random",
        "use_real_typing": True,
        "typing_delay": (20, 60),
        "mouse_steps": (3, 8),
        "pause": (0, 0.3),
        "error_rate": 0.0,
    },
    3: {
        "name": "humanlike",
        "use_real_typing": True,
        "typing_delay": (60, 220),
        "mouse_steps": (10, 25),
        "pause": (0.3, 2.0),
        "error_rate": 0.05,
    },
    4: {
        "name": "highly_humanlike",
        "use_real_typing": True,
        "typing_delay": (40, 350),
        "mouse_steps": (15, 40),
        "pause": (0.5, 4.0),
        "error_rate": 0.12,
    },
}

POST_TEXT = (
    "Hello everyone, I am an automated agent exploring this platform. "
    "I find the interface quite intuitive and easy to navigate. "
    "The registration process was straightforward and well-designed."
)

INTRO_POST = (
    "Hello! My name is an AI agent participating in a behavioral study. "
    "I was created to simulate human-like interactions on web platforms. "
    "I enjoy browsing through posts and reading what others have shared. "
    "My favorite topic to explore is the intersection of technology and human behavior. "
    "I look forward to contributing meaningful content to this community."
)


# ── helpers ──────────────────────────────────────────────────────────────

def htype(page, selector, text, cfg):
    if not cfg["use_real_typing"]:
        page.fill(selector, text)
        return
    el = page.locator(selector)
    el.click()
    for ch in text:
        if random.random() < cfg["error_rate"]:
            wrong = random.choice("abcdefghijklmnopqrstuvwxyz")
            el.type(wrong, delay=random.randint(*cfg["typing_delay"]))
            time.sleep(random.uniform(0.1, 0.4))
            page.keyboard.press("Backspace")
            time.sleep(random.uniform(0.05, 0.2))
        el.type(ch, delay=random.randint(*cfg["typing_delay"]))
        if random.random() < 0.07:
            time.sleep(random.uniform(*cfg["pause"]))


def mmove(page, cfg, n=6):
    for _ in range(n):
        page.mouse.move(
            random.randint(50, 900),
            random.randint(50, 600),
            steps=random.randint(*cfg["mouse_steps"])
        )
        time.sleep(random.uniform(0.05, 0.25))


def pause(cfg):
    if cfg["pause"][1] > 0:
        time.sleep(random.uniform(*cfg["pause"]))


def flush(page):
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    time.sleep(2)


# ── missions ─────────────────────────────────────────────────────────────

def m1_register(page, username, email, cfg):
    """Mission 1 — Account registration"""
    page.goto(f"{BASE_URL}/register")
    mmove(page, cfg, 3)
    htype(page, "#username", username, cfg)
    htype(page, "#email", email, cfg)
    htype(page, "#password", PASSWORD, cfg)
    htype(page, "#password2", PASSWORD, cfg)
    pause(cfg)
    page.check("#accept_terms")
    pause(cfg)
    page.click("input[type=submit]")
    page.wait_for_load_state("networkidle")


def m2_login_with_typo(page, username, cfg):
    """Mission 2 — Login with deliberate typo then correct"""
    page.goto(f"{BASE_URL}/login")
    mmove(page, cfg, 2)
    htype(page, "#username", username, cfg)
    # deliberately wrong password
    htype(page, "#password", PASSWORD[:-2] + "XX", cfg)
    pause(cfg)
    page.click("input[type=submit]")
    page.wait_for_load_state("networkidle")
    time.sleep(random.uniform(0.5, 1.5))   # "notice" the error
    # clear and retype correctly
    page.fill("#username", "")
    page.fill("#password", "")
    htype(page, "#username", username, cfg)
    htype(page, "#password", PASSWORD, cfg)
    pause(cfg)
    page.click("input[type=submit]")
    page.wait_for_load_state("networkidle")


def m3_compose_post(page, cfg, text=None):
    """Mission 3 — Free-form post composition"""
    page.goto(f"{BASE_URL}/")
    mmove(page, cfg, 3)
    htype(page, "#post", text or POST_TEXT, cfg)
    pause(cfg)
    page.click("input[type=submit]")
    page.wait_for_load_state("networkidle")


def m4_scroll_explore(page, cfg):
    """Mission 4 — Scroll through /explore to find the first post"""
    page.goto(f"{BASE_URL}/explore")
    time.sleep(random.uniform(0.5, 1.5))
    scrolled = 0
    while scrolled < 8000:
        delta = random.randint(200, 600)
        page.mouse.wheel(0, delta)
        scrolled += delta
        time.sleep(random.uniform(0.3, 1.2))
        pause(cfg)
    # scroll back up slightly as if reading
    page.mouse.wheel(0, -random.randint(100, 300))
    time.sleep(random.uniform(0.5, 1.0))


def m5_search(page, cfg):
    """Mission 5 — Search for a keyword (press Enter since search bar may not submit otherwise)"""
    page.goto(f"{BASE_URL}/explore")
    mmove(page, cfg, 2)
    try:
        search_box = page.locator("input[name='q']")
        search_box.click()
        htype(page, "input[name='q']", "dear user", cfg)
        pause(cfg)
        page.keyboard.press("Enter")
        page.wait_for_load_state("networkidle")
    except PWTimeout:
        pass


def m6_edit_profile(page, username, cfg):
    """Mission 6 — Edit profile about-me"""
    page.goto(f"{BASE_URL}/edit_profile")
    mmove(page, cfg, 3)
    page.fill("#about_me", "")
    htype(page, "#about_me",
          "I am an automated test agent. I enjoy simulating human-like browsing behavior.", cfg)
    pause(cfg)
    page.click("input[type=submit]")
    page.wait_for_load_state("networkidle")


def m7_read_terms(page, cfg):
    """Mission 7 — Follow the Terms link at the bottom of the login page"""
    page.goto(f"{BASE_URL}/login")
    mmove(page, cfg, 2)
    try:
        page.get_by_text("Terms of Service").click()
        page.wait_for_load_state("networkidle")
        time.sleep(random.uniform(1.5, 4.0))   # "reading" time
        mmove(page, cfg, 4)
        page.go_back()
        page.wait_for_load_state("networkidle")
    except PWTimeout:
        pass


def m8_timed_registration_post(page, username, cfg):
    """Mission 8 — Register AND post an intro within 60 seconds"""
    email = f"{username}_m8@example.com"
    start = time.time()
    page.goto(f"{BASE_URL}/register")
    htype(page, "#username", username, cfg)
    htype(page, "#email", email, cfg)
    htype(page, "#password", PASSWORD, cfg)
    htype(page, "#password2", PASSWORD, cfg)
    page.check("#accept_terms")
    page.click("input[type=submit]")
    page.wait_for_load_state("networkidle")
    # login
    page.goto(f"{BASE_URL}/login")
    htype(page, "#username", username, cfg)
    htype(page, "#password", PASSWORD, cfg)
    page.click("input[type=submit]")
    page.wait_for_load_state("networkidle")
    # post intro
    page.goto(f"{BASE_URL}/")
    htype(page, "#post", INTRO_POST, cfg)
    page.click("input[type=submit]")
    page.wait_for_load_state("networkidle")
    elapsed = time.time() - start
    print(f"    Mission 8 completed in {elapsed:.1f}s (limit: 60s)")


def m9_idle_distraction(page, cfg):
    """Mission 9 — Start composing, idle 30s, then finish"""
    page.goto(f"{BASE_URL}/")
    mmove(page, cfg, 2)
    # type the first sentence
    htype(page, "#post", "This is a test post where I pause midway.", cfg)
    # idle / distracted
    time.sleep(30)
    mmove(page, cfg, 3)
    # finish and submit
    htype(page, "#post", " Returning now after a brief distraction.", cfg)
    pause(cfg)
    page.click("input[type=submit]")
    page.wait_for_load_state("networkidle")


def m10_repeat_task_forgot_password(page, username, cfg):
    """Mission 10 — Logout, try forgot-password (no email), login, post again"""
    # logout
    try:
        page.goto(f"{BASE_URL}/logout")
        page.wait_for_load_state("networkidle")
    except Exception:
        pass

    # try forgot-password flow
    page.goto(f"{BASE_URL}/login")
    mmove(page, cfg, 2)
    try:
        page.get_by_text("Forgot").click()
        page.wait_for_load_state("networkidle")
        # fill in the email field
        htype(page, "input[name='email']",
              f"{username}@example.com", cfg)
        pause(cfg)
        page.click("input[type=submit]")
        page.wait_for_load_state("networkidle")
        time.sleep(random.uniform(1.0, 2.5))
    except PWTimeout:
        pass

    # now actually log back in with real password
    page.goto(f"{BASE_URL}/login")
    htype(page, "#username", username, cfg)
    htype(page, "#password", PASSWORD, cfg)
    pause(cfg)
    page.click("input[type=submit]")
    page.wait_for_load_state("networkidle")

    # repeat post
    m3_compose_post(page, cfg, text=POST_TEXT + " (second run)")


# ── trial runner ─────────────────────────────────────────────────────────

def run_trial(level_num, cfg, trial_num, headless=True):
    username = f"ai_L{level_num}_t{trial_num}"
    email = f"{username}@example.com"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()
        try:
            print(f"    [M1] Register")
            m1_register(page, username, email, cfg)

            print(f"    [M2] Login with typo")
            m2_login_with_typo(page, username, cfg)

            print(f"    [M3] Compose post")
            m3_compose_post(page, cfg)

            print(f"    [M4] Scroll explore")
            m4_scroll_explore(page, cfg)

            print(f"    [M5] Search")
            m5_search(page, cfg)

            print(f"    [M6] Edit profile")
            m6_edit_profile(page, username, cfg)

            print(f"    [M7] Read terms")
            m7_read_terms(page, cfg)

            print(f"    [M9] Idle distraction")
            m9_idle_distraction(page, cfg)

            print(f"    [M10] Repeat + forgot password")
            m10_repeat_task_forgot_password(page, username, cfg)

            flush(page)
            time.sleep(10)

        except Exception as e:
            print(f"    ERROR: {e}")
        finally:
            browser.close()

    # Mission 8 runs as a separate account (registers its own user)
    m8_username = f"ai_L{level_num}_t{trial_num}_m8"
    print(f"    [M8] Timed registration+post ({m8_username})")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()
        try:
            m8_timed_registration_post(page, m8_username, cfg)
            flush(page)
            time.sleep(10)
        except Exception as e:
            print(f"    M8 ERROR: {e}")
        finally:
            browser.close()

    return username, m8_username


# ── main ─────────────────────────────────────────────────────────────────

def run_experiment(headless=True):
    rows = []
    for level_num, cfg in LEVELS.items():
        print(f"\n=== Level {level_num}: {cfg['name']} ===")
        for trial in range(1, TRIALS_PER_LEVEL + 1):
            print(f"  Trial {trial}/{TRIALS_PER_LEVEL}")
            username, m8_username = run_trial(level_num, cfg, trial, headless=headless)
            rows.append({"username": username, "level": level_num,
                         "level_name": cfg["name"], "trial": trial, "mission": "1-7,9,10"})
            rows.append({"username": m8_username, "level": level_num,
                         "level_name": cfg["name"], "trial": trial, "mission": "8"})

    with open(MANIFEST_PATH, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["username", "level", "level_name", "trial", "mission"])
        w.writeheader()
        w.writerows(rows)
    print(f"\nDone. Manifest → {MANIFEST_PATH}")


if __name__ == "__main__":
    run_experiment(headless=True)
