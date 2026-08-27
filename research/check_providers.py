#!/usr/bin/env python3
"""Pre-flight: which (provider, model) pairs can actually drive the agent?

An HTTP 200 is not enough. A model can be reachable and still be useless to
this study in three ways that only show up mid-batch, after it has already
written label-only sessions into the database:

  1. retired/withdrawn  -> 404 / 410 on the first call
  2. wrong output shape -> replies in prose, or fenced markdown, so the action
                           parser gets nothing and the session does nothing
  3. provider defect    -> e.g. Groq returns 400 tool_use_failed for some
                           models even with no tools in the request

So this issues a REAL request in llm_agent's exact shape -- same system prompt
(instruction + ACTION_SPEC), same element listing as the user message,
temperature=0.7, max_tokens=300 -- and checks that the reply parses into a
valid action object. Repeated N times, because a model that gets it right once
in three is a slow poisoner of the dataset, not a usable arm.

    export GEMINI_KEY=... GROQ_KEY=... NVIDIA_KEY=... CEREBRAS_KEY=...
    ./venv/bin/python research/check_providers.py                 # sweep all
    ./venv/bin/python research/check_providers.py --provider groq
    ./venv/bin/python research/check_providers.py --models qwen/qwen3.6-27b
    ./venv/bin/python research/check_providers.py --list          # just list

Exit 0 if at least two same-provider models pass (enough for a model axis),
1 otherwise.
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from instructions import for_llm_agent                    # noqa: E402


def _from_llm_agent(*names):
    """Read literal constants straight out of app/llm_agent.py.

    Importing it would pull in playwright (and app/__init__ pulls in Flask and
    a DB engine) -- neither is needed to talk to an API, and requiring them
    would mean this pre-flight cannot run anywhere the collector cannot. Parsing
    the file keeps a single source of truth with none of that weight, and
    literal_eval means nothing in it executes.
    """
    import ast
    src = (ROOT / "app" / "llm_agent.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            t = node.targets[0]
            if isinstance(t, ast.Name) and t.id in names:
                found[t.id] = ast.literal_eval(node.value)
    missing = [n for n in names if n not in found]
    if missing:
        sys.exit(f"Could not read {', '.join(missing)} from app/llm_agent.py -- "
                 "it was renamed or is no longer a plain literal. Fix "
                 "_from_llm_agent() rather than copying the value here.")
    return tuple(found[n] for n in names)


ACTION_SPEC, PROVIDERS = _from_llm_agent("ACTION_SPEC", "PROVIDERS")

# Key env vars, in priority order per provider. CHARWEB_LLM_KEY is accepted
# everywhere as a last resort so an existing single-provider shell still works.
KEY_ENV = {
    "gemini":   ("GEMINI_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "groq":     ("GROQ_KEY", "GROQ_API_KEY"),
    # CHARWEB_LLM_KEY has always held the NVIDIA key in this project, so it is
    # accepted here rather than forcing a rename of an already-working export.
    "nvidia":   ("NVIDIA_KEY", "NVIDIA_API_KEY", "NVAPI_KEY", "CHARWEB_LLM_KEY"),
    "cerebras": ("CEREBRAS_KEY", "CEREBRAS_API_KEY"),
    "mistral":  ("MISTRAL_KEY", "MISTRAL_API_KEY"),
    "openrouter": ("OPENROUTER_KEY", "OPENROUTER_API_KEY"),
    "openai":   ("OPENAI_API_KEY",),
}

SWEEP = ["nvidia", "groq", "gemini", "cerebras"]

# Models worth testing per provider. Anything the provider lists that matches
# one of these prefixes is probed; --all probes everything listed instead.
CANDIDATE_HINTS = {
    "nvidia": ("qwen/", "openai/gpt-oss", "meta/llama", "mistralai/",
               "microsoft/phi", "google/gemma", "deepseek-ai/"),
    "groq":   ("qwen", "openai/gpt-oss", "llama", "gemma", "allam", "groq/"),
    "gemini": ("gemini-",),
    "cerebras": ("llama", "qwen", "gpt-oss"),
}

# Skip families that cannot serve as a browsing brain at all -- embeddings,
# rerankers, transcription, TTS, guard/moderation models, image models.
BAD_SUBSTR = ("embed", "rerank", "whisper", "tts", "guard", "moderation",
              "vision-only", "stable-diffusion", "flux", "clip", "nemoretriever",
              "ocr", "parse", "safety", "prompt-guard", "distil-whisper")

# One realistic page state, taken from Charweb's own explore page, so the model
# is asked the same kind of question it will get during collection.
ELEMENTS = [
    {"index": 0, "tag": "a", "type": "", "name": "", "href": "/index", "text": "Home"},
    {"index": 1, "tag": "a", "type": "", "name": "", "href": "/explore", "text": "Explore"},
    {"index": 2, "tag": "input", "type": "text", "name": "q", "href": "", "text": "Search"},
    {"index": 3, "tag": "textarea", "type": "", "name": "post", "href": "", "text": "Say something"},
    {"index": 4, "tag": "button", "type": "submit", "name": "", "href": "", "text": "Submit"},
    {"index": 5, "tag": "a", "type": "", "name": "", "href": "/user/ilv", "text": "Profile"},
    {"index": 6, "tag": "a", "type": "", "name": "", "href": "/logout", "text": "Logout"},
]
VALID_ACTIONS = {"click", "type", "scroll", "goto", "wait", "done"}


def build_messages(condition):
    system = for_llm_agent(condition) + "\n\n" + ACTION_SPEC
    listing = "\n".join(
        f'[{e["index"]}] <{e["tag"]}{"/"+e["type"] if e["type"] else ""}> '
        f'{e["text"] or e["name"] or e["href"]}'
        for e in ELEMENTS)
    user = (f"Current page: https://charweb.net/explore\n\nInteractive elements:\n"
            f"{listing}\n\nRecent actions: none\n\n"
            "What is your next action? Reply with the JSON object only.")
    return [{"role": "system", "content": system},
            {"role": "user", "content": user}]


def parse_action(raw):
    """Same leniency llm_agent uses: find the first {...} block and load it.
    Returns (ok, reason)."""
    if not raw or not raw.strip():
        return False, "empty reply"
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return False, "no JSON object in reply"
    try:
        obj = json.loads(m.group(0))
    except Exception:
        return False, "JSON did not parse"
    if not isinstance(obj, dict):
        return False, "JSON is not an object"
    act = obj.get("action")
    if act not in VALID_ACTIONS:
        return False, f"action={act!r} not in schema"
    if act in ("click", "type"):
        idx = obj.get("index")
        if not isinstance(idx, int) or not 0 <= idx < len(ELEMENTS):
            return False, f"{act} with out-of-range index {idx!r}"
    return True, act


def classify(err):
    """Permanent vs transient, matching llm_agent's own rule, plus the
    provider-defect case that motivated this script."""
    msg = str(err)
    low = msg.lower()
    if "tool_use_failed" in low or "tool choice is none" in low:
        return "DEFECT", "provider tool_use_failed (Groq bug)"
    if "404" in msg or "410" in msg or "does not exist" in low or "decommission" in low:
        return "DEAD", "model not available (404/410)"
    if "401" in msg or "403" in msg or "invalid api key" in low or "unauthorized" in low:
        return "AUTH", "key rejected"
    if "429" in msg or "rate limit" in low or "resource_exhausted" in low:
        return "RATE", "rate limited"
    if any(s in low for s in ("timeout", "timed out", "connection", "503", "502",
                             "500", "unavailable", "overloaded")):
        return "TRANSIENT", "backend busy/unreachable"
    if "400" in msg:
        return "BADREQ", msg[:90]
    return "ERROR", msg[:90]


def client_for(provider, key):
    from openai import OpenAI
    return OpenAI(base_url=PROVIDERS[provider], api_key=key,
                  timeout=60, max_retries=0)


def list_models(client):
    try:
        return sorted(m.id for m in client.models.list().data)
    except Exception as e:
        print(f"    (could not list models: {str(e)[:110]})")
        return []


def wanted(provider, model_id, probe_all):
    low = model_id.lower()
    if any(b in low for b in BAD_SUBSTR):
        return False
    if probe_all:
        return True
    hints = CANDIDATE_HINTS.get(provider, ())
    return any(low.startswith(h) or h in low for h in hints)


def probe(client, model, condition, trials, verbose):
    """Returns dict with n_ok, latencies, first failure reason."""
    ok, lat, reason, status = 0, [], "", "OK"
    for t in range(trials):
        t0 = time.time()
        try:
            r = client.chat.completions.create(
                model=model, messages=build_messages(condition),
                temperature=0.7, max_tokens=300)
            dt = time.time() - t0
            raw = (r.choices[0].message.content or "")
            good, why = parse_action(raw)
            if good:
                ok += 1
                lat.append(dt)
            else:
                status = "SHAPE"
                reason = reason or why
                if verbose:
                    print(f"      raw: {raw.strip()[:120]!r}")
        except Exception as e:
            kind, why = classify(e)
            reason = reason or why
            status = kind
            if kind in ("DEAD", "AUTH", "DEFECT"):
                break              # retrying cannot help
            time.sleep(1.5)
    if ok == trials:
        status = "OK"
    elif ok:
        status = "FLAKY"
    return {"ok": ok, "n": trials, "lat": lat, "reason": reason, "status": status}


def main():
    ap = argparse.ArgumentParser(description="Validate every model on every key")
    ap.add_argument("--provider", default=None,
                    help="comma-separated subset (default: all four with keys)")
    ap.add_argument("--models", default=None,
                    help="comma-separated model ids to probe instead of "
                         "auto-selecting from the provider's catalogue")
    ap.add_argument("--trials", type=int, default=3,
                    help="probes per model. A model that passes 1/3 is unusable.")
    ap.add_argument("--condition", default="free_explore",
                    help="which instruction condition's prompt to probe with")
    ap.add_argument("--all", action="store_true",
                    help="probe every chat model the provider lists, not just "
                         "the candidate families")
    ap.add_argument("--list", action="store_true",
                    help="list available models and exit, without probing")
    ap.add_argument("--verbose", action="store_true",
                    help="print the raw reply when a model fails the shape check")
    ap.add_argument("-o", "--out", default="provider_check.csv")
    args = ap.parse_args()

    providers = ([p.strip() for p in args.provider.split(",")]
                 if args.provider else SWEEP)

    keys, missing = {}, []
    for p in providers:
        k = next((os.environ[e] for e in KEY_ENV.get(p, ()) if os.environ.get(e)), None)
        if k:
            keys[p] = k
        else:
            missing.append(p)
    if missing:
        print("No key found for: " + ", ".join(missing))
        for p in missing:
            print(f"    export {KEY_ENV.get(p, ('?',))[0]}=...")
        print()
    if not keys:
        sys.exit("No provider keys set -- nothing to check.")

    rows = []
    for p, key in keys.items():
        print("=" * 74)
        print(f"{p}   base_url={PROVIDERS[p]}   key={key[:6]}...{key[-4:]} "
              f"(len {len(key)})")
        print("=" * 74)
        # A pasted placeholder is the single most common cause of a whole
        # provider "failing": every call comes back 403 and the key looks fine
        # in the log because it is never printed.
        if len(key) < 20 or key.startswith("<") or key.endswith("..."):
            print("  SKIP: that does not look like a real key (too short or a "
                  "placeholder). Re-export it and re-run.")
            continue
        cl = client_for(p, key)
        catalogue = list_models(cl)
        if args.list:
            for m in catalogue:
                print(f"    {m}")
            continue
        if args.models:
            targets = [m.strip() for m in args.models.split(",") if m.strip()]
        else:
            targets = [m for m in catalogue if wanted(p, m, args.all)]
            if not targets and catalogue:
                print("  (no candidate families matched; falling back to --all)")
                targets = [m for m in catalogue if wanted(p, m, True)]
        if not targets:
            print("  no models to probe.")
            continue
        print(f"  probing {len(targets)} model(s) x {args.trials} trial(s)\n")
        print(f"  {'model':<44}{'pass':>7}{'med s':>8}  status")
        for m in targets:
            r = probe(cl, m, args.condition, args.trials, args.verbose)
            med = (sorted(r["lat"])[len(r["lat"]) // 2] if r["lat"] else float("nan"))
            note = "" if r["status"] == "OK" else f"  {r['reason']}"
            print(f"  {m[:43]:<44}{r['ok']}/{r['n']:<5}{med:>8.1f}  "
                  f"{r['status']}{note}", flush=True)
            rows.append({"provider": p, "model": m, "ok": r["ok"], "n": r["n"],
                         "median_s": f"{med:.2f}" if r["lat"] else "",
                         "status": r["status"], "reason": r["reason"]})
        print()

    if args.list:
        return 0

    import csv
    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["provider", "model", "ok", "n",
                                           "median_s", "status", "reason"])
        w.writeheader()
        w.writerows(rows)

    usable = [r for r in rows if r["status"] == "OK"]
    print("=" * 74)
    print(f"USABLE: {len(usable)} of {len(rows)} probed  ->  {args.out}")
    print("=" * 74)
    by_prov = {}
    for r in usable:
        by_prov.setdefault(r["provider"], []).append(r)
    for p, rs in sorted(by_prov.items(), key=lambda kv: -len(kv[1])):
        rs.sort(key=lambda r: float(r["median_s"] or 999))
        print(f"\n  {p}  ({len(rs)} usable)")
        for r in rs:
            print(f"      {r['model']:<44}{r['median_s']:>7}s")

    # The model axis must come from ONE provider. Timing features are the
    # harness axis's strongest signal, so two models on two providers would
    # compare infrastructure as much as models.
    best = max(by_prov.items(), key=lambda kv: len(kv[1]), default=(None, []))
    if best[0] and len(best[1]) >= 2:
        pick = [r["model"] for r in best[1][:2]]
        print(f"\nModel axis (same provider, no infra confound): {best[0]}")
        print(f"  --llm-models \"{','.join(pick)}\"")
        print("\nLaunch:")
        print(f"  export CHARWEB_LLM_PROVIDER={best[0]}")
        print(f"  export CHARWEB_LLM_KEY=$" + KEY_ENV[best[0]][0])
        print("  nohup ./venv/bin/python research/run_interleaved.py \\")
        print("    --rounds 3 --include-llm --no-scripted --url https://charweb.net \\")
        print(f"    --llm-models \"{','.join(pick)}\" \\")
        print("    --session-timeout 300 --sleep 8 \\")
        print("    --instruction free_explore,checklist,targeted_search,"
              "impossible_goal,single_action \\")
        print("    > /srv/charweb/models2.log 2>&1 &")
        return 0

    print("\nNo single provider has 2+ usable models -- the model axis cannot be "
          "built without a provider confound. Add a key or use --all to probe "
          "the full catalogue.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
