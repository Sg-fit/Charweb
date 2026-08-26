#!/usr/bin/env python3
"""Batch grid runner for Charweb AI-only collection.

Sweeps the LLM harness across models x instruction conditions, N sessions per
cell, launching EACH session as its own subprocess with a clean, explicit
environment -- so a stale $env: from the parent shell can never mislabel a
session. Optionally runs grok as the scripted control cell.

Lives at the repo ROOT (not app/), so it doesn't hit the app/email.py shadow
and doesn't import the Flask app -- it only launches the existing harnesses.

Usage (PowerShell), set your Groq key once:
    cd C:\\Users\\charl\\ProjectStart
    $env:CHARWEB_LLM_KEY="gsk_your_key"
    python run_grid.py --per-cell 10          # validate small first
    python run_grid.py --per-cell 40 --include-grok   # full M2 batch

Every session in a run shares one run_id (printed at start + end); filter your
analysis to that run_id so this batch is cleanly separated from pilot data.
"""
import argparse
import datetime
import os
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.abspath(__file__))


def slug(s):
    return "".join(c if c.isalnum() else "-" for c in s)[:24]


def run_session(env_extra, argv, label):
    env = os.environ.copy()
    env.update(env_extra)
    print(f"    -> {label}", flush=True)
    try:
        proc = subprocess.run([sys.executable] + argv, cwd=REPO, env=env, timeout=900)
        return proc.returncode           # 3 => llm_agent hit a daily quota
    except subprocess.TimeoutExpired:
        print(f"       (timeout) {label}", flush=True)
    except Exception as e:
        print(f"       (error) {label}: {e}", flush=True)
    return None


def main():
    ap = argparse.ArgumentParser(description="Charweb collection grid runner")
    ap.add_argument("--per-cell", type=int, default=40,
                    help="LLM sessions per (model x instruction) cell")
    ap.add_argument("--url", default="https://charweb.net")
    ap.add_argument("--provider", default="groq")
    ap.add_argument("--models",
                    default="openai/gpt-oss-120b,qwen/qwen3.6-27b",
                    help="comma-separated model ids (the model axis)")
    ap.add_argument("--instructions", default="free_explore,checklist",
                    help="comma-separated instruction conditions")
    ap.add_argument("--sleep", type=float, default=8.0,
                    help="seconds to wait between sessions (rate-limit margin)")
    ap.add_argument("--run-id", default=None,
                    help="tag for this batch (default: m2_<timestamp>)")
    ap.add_argument("--headed", action="store_true",
                    help="show the browser (default: headless)")
    ap.add_argument("--include-grok", action="store_true",
                    help="also run grok.py as the scripted control cell")
    ap.add_argument("--grok-runs", type=int, default=3,
                    help="how many times to run grok (each loops levels x trials)")
    args = ap.parse_args()

    key = os.environ.get("CHARWEB_LLM_KEY")
    if not key:
        sys.exit("Set CHARWEB_LLM_KEY (your Groq key) in the shell first.")

    run_id = args.run_id or "m2_" + datetime.datetime.now().strftime("%Y%m%d_%H%M")
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    instrs = [i.strip() for i in args.instructions.split(",") if i.strip()]

    total = len(models) * len(instrs) * args.per_cell
    print(f"=== Grid run_id={run_id} ===")
    print(f"models={models}")
    print(f"instructions={instrs}  per-cell={args.per_cell}  "
          f"=> {total} LLM sessions" + ("  + grok control" if args.include_grok else ""))
    print(f"Filter analysis to run_id='{run_id}'.\n", flush=True)

    done = 0
    for model in models:
        for instr in instrs:
            print(f"[cell] llm_driven / {model} / {instr}  x{args.per_cell}", flush=True)
            for i in range(args.per_cell):
                uname = f"{slug(model)}-{instr}-{i:03d}"
                env_extra = {
                    "CHARWEB_LLM_PROVIDER": args.provider,
                    "CHARWEB_LLM_KEY": key,
                    "CHARWEB_LLM_MODEL": model,
                    "CHARWEB_INSTRUCTION": instr,
                    "CHARWEB_RUN_ID": run_id,
                    # clear anything stale so only this cell's labels apply
                    "CHARWEB_HARNESS": "",
                    "CHARWEB_ADV_CONDITION": "",
                    "CHARWEB_MIMICRY_TARGET": "",
                }
                argv = ["app/llm_agent.py", "--username", uname, "--url", args.url]
                if not args.headed:
                    argv.append("--headless")
                done += 1
                rc = run_session(env_extra, argv, f"[{done}/{total}] {model}/{instr}")
                if rc == 3:
                    print(f"\n[grid] Daily free-tier quota reached — stopping cleanly. "
                          f"Resume tomorrow with a new --run-id. (this batch: run_id={run_id})",
                          flush=True)
                    return
                time.sleep(args.sleep)

    if args.include_grok:
        for k in range(args.grok_runs):
            env_extra = {
                "CHARWEB_HARNESS": "playwright",
                "CHARWEB_MODEL": "none_scripted",
                "CHARWEB_INSTRUCTION": "scripted",
                "CHARWEB_RUN_ID": run_id,
            }
            run_session(env_extra, ["app/grok.py"],
                        f"grok control run {k + 1}/{args.grok_runs}")
            time.sleep(args.sleep)

    print(f"\n=== Done. run_id='{run_id}' ===", flush=True)


if __name__ == "__main__":
    main()
