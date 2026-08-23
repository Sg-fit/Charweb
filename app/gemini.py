import argparse
import asyncio
import random
from playwright.async_api import async_playwright

try:
    from run_labels import build_run_headers
except ImportError:
    from app.run_labels import build_run_headers


async def human_delay(min_sec: float = 0.8, max_sec: float = 2.5):
    """Simulates realistic human pause delays between interactions."""
    await asyncio.sleep(random.uniform(min_sec, max_sec))


async def natural_scroll(page, steps: int = 3):
    """Simulates mixed scrolling down the page with variable distances."""
    for _ in range(steps):
        scroll_y = random.randint(250, 600)
        await page.mouse.wheel(0, scroll_y)
        await human_delay(0.7, 1.8)


async def main():
    parser = argparse.ArgumentParser(
        description="Behavioral data collection agent for charweb.net"
    )
    parser.add_argument(
        "--username",
        type=str,
        required=True,
        help="Username parameter for session registration/login",
    )
    parser.add_argument(
        "--url",
        type=str,
        default="https://charweb.net",
        help="Target website URL",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        default=False,
        help="Run browser headlessly (default: visible mode)",
    )
    args = parser.parse_args()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=args.headless)
        context = await browser.new_context()
        await context.set_extra_http_headers(build_run_headers())
        page = await context.new_page()

        base_url = args.url.rstrip('/')
        register_url = f"{base_url}/register"
        login_url = f"{base_url}/login"

        print(f"Navigating to {register_url} as user '{args.username}'...")
        await page.goto(register_url)
        await human_delay(1.5, 3.0)

        # ------------------------------------------------------------------
        # Task 1: Signup / Login Flow
        # ------------------------------------------------------------------
        print("Task 1: Filling Signup/Login Form...")
        
        username_field = page.locator("input[name='username']")
        if await username_field.count() > 0:
            await username_field.type(args.username, delay=random.randint(80, 180))
            await human_delay()

        email_field = page.locator("input[name='email']")
        if await email_field.count() > 0:
            await email_field.type(f"{args.username}@example.com", delay=random.randint(70, 150))
            await human_delay()

        pwd_field = page.locator("input[name='password']")
        if await pwd_field.count() > 0:
            await pwd_field.type("SecurePass123!", delay=random.randint(90, 160))
            await human_delay()

        pwd2_field = page.locator("input[name='password2']")
        if await pwd2_field.count() > 0:
            await pwd2_field.type("SecurePass123!", delay=random.randint(90, 160))
            await human_delay()

        remember_cb = page.locator("input[name='remember_me']")
        if await remember_cb.count() > 0:
            await remember_cb.check()
            await human_delay(0.5, 1.2)

        terms_cb = page.locator("input[name='accept_terms']")
        if await terms_cb.count() > 0:
            await terms_cb.check()
            await human_delay(0.5, 1.2)

        submit_btn = page.locator("input[name='submit']")
        if await submit_btn.count() > 0:
            await submit_btn.first.click()
            await human_delay(2.0, 4.0)

        # Registration submitted -> now actually log in with the same credentials.
        print("Task 1b: Logging in...")
        await page.goto(login_url)
        await human_delay(1.0, 2.0)

        login_username_field = page.locator("input[name='username']")
        if await login_username_field.count() > 0:
            await login_username_field.type(args.username, delay=random.randint(80, 180))
            await human_delay()

        login_pwd_field = page.locator("input[name='password']")
        if await login_pwd_field.count() > 0:
            await login_pwd_field.type("SecurePass123!", delay=random.randint(90, 160))
            await human_delay()

        login_submit_btn = page.locator("input[name='submit']")
        if await login_submit_btn.count() > 0:
            await login_submit_btn.first.click()
            await human_delay(2.0, 4.0)

        # ------------------------------------------------------------------
        # Task 2: Search
        # ------------------------------------------------------------------
        print("Task 2: Performing Search Query...")
        search_input = page.locator("input[name='q']")
        if await search_input.count() > 0:
            await search_input.type("behavioral data collection", delay=random.randint(80, 160))
            await human_delay()
            await search_input.press("Enter")
            await human_delay(2.0, 3.5)
            await natural_scroll(page, steps=2)

        # ------------------------------------------------------------------
        # Task 3: Feed Browsing
        # ------------------------------------------------------------------
        print("Task 3: Browsing Feed Items...")
        await natural_scroll(page, steps=3)

        # Target interactive elements in the feed area
        feed_elements = page.locator(
            "div.post a, td a, small a, div.post img, td img, div a, li a"
        )
        feed_count = await feed_elements.count()
        if feed_count > 0:
            # Pick a random item to click
            chosen_idx = random.randint(0, min(feed_count - 1, 4))
            target_el = feed_elements.nth(chosen_idx)
            try:
                await target_el.scroll_into_view_if_needed()
                await human_delay(0.8, 1.5)
                await target_el.click(timeout=3000)
                await human_delay(2.0, 4.0)
            except Exception:
                pass

        # ------------------------------------------------------------------
        # Task 4: Profile Edit
        # ------------------------------------------------------------------
        print("Task 4: Editing Profile...")
        about_me = page.locator("textarea[name='about_me'], input[name='about_me']")
        if await about_me.count() > 0:
            await about_me.fill("")  # Clear existing content
            await human_delay(0.5, 1.0)
            bio_text = f"Behavioral research test session for user profile {args.username}."
            await about_me.type(bio_text, delay=random.randint(50, 120))
            await human_delay()

            save_btn = page.locator("button:has-text('Save'), input[value='Save'], input[type='submit']")
            if await save_btn.count() > 0:
                await save_btn.first.click()
                await human_delay(2.0, 3.5)

        # ------------------------------------------------------------------
        # Task 5: Timed Task Interaction
        # ------------------------------------------------------------------
        print("Task 5: Interacting with Timer Widgets...")
        start_timer_btn = page.locator("#start-timer, .start-timer, button:has-text('Start Timer')")
        if await start_timer_btn.count() > 0:
            await start_timer_btn.first.click()
            print("Timer started, observing display widget...")
            # Simulate spending time in a timed session/mission
            await human_delay(4.0, 8.0)

        # Finish session with natural idle pause
        print("Session finishing...")
        await human_delay(1.5, 3.0)
        await browser.close()
        print("Execution complete.")


if __name__ == "__main__":
    asyncio.run(main())