from playwright.sync_api import sync_playwright
import random, string, sys

base = 'https://charweb.net'
username = 'debug_' + ''.join(random.choice(string.ascii_lowercase) for _ in range(6))
email = f'{username}@example.com'
password = 'TestPassword123!'

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto(base + '/register', wait_until='networkidle')
    print('register page loaded', page.url)
    page.locator('#username').fill(username)
    page.locator('#email').fill(email)
    page.locator('#password').fill(password)
    page.locator('#password2').fill(password)
    page.locator('#accept_terms').check()
    page.locator("input[type='submit']").click()
    page.wait_for_load_state('networkidle')
    print('after register url=', page.url)
    print(page.text_content('body')[:4000])
    page.goto(base + '/login', wait_until='networkidle')
    print('login page loaded', page.url)
    page.locator('input[name="username"]').fill(username)
    page.locator('input[name="password"]').fill(password)
    page.locator('input[type="submit"]').click()
    page.wait_for_load_state('networkidle')
    print('after login url=', page.url)
    print(page.text_content('body')[:6000])
    browser.close()
