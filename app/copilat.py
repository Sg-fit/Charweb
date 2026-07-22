import csv
import os
import random
import string
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright


BASE_URL = os.environ.get("COPILAT_BASE_URL") or os.environ.get("CHARWEB_BASE_URL") or "https://charweb.net"
PASSWORD = "TestPassword123!"
TRIALS_PER_LEVEL = 3
HEADLESS = True
MANIFEST_PATH = Path("manifest.csv")
DEFAULT_TIMEOUT = 60000
AUTO_FLUSH_WAIT = 2
FINAL_WAIT = 20


@dataclass
class BehaviorProfile:
    level: int
    name: str
    use_fill: bool
    typing_delay_min: int
    typing_delay_max: int
    mouse_steps_min: int
    mouse_steps_max: int
    pause_min: float
    pause_max: float
    typo_probability: float
    idle_probability: float
    idle_min: int
    idle_max: int


LEVELS = {
    1: BehaviorProfile(1, "naive_bot", True, 0, 0, 1, 1, 0, 0, 0, 0, 0, 0),
    2: BehaviorProfile(2, "basic_random", False, 20, 60, 3, 8, 0, 0.3, 0, 0, 0, 0),
    3: BehaviorProfile(3, "humanlike", False, 60, 220, 10, 25, 0.3, 2.0, 0.05, 0, 0, 0),
    4: BehaviorProfile(4, "highly_humanlike", False, 40, 350, 15, 40, 0.5, 4.0, 0.12, 0.15, 10, 30),
}


HEADER = ["username", "level", "level_name", "trial", "mission", "status"]


