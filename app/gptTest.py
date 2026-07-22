#!/usr/bin/env python3

import argparse
import random
import time

from playwright.sync_api import sync_playwright, TimeoutError

BASE_URL = "https://charweb.net"


# ==========================================================
# HUMAN BEHAVIOR HELPERS
# ==========================================================

PASSWORD = "Charweb123!"


def human_delay(a=0.3, b=1.5):
    time.sleep(random.uniform(a, b))


def reading_pause():
    time.sleep(random.uniform(2, 6))


def idle_pause():
    time.sleep(random.uniform(5, 12))


def long_reading_pause():
    time.sleep(random.uniform(8, 15))


def random_viewport():
    return {
        "width": random.choice([1280, 1366, 1440, 1536]),
        "height": random.choice([720, 768, 900, 960]),
    }


# ==========================================================
# MOUSE MOVEMENT
# ==========================================================

def human_move_mouse(page, locator):
    """
    Move mouse naturally to the center of an element.
    """
    try:
        box = locator.bounding_box()

        if not box:
            return False

        x = box["x"] + box["width"] / 2
        y = box["y"] + box["height"] / 2

        page.mouse.move(
            x,
            y,
            steps=random.randint(15, 35)
        )

        human_delay(0.1, 0.4)
        return True

    except Exception:
        return False


def human_click(page, locator):
    """
    Mouse movement + click.
    """
    try:
        locator.scroll_into_view_if_needed()
        human_delay()

        human_move_mouse(page, locator)

        locator.click()

        human_delay()

        return True

    except Exception:
        return False


# ==========================================================
# TYPING
# ==========================================================

def human_type(locator, text):

    locator.click()

    for ch in text:

        locator.press_sequentially(ch)

        time.sleep(
            random.uniform(0.03, 0.15)
        )

        if random.random() < 0.05:
            time.sleep(
                random.uniform(0.25, 0.8)
            )


# ==========================================================
# SCROLLING
# ==========================================================

def human_scroll(page, n=None):

    if n is None:
        n = random.randint(3, 7)

    for _ in range(n):

        amount = random.randint(250, 850)

        page.mouse.wheel(0, amount)

        time.sleep(
            random.uniform(1.0, 3.5)
        )

        if random.random() < 0.20:
            page.mouse.wheel(
                0,
                -random.randint(100, 350)
            )

            human_delay()


# ==========================================================
# GENERIC HELPERS
# ==========================================================

def fill_if_exists(page, selector, value):

    try:
        locator = page.locator(selector).first

        locator.wait_for(timeout=3000)

        human_type(locator, value)

        return True

    except Exception:
        return False


def click_if_exists(page, selector):

    try:
        locator = page.locator(selector).first

        locator.wait_for(timeout=2000)

        return human_click(page, locator)

    except Exception:
        return False


# ==========================================================
# SIGNUP
# ==========================================================

def signup(page, username):

    print("\nRegistering account...")

    page.goto(f"{BASE_URL}/register")

    reading_pause()

    email = f"{username}@example.com"

    fill_if_exists(
        page,
        '[name="username"]',
        username
    )

    fill_if_exists(
        page,
        '[name="email"]',
        email
    )

    fill_if_exists(
        page,
        '[name="password"]',
        PASSWORD
    )

    fill_if_exists(
        page,
        '[name="password2"]',
        PASSWORD
    )

    click_if_exists(
        page,
        '[name="remember_me"]'
    )

    click_if_exists(
        page,
        '[name="accept_terms"]'
    )

    if not click_if_exists(
        page,
        'input[name="submit"]'
    ):
        click_if_exists(
            page,
            '[type="submit"]'
        )

    reading_pause()

    return PASSWORD


# ==========================================================
# LOGIN
# ==========================================================

def login(page, username, password):

    print("Logging in...")

    page.goto(f"{BASE_URL}/login")

    reading_pause()

    fill_if_exists(
        page,
        '[name="username"]',
        username
    )

    fill_if_exists(
        page,
        '[name="password"]',
        password
    )

    if not click_if_exists(
        page,
        'input[name="submit"]'
    ):
        click_if_exists(
            page,
            '[type="submit"]'
        )

    reading_pause()
    




# ==========================================================
# SEARCH
# ==========================================================

SEARCH_TERMS = [

    "robotics",
    "AI",
    "gaming",
    "FRC",
    "technology",
    "community",
    "software",
]


def perform_search(page):

    print("Searching...")

    page.goto(f"{BASE_URL}/home")

    reading_pause()

    term = random.choice(
        SEARCH_TERMS
    )

    if fill_if_exists(
            page,
            '[name="q"]',
            term):

        click_if_exists(
            page,
            '[type="submit"]'
        )

        reading_pause()

        human_scroll(page)


# ==========================================================
# FEED BROWSING
# ==========================================================

