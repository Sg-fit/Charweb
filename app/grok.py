import csv
import random
import time
import uuid
from playwright.sync_api import sync_playwright

try:
    from run_labels import build_run_headers
except ImportError:
    from app.run_labels import build_run_headers

BASE_URL = "https://charweb.net"
PASSWORD = "TestPassword123!"
TRIALS_PER_LEVEL = 3
HEADLESS = True

def get_style(level):
    if level == 1:   # naive_bot
        return {"delay": 0, "pause": 0.05, "typo": 0, "name": "naive_bot"}
    elif level == 2:  # basic_random
        return {"delay": lambda: random.randint(20, 60), "pause": lambda: random.uniform(0, 0.3), "typo": 0, "name": "basic_random"}
    elif level == 3:  # humanlike
        return {"delay": lambda: random.randint(60, 220), "pause": lambda: random.uniform(0.3, 2.0), "typo": 0.05, "name": "humanlike"}
    else:  # highly_humanlike
        return {"delay": lambda: random.randint(40, 350), "pause": lambda: random.uniform(0.5, 4.0), "typo": 0.12, "name": "highly_humanlike"}

def human_type(page, selector, text, style):
    page.click(selector, timeout=8000)
    time.sleep(0.4)
    for char in text:
        delay = style["delay"]() if callable(style["delay"]) else style["delay"]
        page.keyboard.type(char, delay=delay)
        if random.random() < style["typo"]:
            page.keyboard.type(random.choice("abcdefghijklmnopqrstuvwxyz"), delay=80)
            time.sleep(0.3)
            page.keyboard.press("Backspace")
        if random.random() < 0.12:
            time.sleep(style["pause"]() if callable(style["pause"]) else style["pause"])

def run_action(page, action_name, func, username, level, trial, manifest):
    try:
        func()
        status = "success"
        print(f"  [OK] {action_name}")
    except Exception as e:
        status = "failed"
        print(f"  [FAIL] {action_name} - {e}")
    manifest.append([username, level, get_style(level)["name"], trial, action_name, status])
    try:
        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    except:
        pass
    time.sleep(2)

