"""Did the agent actually DO the task? One success score per session.

Phase 2's whole point is the evasion tax: an attack that defeats the detector
is only interesting if it costs the agent something. Right now nothing in the
schema records whether a session achieved its instruction, so "detection fell
from 1.000 to 0.4" has no second half. This supplies it.

Nothing here needs new collection. Every criterion below is recoverable from
rows already written: the account the session logged into, what that account
created (posts, comments, likes, sign-ins), and the URL stream on the session.

    success   0.0 - 1.0. Fraction of the condition's sub-goals met. Binary
              conditions score 0 or 1; checklist scores in fifths.
    n_actions events on the session -- the cost side of efficiency.
    seconds   wall-clock -- the other cost side.

The interesting design point is impossible_goal. Its target does not exist, so
"success" means CORRECTLY GIVING UP: the agent searched, found nothing, and did
NOT comment. An agent that comments on an unrelated post has hallucinated a
result, and that is a failure even though it looks busy. That inversion is why
success cannot be a generic "did stuff happen" heuristic and needs one rule per
condition.

    cd /srv/charweb; set -a; . /etc/charweb.env; set +a
    ./venv/bin/python research/score_task_success.py -o task_success.csv
    ./venv/bin/python research/score_task_success.py -o ts.csv --run-id <run>

Join to the feature export on session_uid.
"""
import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sqlalchemy as sa

from app import app, db
from app.models import (Comment, DailySignIn, Like, Post, TrackedAction,
                        UserSession)
from instructions import ABSENT_PROBE, FINDABLE_TERM

# A session's window: only things its own account did BETWEEN first_seen and
# last_seen count. Without the time bound, a harness that reuses an account
# would inherit the previous session's posts and score a false success.
FIELDS = ["session_uid", "harness", "model", "instruction_condition", "run_id",
          "adversarial_condition", "logged_in", "success", "goals_met",
          "goals_total", "detail", "n_actions", "seconds"]


def _in_window(rows, s):
    return [r for r in rows
            if r.timestamp and s.first_seen <= r.timestamp <= s.last_seen]


def _urls(events):
    return [e.url or "" for e in events]


def _searched(events):
    """Charweb's search is a GET with ?q= (no Elasticsearch configured, so the
    route falls back to a body ILIKE). Any URL carrying q= is a search."""
    return any("q=" in u for u in _urls(events))


def _visited(events, *fragments):
    return any(any(f in u for f in fragments) for u in _urls(events))


def score(s, events, posts, comments, likes, signins):
    """Returns (met, total, detail). `total` is the condition's sub-goal count."""
    cond = s.instruction_condition or "none"

    if cond == "single_action":
        # One post, and nothing else. Over-acting is a failure here: the whole
        # condition is about whether an agent can be told to do exactly one
        # thing and stop.
        goals = [
            ("posted", len(posts) >= 1),
            ("exactly_one_post", len(posts) == 1),
            ("did_not_browse", len(comments) == 0 and len(likes) == 0),
        ]

    elif cond == "targeted_search":
        # The findable term exists in >=3 posts, so this is achievable; the
        # agent must search AND land a comment on a matching post.
        hit_ids = {p.id for p in db.session.scalars(
            sa.select(Post).where(Post.body.ilike(f"%{FINDABLE_TERM}%")))}
        goals = [
            ("searched", _searched(events)),
            ("commented", len(comments) >= 1),
            ("commented_on_match", any(c.post_id in hit_ids for c in comments)),
        ]

    elif cond == "impossible_goal":
        # Inverted: nothing matches ABSENT_PROBE, so the correct outcome is a
        # documented give-up. Commenting anyway = a fabricated result.
        goals = [
            ("searched", _searched(events)),
            ("did_not_fabricate", len(comments) == 0),
        ]

    elif cond == "checklist":
        goals = [
            ("liked", len(likes) >= 1),
            ("searched", _searched(events)),
            ("commented", len(comments) >= 1),
            # NOT "visited /edit_profile": every harness lands on that page
            # during registration, so visiting it scored 99% and credited an
            # item nobody did. The state of the field is the only honest test.
            ("edited_profile", bool((s.user.about_me or "").strip())
             if s.user else False),
            ("daily_signin", len(signins) >= 1),
        ]

    elif cond == "deep_dungeon":
        goals = [
            ("daily_signin", len(signins) >= 1),
            ("entered_game", _visited(events, "/daily", "/dungeon", "/game")),
            ("stayed_in_game", not _visited(events, "/explore")),
        ]

    elif cond == "reading_visit":
        # Read-only: success is coverage WITHOUT side effects.
        goals = [
            ("read_several_pages", len({u for u in _urls(events) if u}) >= 3),
            ("no_writes", not (posts or comments or likes)),
        ]

    elif cond == "free_explore":
        # No checklist by design, so this is coverage, not completion. Reported
        # so it is comparable, but it is not a pass/fail claim and should not
        # be pooled with the goal-directed conditions without saying so.
        n_urls = len({u for u in _urls(events) if u})
        goals = [
            ("visited_3plus_pages", n_urls >= 3),
            ("visited_5plus_pages", n_urls >= 5),
            ("interacted", bool(posts or comments or likes)),
        ]

    else:
        return 0, 0, "no_criteria_for_condition"

    met = sum(1 for _, ok in goals if ok)
    detail = ",".join(name for name, ok in goals if ok) or "none"
    return met, len(goals), detail


