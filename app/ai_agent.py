from playwright.sync_api import sync_playwright
import time
import random
import csv
from datetime import datetime

try:
    from run_labels import build_run_headers
except ImportError:
    from app.run_labels import build_run_headers

BASE_URL = "https://charweb.net"
PASSWORD = "TestPass123!"
TRIALS_PER_LEVEL = 3
MANIFEST_PATH = "ai_trials_manifest.csv"

LEVEL_CONFIG = {
    1: {"name": "naive_bot", "delay": 5, "pause": 0.05, "mouse_steps": 3, "typo_rate": 0.0},
    2: {"name": "basic_random", "delay": lambda: random.randint(25, 70), "pause": lambda: random.uniform(0.1, 0.4), "mouse_steps": 8, "typo_rate": 0.0},
    3: {"name": "humanlike", "delay": lambda: random.randint(60, 220), "pause": lambda: random.uniform(0.3, 2.0), "mouse_steps": 20, "typo_rate": 0.05},
    4: {"name": "highly_humanlike", "delay": lambda: random.randint(40, 350), "pause": lambda: random.uniform(0.5, 4.0), "mouse_steps": 30, "typo_rate": 0.12}
}

def human_type(page, selector, text, level):
    config = LEVEL_CONFIG[level]
    page.click(selector)
    time.sleep(0.3)
    
    for char in text:
        delay = config["delay"]() if callable(config["delay"]) else config["delay"]
        page.keyboard.type(char, delay=delay)
        
        if random.random() < config["typo_rate"]:
            page.keyboard.type(random.choice("abcdefghijklmnopqrstuvwxyz"), delay=80)
            time.sleep(0.3)
            page.keyboard.press("Backspace")
        
        if random.random() < 0.07 and level >= 3:
            time.sleep(random.uniform(0.4, 1.5))

def natural_mouse_move(page, x, y, level):
    steps = LEVEL_CONFIG[level]["mouse_steps"]
    page.mouse.move(x, y, steps=steps)

def human_pause(level):
    pause = LEVEL_CONFIG[level]["pause"]
    time.sleep(pause() if callable(pause) else pause)

def log_trial(username, level, trial, status):
    with open(MANIFEST_PATH, 'a', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([username, level, LEVEL_CONFIG[level]["name"], trial, status])

def run_full_mission(level, trial):
    username = f"ai_L{level}_t{trial}"
    print(f"🚀 Level {level} ({LEVEL_CONFIG[level]['name']}) Trial {trial} - {username}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_extra_http_headers(build_run_headers())

        try:
            # Register
            page.goto(f"{BASE_URL}/register")
            human_pause(level)
            page.fill("input[name='username']", username)
            page.fill("input[name='email']", f"{username}@test.com")
            human_type(page, "input[name='password']", PASSWORD, level)
            human_type(page, "input[name='password2']", PASSWORD, level)
            page.check("input[name='accept_terms']")
            page.click("button[type='submit']")
            time.sleep(5)

            # Login
            page.goto(f"{BASE_URL}/login")
            human_type(page, "input[name='username']", username, level)
            human_type(page, "input[name='password']", PASSWORD, level)
            page.click("button[type='submit']")
            time.sleep(5)

            # Home + Post
            page.goto(BASE_URL)
            page.wait_for_selector("textarea", timeout=20000)
            human_type(page, "textarea", "This is a realistic test post from AI agent.", level)
            page.click("button[type='submit']")
            time.sleep(4)

            # Explore
            page.goto(f"{BASE_URL}/explore")
            for _ in range(6):
                page.mouse.wheel(0, random.randint(300, 700))
                human_pause(level)

            # Search
            page.goto(BASE_URL)
            search = page.locator("input[placeholder*='search' i], input[type='search']").first
            if search:
                human_type(page, search, "test", level)
                page.keyboard.press("Enter")
                time.sleep(3)

            # More actions (daily, chat, etc.) can be added here

            print(f"✅ Completed")
            log_trial(username, level, trial, "success")

        except Exception as e:
            print(f"❌ Failed: {e}")
            log_trial(username, level, trial, "failed")
        finally:
            time.sleep(20)
            browser.close()

def main():
    with open(MANIFEST_PATH, 'w', newline='') as f:
        csv.writer(f).writerow(["username", "level", "level_name", "trial", "status"])

    for level in [1,2,3,4]:
        for trial in range(1, TRIALS_PER_LEVEL + 1):
            run_full_mission(level, trial)
            time.sleep(10)

if __name__ == "__main__":
    main()