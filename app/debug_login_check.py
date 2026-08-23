from pathlib import Path
from playwright.sync_api import sync_playwright
from copilat import CharwebRunner, LEVELS, Manifest, PASSWORD

runner = CharwebRunner(LEVELS[1], 1, Manifest(Path('manifest.csv')))
runner.username = 'debug_login_check'

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto('https://charweb.net/register')
    page.locator('#username').fill(runner.username)
    page.locator('#email').fill(f'{runner.username}@example.com')
    page.locator('#password').fill(PASSWORD)
    page.locator('#password2').fill(PASSWORD)
    page.locator('#accept_terms').check()
    runner._submit_form(page, field_selector='#username')

    page.goto('https://charweb.net/login')
    page.locator('input[name="username"], #username').first.fill(runner.username)
    page.locator('input[name="password"], #password').first.fill(PASSWORD)
    runner._submit_form(page, field_selector="input[name='username'], #username")

    print('Current URL after login:', page.url)
    print('Page title:', page.title())
    print('Cookies after login:', page.context.cookies())

    page.goto('https://charweb.net/home')
    print('compose_post URL:', page.url)
    print('compose_post title:', page.title())
    print('#post count:', page.locator('#post').count())
    if page.locator('#post').count() > 0:
        print('#post outerHTML:', page.locator('#post').evaluate('el => el.outerHTML'))
    else:
        print('#post outerHTML: <missing>')

    browser.close()
