"""Attribution labels for a data-collection run.

Each harness sends these as HTTP headers on its Playwright context/page, so
they ride along on the /api/track XHR and the server records them on the
session's UserSession row (see routes.track). This is the ground truth the
AI-only study groups on (leave-one-out CV by harness / by model, variance
decomposition), and the Phase-2 adversarial condition.

Set the env vars before launching a harness, e.g.:

    CHARWEB_HARNESS=llm_driven CHARWEB_MODEL=gemini-1.5-pro \
    CHARWEB_INSTRUCTION=free_explore CHARWEB_RUN_ID=run_2026_08_23_a \
    python app/gemini.py --url https://charweb.net

Anything left unset is simply omitted; adversarial_condition then falls back
to its 'clean' default on the server.
"""
import os

_ENV_TO_HEADER = {
    "CHARWEB_RUN_ID": "X-Run-Id",
    "CHARWEB_HARNESS": "X-Harness",
    "CHARWEB_MODEL": "X-Model",
    "CHARWEB_INSTRUCTION": "X-Instruction",
    "CHARWEB_ADV_CONDITION": "X-Adv-Condition",
    "CHARWEB_MIMICRY_TARGET": "X-Mimicry-Target",
}


def build_run_headers():
    """Return X-* label headers for whichever env vars are set. Empty dict if
    none — set_extra_http_headers accepts that as a no-op."""
    return {header: os.environ[env]
            for env, header in _ENV_TO_HEADER.items()
            if os.environ.get(env)}


# Cookie fallback for the same labels. HTTP headers set on a Playwright context
# ride the top-level navigation and interval fetches, but are NOT attached to
# navigator.sendBeacon and are unreliable on the page-teardown keepalive flush.
# Cookies, by contrast, are sent by the browser on EVERY request automatically
# (navigation, fetch, keepalive, beacon), so a fast-navigating harness whose
# events all flush on page-hide is still labelled. The server reads the header
# first and falls back to the cookie (see routes.track).
_ENV_TO_COOKIE = {
    "CHARWEB_RUN_ID": "cw_run_id",
    "CHARWEB_HARNESS": "cw_harness",
    "CHARWEB_MODEL": "cw_model",
    "CHARWEB_INSTRUCTION": "cw_instruction",
    "CHARWEB_ADV_CONDITION": "cw_adv_condition",
    "CHARWEB_MIMICRY_TARGET": "cw_mimicry_target",
}


def build_run_cookies(url):
    """Return a list of Playwright cookie dicts for whichever label env vars are
    set, scoped to `url`'s origin. Pass to context.add_cookies(). Empty list if
    none set."""
    return [{"name": cookie, "value": os.environ[env], "url": url}
            for env, cookie in _ENV_TO_COOKIE.items()
            if os.environ.get(env)]