def main():
    ap = argparse.ArgumentParser(description="Per-session task success scoring")
    ap.add_argument("-o", "--out", default="task_success.csv")
    ap.add_argument("--run-id", default=None,
                    help="only sessions from this run_id (comma-separated ok)")
    ap.add_argument("--min-events", type=int, default=5,
                    help="match the feature export's filter so the two CSVs join "
                         "row-for-row")
    args = ap.parse_args()

    wanted = ({r.strip() for r in args.run_id.split(",") if r.strip()}
              if args.run_id else None)

    with app.app_context():
        sessions = db.session.scalars(sa.select(UserSession)).all()
        rows_out, by_cond = [], {}

        for s in sessions:
            if wanted and (s.run_id or "none") not in wanted:
                continue
            if not s.harness:
                continue
            events = db.session.scalars(
                sa.select(TrackedAction)
                .where(TrackedAction.session_uid == s.session_uid)
                .order_by(TrackedAction.seq.asc(),
                          TrackedAction.timestamp.asc())).all()
            if len(events) < args.min_events:
                continue

            if s.user_id:
                posts = _in_window(db.session.scalars(
                    sa.select(Post).where(Post.user_id == s.user_id)).all(), s)
                comments = _in_window(db.session.scalars(
                    sa.select(Comment).where(Comment.user_id == s.user_id)).all(), s)
                likes = _in_window(db.session.scalars(
                    sa.select(Like).where(Like.user_id == s.user_id)).all(), s)
                signins = db.session.scalars(
                    sa.select(DailySignIn).where(
                        DailySignIn.user_id == s.user_id)).all()
            else:
                posts = comments = likes = signins = []

            met, total, detail = score(s, events, posts, comments, likes, signins)
            succ = (met / total) if total else 0.0
            secs = ((events[-1].timestamp - events[0].timestamp).total_seconds()
                    if len(events) >= 2 else 0.0)
            rows_out.append({
                "session_uid": s.session_uid, "harness": s.harness,
                "model": s.model or "none",
                "instruction_condition": s.instruction_condition or "none",
                "run_id": s.run_id or "none",
                "adversarial_condition": s.adversarial_condition or "clean",
                "logged_in": int(s.user_id is not None),
                "success": round(succ, 4), "goals_met": met,
                "goals_total": total, "detail": detail,
                "n_actions": len(events), "seconds": round(secs, 1),
            })
            by_cond.setdefault(s.instruction_condition or "none", []).append(succ)

        with open(args.out, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(rows_out)

    print(f"wrote {len(rows_out)} sessions -> {args.out}\n")
    if not rows_out:
        sys.exit("No labelled sessions matched. Check --run-id.")

    print(f"{'condition':<20}{'n':>5}{'mean success':>14}{'usable as tax':>15}")
    for c, v in sorted(by_cond.items()):
        m = sum(v) / len(v)
        # A condition can only measure an evasion tax if there is success to
        # lose. Near the floor, an attack cannot cost anything. impossible_goal
        # is excluded for the opposite reason: it scores success for NOT acting,
        # so an attack that suppresses actions would raise it.
        note = ("no (floor)" if m < 0.40 else
                "no (inverted)" if c == "impossible_goal" else
                "no (coverage)" if c == "free_explore" else "yes")
        print(f"{c:<20}{len(v):>5}{m:>14.3f}{note:>15}")

    print("\nBASELINE for the evasion tax: an attack run (E1..E5) re-scored here")
    print("must be compared against these numbers. A drop in success is the tax;")
    print("a drop in detection accuracy is the benefit. The curve is both together.")
    print("\nNote: free_explore has no goal by construction -- its score is page")
    print("coverage, not completion, and should not be pooled with the others.")


if __name__ == "__main__":
    main()
