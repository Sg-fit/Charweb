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