def main():
    manifest = [["username", "level", "level_name", "trial", "action_name", "status"]]

    with sync_playwright() as p:
        for level in [1, 2, 3, 4]:
            for trial in range(1, TRIALS_PER_LEVEL + 1):
                RUN_ID = uuid.uuid4().hex[:8]
                username = f"ai_L{level}_t{trial}_{RUN_ID}"
                style = get_style(level)
                print(f"\nRunning {style['name']} (Level {level}) Trial {trial} - {username}")

                browser = p.chromium.launch(headless=HEADLESS)
                page = browser.new_page()
                page.set_extra_http_headers(build_run_headers())

                # 1. Register
                def do_register():
                    page.goto(f"{BASE_URL}/register", wait_until="domcontentloaded")
                    page.fill("input[name='username']", username)
                    page.fill("input[name='email']", f"{username}@test.com")
                    human_type(page, "input[name='password']", PASSWORD, style)
                    human_type(page, "input[name='password2']", PASSWORD, style)
                    page.check("input[name='accept_terms']")
                    page.click("input[type='submit']")
                    page.wait_for_load_state("networkidle", timeout=15000)
                run_action(page, "Register", do_register, username, level, trial, manifest)

                # 2. Login
                def do_login():
                    page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded")
                    human_type(page, "input[name='username']", username, style)
                    human_type(page, "input[name='password']", PASSWORD, style)
                    page.click("input[type='submit']")
                    page.wait_for_load_state("networkidle", timeout=15000)
                run_action(page, "Login", do_login, username, level, trial, manifest)

                # 3. Write a post
                run_action(page, "Write post", lambda: (
                    page.goto(BASE_URL, wait_until="domcontentloaded"),
                    page.wait_for_selector("#post", timeout=30000),
                    human_type(page, "#post", "This is a test post about my day on Charweb.", style),
                    page.click("input[type='submit']")
                ), username, level, trial, manifest)

                # 4. Scroll explore
                run_action(page, "Scroll explore", lambda: (
                    page.goto(f"{BASE_URL}/explore", wait_until="domcontentloaded"),
                    [page.mouse.wheel(0, 400) or time.sleep(0.6) for _ in range(8)]
                ), username, level, trial, manifest)

                # 5. Search
                run_action(page, "Search", lambda: (
                    page.goto(BASE_URL, wait_until="domcontentloaded"),
                    page.fill("input[name='q']", "charweb"),
                    page.keyboard.press("Enter")
                ), username, level, trial, manifest)

                # 6. Edit profile
                run_action(page, "Edit profile", lambda: (
                    page.goto(f"{BASE_URL}/edit_profile", wait_until="domcontentloaded"),
                    human_type(page, "#about_me", "I am a test AI agent studying human behavior.", style),
                    page.click("input[type='submit']")
                ), username, level, trial, manifest)

                # 7. Read Terms
                run_action(page, "Read Terms", lambda: (
                    page.goto(f"{BASE_URL}/terms", wait_until="domcontentloaded"),
                    time.sleep(3),
                    page.go_back()
                ), username, level, trial, manifest)

                # 8. Timed task
                run_action(page, "Timed register+post", lambda: (
                    # /register redirects already-authenticated users to /home,
                    # so the earlier login in this session must be cleared first.
                    page.goto(f"{BASE_URL}/logout", wait_until="domcontentloaded"),
                    (start := time.time()),
                    page.goto(f"{BASE_URL}/register", wait_until="domcontentloaded"),
                    page.fill("input[name='username']", username + "_m8"),
                    page.fill("input[name='email']", f"{username}_m8@test.com"),
                    human_type(page, "input[name='password']", PASSWORD, style),
                    human_type(page, "input[name='password2']", PASSWORD, style),
                    page.check("input[name='accept_terms']"),
                    page.click("input[type='submit']"),
                    time.sleep(3),
                    page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded"),
                    human_type(page, "input[name='username']", username + "_m8", style),
                    human_type(page, "input[name='password']", PASSWORD, style),
                    page.click("input[type='submit']"),
                    time.sleep(3),
                    page.goto(BASE_URL, wait_until="domcontentloaded"),
                    page.wait_for_selector("#post", timeout=20000),
                    human_type(page, "#post", "Hello everyone, this is my introduction.", style),
                    page.click("input[type='submit']"),
                    print(f"Timed task took {time.time() - start:.1f} seconds")
                ), username, level, trial, manifest)

                # 9. Pause while typing
                run_action(page, "Pause while typing", lambda: (
                    page.goto(BASE_URL, wait_until="domcontentloaded"),
                    page.wait_for_selector("#post", timeout=20000),
                    human_type(page, "#post", "I am starting to write something and then I will stop typing. ", style),
                    time.sleep(30),
                    human_type(page, "#post", "Now I am finishing my thought.", style),
                    page.click("input[type='submit']")
                ), username, level, trial, manifest)

                # 10. Logout + Forgot Password
                run_action(page, "Logout + Forgot Password", lambda: (
                    page.goto(f"{BASE_URL}/logout", wait_until="domcontentloaded"),
                    page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded"),
                    page.click("a[href*='reset_password_request']"),
                    page.fill("input[name='email']", f"{username}@test.com"),
                    page.click("input[type='submit']"),
                    time.sleep(3),
                    page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded"),
                    human_type(page, "input[name='username']", username, style),
                    human_type(page, "input[name='password']", PASSWORD, style),
                    page.click("input[type='submit']")
                ), username, level, trial, manifest)

                # 11. Daily sign in
                run_action(page, "Daily sign in", lambda: (
                    page.goto(f"{BASE_URL}/daily", wait_until="domcontentloaded"),
                    page.locator("button:has-text('Sign In')").click(timeout=8000) if page.locator("button:has-text('Sign In')").count() > 0 else None
                ), username, level, trial, manifest)

                # 12. Create character
                run_action(page, "Create character", lambda: (
                    page.goto(f"{BASE_URL}/daily", wait_until="domcontentloaded"),
                    page.fill("input[name='name']", "Hero1"),
                    page.click("button:has-text('Create Character')")
                ), username, level, trial, manifest)

                # 13. Dungeon
                run_action(page, "Dungeon explore", lambda: (
                    page.goto(f"{BASE_URL}/daily", wait_until="domcontentloaded"),
                    [page.locator("button:has-text('Explore')").click(timeout=5000) or time.sleep(1) for _ in range(3)]
                ), username, level, trial, manifest)

                # 14. Shop
                run_action(page, "Buy from shop", lambda: (
                    page.goto(f"{BASE_URL}/daily/shop", wait_until="domcontentloaded"),
                    page.locator("button:has-text('Buy'), input[value='Buy']").first.click(timeout=8000)
                ), username, level, trial, manifest)

                # 15. Equip item
                run_action(page, "Equip item", lambda: (
                    page.goto(f"{BASE_URL}/daily", wait_until="domcontentloaded"),
                    page.locator("form[action*='/daily/equip/'] button, form[action*='/daily/equip/'] input[type='submit']").first.click(timeout=8000)
                ), username, level, trial, manifest)

                # 16. Chat
                def send_chat_message():
                    page.goto(f"{BASE_URL}/chat/", wait_until="domcontentloaded")
                    if page.locator(".user-item").count() > 0:
                        page.locator(".user-item").first.click(timeout=15000)
                        human_type(page, "#message-input", "Hello from the AI test agent!", style)
                        page.click("#send-btn")
                    else:
                        print("No chat users available, skipping")
                run_action(page, "Send chat message", send_chat_message, username, level, trial, manifest)

                # 17. Ranking
                run_action(page, "Visit ranking", lambda: (
                    page.goto(f"{BASE_URL}/ranking", wait_until="domcontentloaded"),
                    page.mouse.wheel(0, 400)
                ), username, level, trial, manifest)

                # 18. Allocate points
                def allocate_points():
                    page.goto(f"{BASE_URL}/daily", wait_until="domcontentloaded")
                    if page.locator("form[action='/daily/allocate']").count() > 0:
                        page.locator("form[action='/daily/allocate']").first.locator("button, input[type='submit']").first.click(timeout=8000)
                    else:
                        print("No points to allocate, skipping")
                run_action(page, "Allocate points", allocate_points, username, level, trial, manifest)

                browser.close()
                time.sleep(20)

    with open("ai_manifest.csv", "w", newline="") as f:
        csv.writer(f).writerows(manifest)

    print("\nAll 18 actions completed for all trials!")

if __name__ == "__main__":
    main()