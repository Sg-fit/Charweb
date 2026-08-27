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
                 "--url", url, "--username", "ilv", "--headless"],
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
    ap.add_argument("--instruction", default="free_explore",
                    help="single condition, or a comma-separated list to rotate "
                         "through (each becomes its own arm, so conditions are "
                         "interleaved in time exactly like harnesses are)")
    ap.add_argument("--skip-corpus-check", action="store_true",
                    help="skip the targeted_search/impossible_goal corpus check")
    ap.add_argument("--session-timeout", type=int, default=900,
                    help="kill a session after this many seconds. Lower it when "
                         "a large model queues on a free tier: a stalled session "
                         "otherwise holds the whole rotation for the full window")
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
    conditions = [c.strip() for c in args.instruction.split(",") if c.strip()]

    # targeted_search and impossible_goal depend on what is in the post corpus,
    # and both degrade silently if that changes. Check before burning a batch.
    # Best-effort: this only works where the DB is reachable (i.e. on the
    # server), so a failure to import is a skip, not an error.
    if not args.skip_corpus_check and (
            {"targeted_search", "impossible_goal"} & set(conditions)):
        checker = ROOT / "research" / "check_conditions.py"
        r = subprocess.run([sys.executable, str(checker)], cwd=str(ROOT))
        if r.returncode == 1:
            sys.exit("\n[interleaved] Refusing to collect: a condition's corpus "
                     "assumption no longer holds (see above). Re-run with "
                     "--skip-corpus-check only if you know why that is fine.")
        if r.returncode not in (0, 1):
            print("[interleaved] corpus check could not run (no DB here?) -- "
                  "continuing without it.")

    arms = build_arms(args.url, args.include_llm,
                      [m.strip() for m in args.llm_models.split(",") if m.strip()])
    # One arm per (harness, condition) pair, so instruction conditions are
    # interleaved in time too. Collecting conditions in blocks would rebuild
    # exactly the confound the interleaving exists to remove -- on a new axis.
    if len(conditions) > 1:
        arms = [(f"{label}|{cond}", argv, {**extra, "CHARWEB_INSTRUCTION": cond})
                for label, argv, extra in arms for cond in conditions]
    if args.only:
        want = {s.strip() for s in args.only.split(",")}
        arms = [a for a in arms if a[0] in want]
    if not arms:
        sys.exit("no arms selected")

    if args.include_llm:
        missing = []
        if not (os.environ.get("CHARWEB_LLM_KEY") or os.environ.get("OPENAI_API_KEY")):
            missing.append("CHARWEB_LLM_KEY")
        if not os.environ.get("CHARWEB_LLM_PROVIDER"):
            missing.append("CHARWEB_LLM_PROVIDER  (e.g. nvidia)")
        if missing:
            # Checked here rather than discovered one dead session at a time:
            # an unset key makes every LLM arm exit instantly, which silently
            # turns an interleaved run back into a scripted-only one.
            sys.exit("--include-llm needs these set in THIS shell before "
                     "launching:\n  " + "\n  ".join(f"export {m}" for m in missing) +
                     "\n\nNote they must be exported in the same shell as the "
                     "nohup command -- a previous ssh session's exports are gone.")

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
    # Per-arm failure counts. A globally-reset counter never catches one broken
    # arm among healthy ones -- the good arms keep clearing it while the broken
    # one burns every round.
    arm_fail = {label: 0 for label, _, _ in arms}
    disabled = set()
    for rnd in range(args.rounds):
        order = [a for a in arms if a[0] not in disabled]
        if not order:
            print("\n[interleaved] ABORTING: every arm has been disabled.")
            fh.close()
            sys.exit(1)
        if args.shuffle:
            # Rotating in a FIXED order would leave arm position inside each
            # round correlated with time-within-round; shuffling each round
            # removes that residual pattern too.
            rng.shuffle(order)
        for label, argv, extra in order:
            seq += 1
            env = dict(os.environ)
            # Without this, a child's prints sit in a 4-8KB stdout buffer when
            # the batch is redirected to a log file, so a slow session looks
            # identical to a hung one -- you cannot tell progress from a stall
            # until the process exits and flushes.
            env["PYTHONUNBUFFERED"] = "1"
            env["CHARWEB_RUN_ID"] = run_id
            env["CHARWEB_INSTRUCTION"] = conditions[0]
            env.update(extra)   # per-arm condition, when rotating, wins here
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
                                   timeout=args.session_timeout)
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
            # Exit 4 = the collector reached the site fine but its model backend
            # is dead (retired model, bad key). Those sessions are recorded with
            # a real label and almost no behaviour, so one is already one too
            # many -- disable the arm on the first occurrence, not the second.
            if rc == 4:
                if label not in disabled:
                    disabled.add(label)
                    print(f"    DISABLING arm '{label}': model backend is not "
                          f"usable (retired model or rejected key). Its sessions "
                          f"would be label-only and would corrupt the model axis.")
                time.sleep(args.sleep)
                continue

            if rc != 0 and dt < 10:
                arm_fail[label] += 1
                if arm_fail[label] >= 2 and label not in disabled:
                    disabled.add(label)
                    print(f"    DISABLING arm '{label}': failed instantly twice. "
                          f"Its config is broken (missing key/dependency), not "
                          f"unlucky. Remaining arms continue.")
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
                arm_fail[label] = 0
            time.sleep(args.sleep)

    fh.close()
    print(f"\n[interleaved] done. run_id={run_id}")
    if disabled:
        print(f"[interleaved] WARNING: {len(disabled)} arm(s) were disabled and "
              f"contributed NOTHING: {', '.join(sorted(disabled))}")
        print("[interleaved] This batch is not interleaved across those arms. "
              "Fix their config and re-run before using it as a confound control.")
    print(f"[interleaved] manifest -> {manifest}")
    print("\nNext -- export BOTH batches (the --run-id filter is what keeps this")
    print("batch separate; without it you re-export the old blocked data):")
    print("  set -a; . /etc/charweb.env; set +a")
    print("  ./venv/bin/python research/export_research_features.py -o blocked.csv")
    print("  ./venv/bin/python research/export_research_features.py "
          f"-o interleaved.csv --run-id {run_id}")
    print("\nThen the confound test -- this is the comparison that matters:")
    print("  ./venv/bin/python research/m3_confound_check.py blocked.csv interleaved.csv")
    print("\nAnd confirm the finding still holds on the new batch alone:")
    print("  ./venv/bin/python research/m3_analysis.py interleaved.csv")


if __name__ == "__main__":
    main()