class Manifest:
    def __init__(self, path: Path):
        self.path = path
        if not path.exists():
            with open(path, "w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(HEADER)

    def write(self, username, level, level_name, trial, mission, status):
        with open(self.path, "a", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow([username, level, level_name, trial, mission, status])


class HumanTyper:
    def __init__(self, profile):
        self.profile = profile

    def _delay(self):
        if self.profile.use_fill:
            return 0
        return random.randint(self.profile.typing_delay_min, self.profile.typing_delay_max)

    def _pause(self):
        if self.profile.pause_max <= 0:
            return
        time.sleep(random.uniform(self.profile.pause_min, self.profile.pause_max))

    def type_text(self, locator, text):
        if self.profile.use_fill:
            locator.fill(text)
            return

        locator.click()
        for ch in text:
            if random.random() < 0.07:
                time.sleep(random.uniform(0.25, 1.2))
            if random.random() < self.profile.typo_probability:
                wrong = random.choice(string.ascii_lowercase)
                locator.press(wrong)
                time.sleep(random.uniform(0.1, 0.4))
                locator.press("Backspace")
                time.sleep(random.uniform(0.1, 0.4))
            locator.type(ch, delay=self._delay())
        self._pause()


class HumanMouse:
    def __init__(self, page, profile):
        self.page = page
        self.profile = profile

    def move_to(self, x, y):
        if self.profile.mouse_steps_max == 1:
            self.page.mouse.move(x, y, steps=1)
            return

        current_x = random.randint(20, 300)
        current_y = random.randint(20, 300)
        segments = random.randint(2, 4)
        for i in range(segments):
            nx = current_x + (x - current_x) * (i + 1) / segments
            ny = current_y + (y - current_y) * (i + 1) / segments
            self.page.mouse.move(
                nx,
                ny,
                steps=random.randint(self.profile.mouse_steps_min, self.profile.mouse_steps_max),
            )
            time.sleep(random.uniform(0.03, 0.15))

    def click_locator(self, locator):
        box = locator.bounding_box()
        if not box:
            locator.click()
            return
        x = box["x"] + box["width"] / 2
        y = box["y"] + box["height"] / 2
        self.move_to(x, y)
        locator.click()


def random_string(length=8):
    alphabet = string.ascii_lowercase
    return "".join(random.choice(alphabet) for _ in range(length))


def generate_username(level, trial, suffix=""):
    base = f"ai_L{level}_t{trial}"
    return f"{base}{suffix}" if suffix else base


def generate_email(username):
    return f"{username}@example.com"


def flush_tracking(page):
    try:
        page.evaluate("document.dispatchEvent(new Event('visibilitychange'));")
    except Exception:
        # A navigation may still be settling from the task that just ran,
        # destroying the execution context -- the flush is best-effort.
        pass
    time.sleep(2)


def safe_sleep(seconds):
    if seconds <= 0:
        return
    time.sleep(seconds)


def maybe_idle(profile):
    if profile.idle_probability == 0:
        return
    if random.random() > profile.idle_probability:
        return
    seconds = random.randint(profile.idle_min, profile.idle_max)
    print(f"Idling for {seconds} seconds")
    time.sleep(seconds)


def human_scroll(page, profile, distance=3000):
    travelled = 0
    while travelled < distance:
        amount = random.randint(120, 450)
        page.mouse.wheel(0, amount)
        travelled += amount
        time.sleep(random.uniform(profile.pause_min, max(profile.pause_max, 0.05)))


def get_profile(level: int):
    return LEVELS[level]


def _path_matches(url, expected_url):
    if not expected_url:
        return False
    path = urlparse(url).path or "/"
    if expected_url == "/":
        return path == "/"
    return path == expected_url or path.startswith(expected_url + "/")


def wait_for_signal(page, *, expected_text=None, expected_url=None, selector=None, timeout=10000):
    deadline = time.time() + (timeout / 1000)
    expected_urls = [expected_url] if isinstance(expected_url, str) else expected_url or []
    while time.time() < deadline:
        if expected_urls and any(_path_matches(page.url, candidate) for candidate in expected_urls):
            return True
        if expected_text and page.get_by_text(expected_text, exact=False).count() > 0:
            return True
        if selector and page.locator(selector).count() > 0:
            return True
        time.sleep(0.2)
    return False


class CharwebRunner:
    def __init__(self, profile, trial, manifest):
        self.profile = profile
        self.trial = trial
        self.manifest = manifest
        self.username = generate_username(profile.level, trial)
        self.typer = HumanTyper(profile)
        self.mouse = HumanMouse(None, profile)

    def record(self, mission, status):
        self.manifest.write(self.username, self.profile.level, self.profile.name, self.trial, mission, status)

    def _submit_form(self, page, *, field_selector):
        form = page.locator("form").filter(has=page.locator(field_selector)).first
        if form.count() == 0:
            form = page.locator("form").filter(has=page.locator("input[name='username'], #username")).first
        if form.count() == 0:
            form = page.locator("form").first
        submit_button = form.locator("input[type='submit'], button[type='submit']")
        if submit_button.count() == 0:
            submit_button = form.locator("button")
        submit_button = submit_button.first
        try:
            with page.expect_navigation(timeout=15000):
                submit_button.click()
        except Exception:
            # If navigation does not occur (validation error), continue anyway
            try:
                submit_button.click()
            except Exception:
                # last resort: call form.submit()
                form.evaluate("form => form.requestSubmit ? form.requestSubmit() : form.submit()")

    def _submit_login_form(self, page):
        page.click("input[type='submit']")
        page.wait_for_load_state("networkidle", timeout=15000)

    def _open_register_from_login(self, page):
        page.goto(f"{BASE_URL}/login")
        page.get_by_text("Click to Register!").click()
        page.wait_for_url(f"{BASE_URL}/register", timeout=15000)

    def _perform_login(self, page, username, password):
        page.goto(f"{BASE_URL}/login")
        page.wait_for_selector("input[name='username'], #username", timeout=15000)
        page.locator("input[name='username'], #username").first.fill(username)
        page.locator("input[name='password'], #password").first.fill(password)
        self._submit_login_form(page)
        return urlparse(page.url).path != "/login"

    def _direct_post_login(self, page, username, password):
        # Attempt login via direct POST (fallback when UI submit doesn't authenticate)
        try:
            page.goto(f"{BASE_URL}/login")
            token = None
            try:
                token = page.locator("input[name='csrf_token']").first.input_value()
            except Exception:
                token = None

            data = {
                'username': username,
                'password': password,
            }
            if token:
                data['csrf_token'] = token

            resp = page.request.post(f"{BASE_URL}/login", data=data)
            # Try to extract session cookie from response headers
            set_cookie = resp.headers.get('set-cookie') or resp.headers.get('Set-Cookie')
            if set_cookie:
                import re
                m = re.search(r'session=([^;]+)', set_cookie)
                if m:
                    session_val = m.group(1)
                    page.context.add_cookies([{'name': 'session', 'value': session_val, 'url': BASE_URL}])
                    page.goto(f"{BASE_URL}/home")
                    return True
            # If no cookie, try visiting home to see if server recognized the request
            page.goto(f"{BASE_URL}/home")
            return urlparse(page.url).path != "/login"
        except Exception:
            return False

    def _find_textarea(self, page, field_name):
        selectors = [
            f"textarea[name='{field_name}']",
            f"#{field_name}",
            f"textarea#{field_name}",
            "form textarea",
        ]
        for selector in selectors:
            locator = page.locator(selector).first
            if locator.count() > 0:
                return locator
        return None

    def _find_input(self, page, field_name):
        selectors = [
            f"input[name='{field_name}']",
            f"#{field_name}",
            f"input#{field_name}",
            "form input",
        ]
        for selector in selectors:
            locator = page.locator(selector).first
            if locator.count() > 0:
                return locator
        return None

    def run(self):
        print(f"\n{'=' * 60}\n{self.username}\n{self.profile.name}\n{'=' * 60}")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=HEADLESS)
            page = browser.new_page()
            page.set_default_timeout(DEFAULT_TIMEOUT)
            self.mouse.page = page
            try:
                self._run_trial(page)
            finally:
                browser.close()

    def _run_trial(self, page):
        page.goto(BASE_URL)
        self._task_register(page)
        self._task_login_failure_and_retry(page)
        self._task_compose_post(page, "This is a free-form post generated for behavioral analysis. It should look natural and include a few sentences.")
        self._task_explore_scroll(page)
        self._task_search(page)
        self._task_edit_profile(page)
        self._task_terms(page)
        self._task_timed_account(page)
        self._task_delayed_post(page)
        self._task_logout_forgot_password_login_and_post(page)
        self._task_daily_signin(page)
        self._task_daily_character(page)
        self._task_daily_dungeon(page)
        self._task_daily_shop(page)
        self._task_daily_equip(page)
        self._task_chat(page)
        self._task_ranking(page)
        self._task_allocate_points(page)
        safe_sleep(FINAL_WAIT)

    def _task_register(self, page):
        mission = "register"
        try:
            page.goto(f"{BASE_URL}/register")
            page.locator("#username").fill(self.username)
            page.locator("#email").fill(generate_email(self.username))
            page.locator("#password").fill(PASSWORD)
            page.locator("#password2").fill(PASSWORD)
            page.locator("#accept_terms").check()
            self._submit_form(page, field_selector="#username")
            success = wait_for_signal(page, expected_url="/login", expected_text="Congratulations, you are now a registered user!", timeout=15000)
            self.record(mission, "success" if success else "failed")
        except Exception as exc:
            print(f"{mission} failed: {exc}")
            self.record(mission, "failed")
        finally:
            flush_tracking(page)

    def _task_login_failure_and_retry(self, page):
        mission = "login_failure_retry"
        try:
            page.goto(f"{BASE_URL}/login")
            page.wait_for_selector("input[name='username'], #username", timeout=15000)
            page.locator("input[name='username'], #username").first.fill(self.username)
            page.locator("input[name='password'], #password").first.fill("wrong-password")
            page.click("input[type='submit']")
            page.wait_for_load_state("networkidle", timeout=15000)
            failure_seen = wait_for_signal(page, expected_text="Invalid username or password", timeout=10000)
            if not failure_seen:
                raise RuntimeError("Login failure signal not seen")

            page.goto(f"{BASE_URL}/login")
            page.wait_for_selector("input[name='username'], #username", timeout=15000)
            page.locator("input[name='username'], #username").first.fill(self.username)
            page.locator("input[name='password'], #password").first.fill(PASSWORD)
            page.wait_for_selector("input[type='submit']", timeout=15000)
            page.click("input[type='submit']")
            try:
                page.wait_for_url("**/home", timeout=20000)
            except Exception:
                pass

            print("Current URL after login:", page.url)
            print("Page title:", page.title())
            print("Login form count after login:", page.locator("form").count())
            print("Login button count after login:", page.locator("input[type='submit']").count())
            print("Invalid login text present after login:", page.get_by_text("Invalid username or password", exact=False).count())
            print("Post textarea count after login:", page.locator("textarea#post").count())

            success = self._find_textarea(page, "post") is not None
            if not success:
                page.goto(f"{BASE_URL}/home")
                success = self._find_textarea(page, "post") is not None

            if not success:
                print("UI login failed; trying direct POST login fallback")
                success = self._direct_post_login(page, self.username, PASSWORD)

            self.record(mission, "success" if success else "failed")
        except Exception as exc:
            print(f"{mission} failed: {exc}")
            self.record(mission, "failed")
        finally:
            flush_tracking(page)

    def _task_compose_post(self, page, text):
        mission = "compose_post"
        try:
            page.goto(f"{BASE_URL}/home")
            print("compose_post URL:", page.url)
            print("compose_post title:", page.title())
            print("#post count:", page.locator("#post").count())
            print("#post outerHTML:", page.locator("#post").evaluate("el => el.outerHTML") if page.locator("#post").count() > 0 else "<missing>")
            locator = self._find_textarea(page, "post")
            if locator is None:
                raise RuntimeError("Post field not found")
            self.typer.type_text(locator, text)
            self._submit_form(page, field_selector="textarea[name='post'], #post, textarea#post, form textarea")
            success = wait_for_signal(page, expected_url=["/", "/home"], expected_text="Your post is now live!", timeout=15000)
            self.record(mission, "success" if success else "failed")
        except Exception as exc:
            print(f"{mission} failed: {exc}")
            self.record(mission, "failed")
        finally:
            flush_tracking(page)

    def _task_explore_scroll(self, page):
        mission = "explore_scroll"
        try:
            page.goto(f"{BASE_URL}/explore")
            human_scroll(page, self.profile, distance=4000)
            self.record(mission, "success")
        except Exception as exc:
            print(f"{mission} failed: {exc}")
            self.record(mission, "failed")
        finally:
            flush_tracking(page)

    def _task_search(self, page):
        mission = "search"
        try:
            page.goto(f"{BASE_URL}/explore")
            search_input = page.locator("form[action*='/search'] input[name='q'], input[name='q']").first
            search_input.wait_for(state="visible", timeout=5000)
            search_input.fill("charweb")
            page.keyboard.press("Enter")
            success = wait_for_signal(page, expected_url=["/search", "/explore"], expected_text="Search Results", timeout=10000)
            self.record(mission, "success" if success else "failed")
        except Exception as exc:
            print(f"{mission} failed: {exc}")
            self.record(mission, "failed")
        finally:
            flush_tracking(page)

    def _task_edit_profile(self, page):
        mission = "edit_profile"
        try:
            page.goto(f"{BASE_URL}/edit_profile")
            page.locator("input[name='username'], #username").first.fill(self.username)
            self._find_textarea(page, "about_me").fill("This is an AI-generated profile update used for behavioral testing.")
            self._submit_form(page, field_selector="textarea[name='about_me'], #about_me, textarea#about_me, form textarea")
            success = wait_for_signal(page, expected_text="Your changes have been saved.", timeout=15000)
            self.record(mission, "success" if success else "failed")
        except Exception as exc:
            print(f"{mission} failed: {exc}")
            self.record(mission, "failed")
        finally:
            flush_tracking(page)

    def _task_terms(self, page):
        mission = "terms"
        try:
            page.goto(f"{BASE_URL}/login")
            # The link opens in a new tab (target="_blank"), so the terms
            # page never loads in `page` itself -- capture the popup instead.
            with page.context.expect_page() as popup_info:
                page.get_by_text("Terms of Service").click()
            terms_page = popup_info.value
            terms_page.wait_for_load_state()
            success = _path_matches(terms_page.url, "/terms")
            safe_sleep(3)
            terms_page.close()
            self.record(mission, "success" if success else "failed")
        except Exception as exc:
            print(f"{mission} failed: {exc}")
            self.record(mission, "failed")
        finally:
            flush_tracking(page)

    def _task_timed_account(self, page):
        mission = "timed_account"
        try:
            # /register redirects already-authenticated users straight to /home,
            # so the earlier login in this same session must be cleared first.
            page.goto(f"{BASE_URL}/logout")
            start = time.time()
            username = generate_username(self.profile.level, self.trial, "_m8")
            page.goto(f"{BASE_URL}/register")
            page.locator("#username").fill(username)
            page.locator("#email").fill(generate_email(username))
            page.locator("#password").fill(PASSWORD)
            page.locator("#password2").fill(PASSWORD)
            page.locator("#accept_terms").check()
            self._submit_form(page, field_selector="#username")
            wait_for_signal(page, expected_url="/login", timeout=10000)
            page.goto(f"{BASE_URL}/login")
            page.locator("#username").fill(username)
            page.locator("#password").fill(PASSWORD)
            self._submit_form(page, field_selector="#username")
            wait_for_signal(page, expected_url=["/", "/home"], timeout=10000)
            page.goto(f"{BASE_URL}/home")
            locator = self._find_textarea(page, "post")
            self.typer.type_text(locator, "This is a five sentence intro written quickly to test the timed registration and login flow. It should be natural and readable. The goal is to observe timing behavior across the site. This action is part of the required timed mission.")
            self._submit_form(page, field_selector="textarea[name='post'], #post, textarea#post, form textarea")
            elapsed = time.time() - start
            success = wait_for_signal(page, expected_url=["/", "/home"], expected_text="Your post is now live!", timeout=15000) and elapsed < 60
            self.record(mission, "success" if success else "failed")
        except Exception as exc:
            print(f"{mission} failed: {exc}")
            self.record(mission, "failed")
        finally:
            flush_tracking(page)

    def _task_delayed_post(self, page):
        mission = "delayed_post"
        try:
            page.goto(f"{BASE_URL}/home")
            locator = self._find_textarea(page, "post")
            self.typer.type_text(locator, "I am starting this draft and will pause for a while before finishing it. ")
            safe_sleep(30)
            locator.click()
            locator.press("End")
            self.typer.type_text(locator, "The remainder of the post is added after a long pause so the timing pattern is visible.")
            self._submit_form(page, field_selector="textarea[name='post'], #post, textarea#post, form textarea")
            success = wait_for_signal(page, expected_url=["/", "/home"], expected_text="Your post is now live!", timeout=15000)
            self.record(mission, "success" if success else "failed")
        except Exception as exc:
            print(f"{mission} failed: {exc}")
            self.record(mission, "failed")
        finally:
            flush_tracking(page)

    def _task_logout_forgot_password_login_and_post(self, page):
        mission = "logout_forgot_login_post"
        try:
            page.goto(f"{BASE_URL}/logout")
            page.goto(f"{BASE_URL}/login")
            page.get_by_text("Forgot Your Password?").click()
            self._find_input(page, "email").fill(generate_email(self.username))
            self._submit_form(page, field_selector="input[name='email']")
            success = wait_for_signal(page, expected_url="/login", expected_text="Check your email for the instructions to reset your password", timeout=15000)
            if not success:
                raise RuntimeError("Forgot-password signal not seen")
            page.goto(f"{BASE_URL}/login")
            page.locator("input[name='username'], #username").first.fill(self.username)
            page.locator("input[name='password'], #password").first.fill(PASSWORD)
            self._submit_form(page, field_selector="input[name='username'], #username")
            wait_for_signal(page, expected_url=["/", "/home"], timeout=10000)
            self._task_compose_post(page, "This is the follow-up post that confirms the account can login again after a password reset request flow.")
            self.record(mission, "success")
        except Exception as exc:
            print(f"{mission} failed: {exc}")
            self.record(mission, "failed")
        finally:
            flush_tracking(page)

    def _task_daily_signin(self, page):
        mission = "daily_signin"
        try:
            page.goto(f"{BASE_URL}/daily")
            button = page.locator("button:has-text('Sign In')").first
            if button.count() > 0:
                button.click()
                wait_for_signal(page, expected_url="/daily", timeout=10000)
            self.record(mission, "success")
        except Exception as exc:
            print(f"{mission} failed: {exc}")
            self.record(mission, "failed")
        finally:
            flush_tracking(page)

    def _task_daily_character(self, page):
        mission = "daily_create_character"
        try:
            page.goto(f"{BASE_URL}/daily")
            self._find_input(page, "name").fill(f"{self.username}_hero")
            page.locator("button:has-text('Create Character')").first.click()
            wait_for_signal(page, expected_url="/daily", timeout=10000)
            self.record(mission, "success")
        except Exception as exc:
            print(f"{mission} failed: {exc}")
            self.record(mission, "failed")
        finally:
            flush_tracking(page)

    def _task_daily_dungeon(self, page):
        mission = "daily_dungeon"
        try:
            page.goto(f"{BASE_URL}/daily")
            for _ in range(4):
                for button_text in ["⚔️ Explore (5 tokens)", "💤 Rest", "⬇️ Descend", "⬆️ Ascend"]:
                    button = page.get_by_role("button", name=button_text)
                    if button.count() > 0:
                        button.first.click()
                        safe_sleep(1)
                        break
                if page.get_by_role("button", name="⚔️ Fight").count() > 0:
                    fight = page.get_by_role("button", name="⚔️ Fight")
                    if fight.count() > 0:
                        fight.first.click()
                        safe_sleep(1)
                        break
            self.record(mission, "success")
        except Exception as exc:
            print(f"{mission} failed: {exc}")
            self.record(mission, "failed")
        finally:
            flush_tracking(page)

    def _task_daily_shop(self, page):
        mission = "daily_shop"
        try:
            page.goto(f"{BASE_URL}/daily/shop")
            buy_button = page.locator("button:has-text('Buy')").first
            if buy_button.count() > 0:
                buy_button.click()
                wait_for_signal(page, expected_url="/daily", timeout=10000)
            self.record(mission, "success")
        except Exception as exc:
            print(f"{mission} failed: {exc}")
            self.record(mission, "failed")
        finally:
            flush_tracking(page)

    def _task_daily_equip(self, page):
        mission = "daily_equip"
        try:
            page.goto(f"{BASE_URL}/daily")
            equip_form = page.locator("form[action*='/daily/equip/']")
            if equip_form.count() > 0:
                equip_form.first.locator("button[type=submit], button").first.click()
                wait_for_signal(page, expected_url="/daily", timeout=10000)
            self.record(mission, "success")
        except Exception as exc:
            print(f"{mission} failed: {exc}")
            self.record(mission, "failed")
        finally:
            flush_tracking(page)

    def _task_chat(self, page):
        mission = "chat"
        try:
            page.goto(f"{BASE_URL}/chat/")
            users = page.locator(".user-item")
            if users.count() > 0:
                users.first.click()
                message_box = page.locator("#message-input")
                message_box.fill("Hello from the AI behavioral run.")
                page.locator("#send-btn").click()
            self.record(mission, "success")
        except Exception as exc:
            print(f"{mission} failed: {exc}")
            self.record(mission, "failed")
        finally:
            flush_tracking(page)

    def _task_ranking(self, page):
        mission = "ranking"
        try:
            page.goto(f"{BASE_URL}/ranking")
            page.mouse.wheel(0, 1200)
            self.record(mission, "success")
        except Exception as exc:
            print(f"{mission} failed: {exc}")
            self.record(mission, "failed")
        finally:
            flush_tracking(page)

    def _task_allocate_points(self, page):
        mission = "allocate_points"
        try:
            page.goto(f"{BASE_URL}/daily")
            allocate_form = page.locator("form[action='/daily/allocate']")
            if allocate_form.count() > 0:
                allocate_form.first.locator("button[type=submit], button").first.click()
                wait_for_signal(page, expected_url="/daily", timeout=10000)
            self.record(mission, "success")
        except Exception as exc:
            print(f"{mission} failed: {exc}")
            self.record(mission, "failed")
        finally:
            flush_tracking(page)


manifest = Manifest(MANIFEST_PATH)


def main():
    for level in LEVELS.values():
        for trial in range(1, TRIALS_PER_LEVEL + 1):
            runner = CharwebRunner(level, trial, manifest)
            runner.run()
            safe_sleep(15)


if __name__ == "__main__":
    main()