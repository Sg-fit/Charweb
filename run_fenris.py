#!/usr/bin/env python3
"""Batch Fenris collection driver for Charweb (the harness-axis cell).

Fenris's browsing runs in its backend brain service (uvicorn backend.main:app),
which drives an LLM that decides to use the web_browser addon. This script is
just an HTTP client to that backend's /chat endpoint -- no voice/HUD needed.
Each session uses a fresh actor_name (so Fenris's per-actor cookie store is
empty -> a new Charweb account/session each time) and actor_role="user" (the
web_browser addon refuses guests).

The session LABELS (harness=fenris, model=..., instruction, run_id) are read
from CHARWEB_* env vars *inside the backend process* (that's where the patched
web_browser._new_context runs), so set them BEFORE launching uvicorn:

  # window 1 -- the Fenris backend, configured for a free Groq brain + labels
  cd <your Fenris folder>
  $env:FENRIS_BRAIN_PROVIDER="local"
  $env:FENRIS_LOCAL_BASE_URL="https://api.groq.com/openai/v1"
  $env:FENRIS_LOCAL_MODEL="openai/gpt-oss-120b"
  $env:FENRIS_LOCAL_API_KEY="gsk_your_key"
  $env:CHARWEB_HARNESS="fenris"
  $env:CHARWEB_MODEL="openai/gpt-oss-120b"
  $env:CHARWEB_INSTRUCTION="free_explore"
  $env:CHARWEB_RUN_ID="m2_fenris"
  python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000

  # window 2 -- this driver (talks to the backend above)
  cd C:\\Users\\charl\\ProjectStart
  python run_fenris.py --per-cell 40 --instruction free_explore

To collect the 'checklist' condition too, restart the backend with
CHARWEB_INSTRUCTION=checklist and run this with --instruction checklist.

Requires: pip install requests
"""
import argparse
import json
import os
import time
import uuid

import requests

# Instruction conditions come from instructions.py (repo root) -- same text the
# llm_agent harness uses, so "checklist" means one thing across the study.
# for_fenris() adds the act-without-asking preface and the register-first step
# that this harness needs (llm_agent registers on its own).
from instructions import CONDITIONS, for_fenris

TASKS = {name: for_fenris(name) for name in CONDITIONS}


def stream_chat(backend, messages, actor_name, session_id):
    """POST one turn to /chat, consume the streamed events, return
    (terminal_text, ended_on_question)."""
    final_text, is_question = "", False
    with requests.post(
        f"{backend}/chat",
        json={"messages": messages, "actor_name": actor_name,
              "actor_role": "user", "session_id": session_id},
        timeout=(10, 900), stream=True) as r:
        r.raise_for_status()
        for line in r.iter_lines(decode_unicode=True):
            if not line:
                continue
            try:
                ev = json.loads(line)
            except (ValueError, TypeError):
                continue
            etype = ev.get("type")
            if etype in {"question", "result"}:
                final_text = ev.get("text", "")
                is_question = etype == "question"
            elif etype:  # progress narration
                snippet = (ev.get("text") or "").strip()
                if snippet:
                    print(f"      · {snippet[:80]}")
    return final_text, is_question


def run_one(backend, task, actor_name):
    """One full Fenris session; auto-confirms if the mission pauses to ask."""
    session_id = str(uuid.uuid4())
    messages = [{"role": "user", "content": task}]
    for turn in range(4):
        text, is_question = stream_chat(backend, messages, actor_name, session_id)
        print(f"      => {text[:120]}")
        if not is_question:
            return
        # Mission paused to ask (e.g. confirm an action) -> approve and continue.
        messages.append({"role": "assistant", "content": text})
        messages.append({"role": "user", "content": "Yes, go ahead."})


def main():
    ap = argparse.ArgumentParser(description="Batch Fenris collection driver")
    ap.add_argument("--per-cell", type=int, default=40)
    ap.add_argument("--instruction", choices=list(TASKS), default="free_explore",
                    help="task text (match the backend's CHARWEB_INSTRUCTION)")
    ap.add_argument("--backend", default="http://127.0.0.1:8000")
    ap.add_argument("--sleep", type=float, default=8.0)
    ap.add_argument("--run-id", default=None,
                    help="informational only; the real label comes from the "
                         "backend's CHARWEB_RUN_ID")
    args = ap.parse_args()

    # Health check so we fail fast with a clear message if the backend is down.
    try:
        h = requests.get(f"{args.backend}/health", timeout=5).json()
        print(f"Backend OK (provider={h.get('provider')}). "
              f"Make sure it was launched with CHARWEB_HARNESS=fenris and your "
              f"CHARWEB_INSTRUCTION={args.instruction}.")
    except Exception as e:
        raise SystemExit(
            f"Cannot reach Fenris backend at {args.backend} ({e}). Start it first:\n"
            f"  python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000")

    task = TASKS[args.instruction]
    for i in range(args.per_cell):
        actor = f"fenris-{args.instruction}-{i:03d}-{uuid.uuid4().hex[:4]}"
        print(f"[{i + 1}/{args.per_cell}] {actor}", flush=True)
        try:
            run_one(args.backend, task, actor)
        except requests.RequestException as e:
            print(f"      (request error) {e}")
        time.sleep(args.sleep)

    print("\nDone. Verify on the server that fenris/<model> sessions appear.")


if __name__ == "__main__":
    main()