def browse_feed(page):

    print("Browsing feed...")

    page.goto(f"{BASE_URL}/")

    reading_pause()

    human_scroll(page)

    selectors = [
        "a",
        "img",
        ".post",
        "article",
        "td"
    ]

    number_of_clicks = random.randint(
        2,
        4
    )

    for _ in range(number_of_clicks):

        selector = random.choice(
            selectors
        )

        try:

            items = page.locator(selector)

            count = items.count()

            if count == 0:
                continue

            index = random.randint(
                0,
                min(count - 1, 5)
            )

            item = items.nth(index)

            if human_click(
                    page,
                    item):

                reading_pause()

                if random.random() < 0.7:
                    human_scroll(page)

                try:
                    page.go_back()
                except Exception:
                    pass

                reading_pause()

        except Exception:
            continue


# ==========================================================
# PROFILE
# ==========================================================

ABOUT_TEXT = [

    "I enjoy AI research.",
    "Interested in robotics and technology.",
    "Exploring Charweb for the first time.",
    "Learning more about online communities.",
]


def edit_profile(page):

    print("Editing profile...")

    page.goto(
        f"{BASE_URL}/edit_profile"
    )

    reading_pause()

    fill_if_exists(
        page,
        '[name="about_me"]',
        random.choice(
            ABOUT_TEXT
        )
    )

    click_if_exists(
        page,
        'input[name="submit"]'
    )

    reading_pause()


# ==========================================================
# DAILY HUB
# ==========================================================

def daily_sign_in(page):

    print("Visiting Daily Hub...")

    page.goto(
        f"{BASE_URL}/daily"
    )

    reading_pause()

    human_scroll(page)

    possible_buttons = [

        'button',
        '[type="submit"]',
        'input[type="submit"]'
    ]

    for selector in possible_buttons:

        click_if_exists(
            page,
            selector
        )

        human_delay()

    long_reading_pause()


# ==========================================================
# DUNGEON
# ==========================================================

def dungeon_interaction(page):

    print("Exploring dungeon...")

    page.goto(
        f"{BASE_URL}/daily"
    )

    reading_pause()

    human_scroll(page)

    buttons = page.locator("button")

    try:

        count = buttons.count()

        if count:

            for _ in range(
                    random.randint(1, 3)):

                idx = random.randint(
                    0,
                    count - 1
                )

                human_click(
                    page,
                    buttons.nth(idx)
                )

                reading_pause()

    except Exception:
        pass


# ==========================================================
# SHOP
# ==========================================================

def browse_shop(page):

    print("Browsing shop...")

    page.goto(
        f"{BASE_URL}/daily/shop"
    )

    reading_pause()

    human_scroll(page)

    try:

        buttons = page.locator(
            "button"
        )

        count = buttons.count()

        if count:

            if random.random() < 0.5:

                idx = random.randint(
                    0,
                    count - 1
                )

                human_click(
                    page,
                    buttons.nth(idx)
                )

                reading_pause()

    except Exception:
        pass


# ==========================================================
# RANKING
# ==========================================================

def browse_ranking(page):

    print("Browsing rankings...")

    page.goto(
        f"{BASE_URL}/ranking"
    )

    reading_pause()

    human_scroll(page)

    long_reading_pause()


# ==========================================================
# TEAM PAGES
# ==========================================================

TEAM_PAGES = [

    "/team",
    "/team/software",
    "/team/electrical",
    "/team/mechanical",
    "/team/outreach",
]


def browse_team_pages(page):

    random.shuffle(
        TEAM_PAGES
    )

    count = random.randint(
        2,
        4
    )

    for route in TEAM_PAGES[:count]:

        page.goto(
            BASE_URL + route
        )

        reading_pause()

        human_scroll(page)

        if random.random() < 0.3:
            idle_pause()


# ==========================================================
# RANDOM EXPLORATION
# ==========================================================

EXTRA_PAGES = [

    "/home",
    "/explore",
    "/daily",
    "/daily/shop",
    "/ranking",
    "/team"

]


def random_explore(page):

    random.shuffle(
        EXTRA_PAGES
    )

    for route in EXTRA_PAGES[:3]:

        page.goto(
            BASE_URL + route
        )

        reading_pause()

        human_scroll(page)


# ==========================================================
# SESSION
# ==========================================================

def run_session(page, username):

    password = signup(
        page,
        username
    )

    login(
        page,
        username,
        password
    )

    perform_search(page)

    browse_feed(page)

    edit_profile(page)

    daily_sign_in(page)

    dungeon_interaction(page)

    browse_shop(page)

    browse_ranking(page)

    browse_team_pages(page)

    random_explore(page)

    idle_pause()


# ==========================================================
# CLI
# ==========================================================

def parse_args():

    parser = argparse.ArgumentParser()

    parser.add_argument(

        "--username",
        required=True,
        help="Username to register."

    )

    parser.add_argument(

        "--headless",
        action="store_true",
        help="Run headless."

    )

    return parser.parse_args()


# ==========================================================
# MAIN
# ==========================================================

def main():

    args = parse_args()

    with sync_playwright() as p:

        browser = p.chromium.launch(

            headless=args.headless,

            slow_mo=random.randint(
                40,
                120
            )
        )

        context = browser.new_context(

            viewport=random_viewport()

        )

        page = context.new_page()

        try:

            run_session(
                page,
                args.username
            )

        except TimeoutError:

            print(
                "Timeout occurred."
            )

        except Exception as e:

            print(
                f"Unexpected error: {e}"
            )

        finally:

            context.close()

            browser.close()


if __name__ == "__main__":
    main()