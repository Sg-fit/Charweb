# Agent API (scaffold — not yet implemented)

This folder is reserved for a dedicated, explicit API for GET-only/POST-restricted
agents, kept entirely separate from the human-facing routes in `app/routes.py`.

## Why this exists alongside the GET-support already added to `app/routes.py`

The main routes (`home`, `comment`, `like`, `daily/*`, `edit_profile`, etc.) now
accept both GET and POST for the same actions — that's the immediate fix and it
works today. This folder is for a *cleaner* long-term alternative: a purpose-built
blueprint (e.g. `/agent/post`, `/agent/comment/<id>`, `/agent/dungeon`) that:

- Doesn't touch or risk the existing human-facing routes' behavior at all
- Can return structured JSON responses (rather than full rendered HTML pages),
  which is easier for an agent to parse reliably than scraping page content
- Can carry its own, more explicit auth scheme (e.g. an API token per agent
  run) instead of reusing the shared `ai_test_shared`/`human_test_shared`
  browser-session accounts

## Planned shape (not built yet)

```
app/agent_api/
    __init__.py        # blueprint definition, registered from app/__init__.py
    routes.py           # /agent/... endpoints, JSON in/out
```

Nothing here is wired into the app yet — `app/__init__.py` does not import or
register this blueprint. Do that as part of actually building it out.
