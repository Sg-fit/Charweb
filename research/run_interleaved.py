#!/usr/bin/env python3
"""Interleaved collection -- removes the batch/temporal confound.

The problem this fixes: every harness so far was collected in one contiguous
block (15 of A, then 15 of B, then 15 of C). That makes *when* a session was
recorded partly interchangeable with *what recorded it*, so a classifier could
in principle be reading the clock rather than the agent. Nothing in the
existing data can separate the two.

The fix is scheduling, not analysis: rotate A, B, C, A, B, C... so every
harness is spread across the whole collection window and shares the same
network conditions, server load and time of day. If accuracy holds on data
collected this way, the confound is dead.

Each session is run as its own subprocess with a clean environment, exactly
as the normal collectors do, and every session is tagged with the same
--run-id so the analysis can select just this batch.

A manifest CSV records the order and wall-clock time of every session, so the
follow-up check ("does collection time predict the label?") is possible.

    python research/run_interleaved.py --rounds 5
    python research/run_interleaved.py --rounds 5 --include-llm
    python research/run_interleaved.py --rounds 5 --only scripted_plain,scripted_noisy
"""
import argparse
import csv
import os
import random
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Each entry: label -> (argv, extra env). The label is only for the manifest;
# the harness/model labels recorded on the server are set by the scripts
# themselves, exactly as in a normal run.
def build_arms(url, include_llm, llm_models):
    arms = []
    for profile in ("plain", "noisy", "humanlike"):
        arms.append((
            f"scripted_{profile}",
            [sys.executable, str(ROOT / "app" / "scripted_agent.py"),
             "--profile", profile, "--n", "1", "--url", url],
            {},
        ))
    if include_llm:
        for model in llm_models:
            arms.append((
                f"llm:{model}",
                [sys.executable, str(ROOT / "app" / "llm_agent.py"),
                 "--url", url, "--username", "ilv"],
                {"CHARWEB_LLM_MODEL": model},
            ))
    return arms


def main():
    ap = argparse.ArgumentParser(description="Interleaved collection driver")
    ap.add_argument("--rounds", type=int, default=5,
                    help="sessions per harness (one per harness per round)")
    ap.add_argument("--url", default="https://charweb.net")
    ap.add_argument("--run-id", default=None,
                    help="defaults to interleaved_<UTC timestamp>")
    ap.add_argument("--instruction", default="free_explore")
    ap.add_argument("--sleep", type=float, default=5.0,
                    help="pause between sessions")
    ap.add_argument("--include-llm", action="store_true",
                    help="also rotate the LLM harness (needs CHARWEB_LLM_KEY)")
    ap.add_argument("--llm-models",
                    default="openai/gpt-oss-20b,meta/llama-3.1-8b-instruct",
                    help="comma-separated models for the LLM arm")
    ap.add_argument("--only", default=None,
                    help="comma-separated subset of arm labels to run")
    ap.add_argument("--manifest", default="interleaved_manifest.csv")
    ap.add_argument("--shuffle", action="store_true", default=True,
                    help="shuffle arm order within each round (default on)")
    ap.add_argument("--no-shuffle", dest="shuffle", action="store_false")
    args = ap.parse_args()

    run_id = args.run_id or f"interleaved_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}"
    arms = build_arms(args.url, args.include_llm,
                      [m.strip() for m in args.llm_models.split(",") if m.strip()])
    if args.only:
        want = {s.strip() for s in args.only.split(",")}
        arms = [a for a in arms if a[0] in want]
    if not arms:
        sys.exit("no arms selected")

    if args.include_llm and not (os.environ.get("CHARWEB_LLM_KEY")
                                 or os.environ.get("OPENAI_API_KEY")):
        sys.exit("--include-llm needs CHARWEB_LLM_KEY (or OPENAI_API_KEY) set")

    print(f"[interleaved] run_id={run_id}")
    print(f"[interleaved] {len(arms)} arms x {args.rounds} rounds = "
          f"{len(arms) * args.rounds} sessions")
    for label, _, _ in arms:
        print(f"             - {label}")

    rng = random.Random(0)
    manifest = Path(args.manifest)
    new_file = not manifest.exists()
    fh = manifest.open("a", newline="", encoding="utf-8")
    w = csv.writer(fh)
    if new_file:
        w.writerow(["run_id", "seq", "round", "arm", "started_utc",
                    "finished_utc", "seconds", "returncode"])

    seq = 0
    consecutive_fail = 0
    for rnd in range(args.rounds):
        order = list(arms)
        if args.shuffle:
            # Rotating in a FIXED order would leave arm position inside each
            # round correlated with time-within-round; shuffling each round
            # removes that residual pattern too.
            rng.shuffle(order)
        for label, argv, extra in order:
            seq += 1
            env = dict(os.environ)
            env["CHARWEB_RUN_ID"] = run_id
            env["CHARWEB_INSTRUCTION"] = args.instruction
            env.update(extra)
            # The collectors force their own harness/model labels, so nothing
            # stale from this shell can mislabel a session.
            env.pop("CHARWEB_HARNESS", None)
            if "CHARWEB_LLM_MODEL" not in extra:
                env.pop("CHARWEB_MODEL", None)

            started = datetime.now(timezone.utc)
            t0 = time.time()
            print(f"[{seq}/{len(arms)*args.rounds}] round {rnd+1} :: {label}",
                  flush=True)
            try:
                p = subprocess.run(argv, env=env, cwd=str(ROOT),
                                   timeout=900)
                rc = p.returncode
            except subprocess.TimeoutExpired:
                rc = -1
                print("    (timeout)")
            except Exception as e:
                rc = -2
                print(f"    (error) {str(e)[:120]}")
            dt = time.time() - t0
            w.writerow([run_id, seq, rnd + 1, label,
                        started.isoformat(),
                        datetime.now(timezone.utc).isoformat(),
                        f"{dt:.1f}", rc])
            fh.flush()
            if rc == 3:
                print("    daily quota reached -- stopping batch")
                fh.close()
                return

            # A collector that exits instantly and non-zero is broken (a missing
            # dependency, a dead site), not unlucky. Without this guard the whole
            # batch "completes" in seconds having collected nothing, which looks
            # like a finished run until the export comes back empty.
            if rc != 0 and dt < 10:
                consecutive_fail += 1
                if consecutive_fail >= 3:
                    print(f"\n[interleaved] ABORTING: {consecutive_fail} collectors "
                          f"in a row failed in under 10s (last exit code {rc}).")
                    print("[interleaved] Fix the collector, then re-run. Nothing "
                          "useful was collected, so no partial batch to clean up.")
                    fh.close()
                    sys.exit(1)
            else:
                consecutive_fail = 0
            time.sleep(args.sleep)

    fh.close()
    print(f"\n[interleaved] done. run_id={run_id}")
    print(f"[interleaved] manifest -> {manifest}")
    print("\nNext: re-export and score ONLY this batch, then compare with the "
          "blocked result:")
    print(f"  ./venv/bin/python research/export_research_features.py -o interleaved.csv")
    print(f"  ./venv/bin/python research/m3_analysis.py interleaved.csv")


if __name__ == "__main__":
    main()
